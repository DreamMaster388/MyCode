"""文件类工具共享的路径解析与读写授权能力。

Read/Write/Edit/MultiEdit/Grep/Glob 都从这里取得:
- 统一的 project_root / working_dir 解析基准;
- 每次调用可变的策略来源(policy_provider), 以便用户批准目录立即生效;
- 读授权(authorize_read)与写授权(authorize_write)的严格判定;
- 外部路径的审批接入(access_guard)与 ONCE 调用内临时许可(AccessPermit)。

没有 access_guard 时保持严格 fail-closed; 有 guard 时才向用户请求审批。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
import os
from pathlib import Path
from typing import Optional

from ...core.approval import (
    ApprovalDecision,
    ApprovalRequest,
    approval_root,
)
from ...fs import (
    AccessKind,
    FsTarget,
    PathError,
    SandboxApprovalRequired,
    SandboxDenied,
    SandboxMode,
    SandboxPolicy,
    authorize_read,
    authorize_write,
    build_policy,
    is_within,
    resolve_path,
)
from ..errors import ToolErrorCode
from ..response import ToolResponse

# 与内核围栏的拒绝标记保持一致, 便于模型识别"这是策略拒绝"。
DENIAL_MARKER = "[sandbox: file access denied]"
# 审批拒绝使用单独的标记, 与"路径本身不可读/不可写"区分。
APPROVAL_DENIED_MARKER = "[sandbox: approval denied]"


@dataclass(frozen=True)
class AccessPermit:
    """一次工具调用内的临时访问许可(仅 ONCE 使用)。

    write_roots 用于覆盖写目标派生出的临时文件与 .backups 目录;
    read_roots 为读许可保留, 当前读工具没有派生路径。
    """

    read_roots: tuple[FsTarget, ...] = ()
    write_roots: tuple[FsTarget, ...] = ()

    def allows_read(self, target: FsTarget) -> bool:
        return any(is_within(root, target) for root in self.read_roots)

    def allows_write(self, target: FsTarget) -> bool:
        return any(is_within(root, target) for root in self.write_roots)


def _permit_scoped(run):
    """让一次性 AccessPermit 的生命周期严格限定在一次工具调用内。

    调用开始和结束都清空许可; 即使中途异常也不会把 ONCE 的放行带到下次。
    """
    @wraps(run)
    def wrapper(self, *args, **kwargs):
        self._permit = None
        try:
            return run(self, *args, **kwargs)
        finally:
            self._permit = None
    return wrapper


class _FsTool:
    """文件工具共享的路径解析与读写授权能力。

    - project_root: 工作区根(策略边界)。
    - working_dir : 相对路径的解析基准(默认 = project_root)。
    - policy      : 沙箱策略; 未提供时按 project_root 生成 workspace-write。
    - policy_provider: 可选的动态策略来源; 每次调用优先使用它, 使批准目录即时生效。
    - access_guard: 可选的 ModeGuard; 外部路径通过它请求审批。
    """

    project_root: Path
    working_dir: Path
    policy: SandboxPolicy

    def _init_fs(self, project_root=".", working_dir=None, policy=None,
                 policy_provider=None, access_guard=None) -> None:
        self.project_root = Path(project_root).resolve()
        self.working_dir = Path(working_dir).resolve() if working_dir else self.project_root
        if policy is None:
            policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(self.project_root))
        self.policy = policy
        # policy_provider 缺省时一直使用构造时的静态策略。
        self._policy_provider = policy_provider
        # access_guard 缺省时保持严格策略: 外部路径直接拒绝, 不询问。
        self._access_guard = access_guard
        # 当前调用内的一次性许可, _authorize_access 每次重新计算。
        self._permit: Optional[AccessPermit] = None

    def _current_policy(self) -> SandboxPolicy:
        """当前调用应使用的策略; provider 异常时回退到静态策略。"""
        provider = getattr(self, "_policy_provider", None)
        if provider is not None:
            try:
                return provider()
            except Exception:
                pass
        return self.policy

    def _resolve(self, path: str) -> FsTarget:
        """把调用方路径解析成稳定身份(相对 working_dir)。"""
        return resolve_path(path, self.working_dir)

    def _display_path(self, path: Path) -> str:
        """展示用路径: 工作区内用相对路径, 工作区外(批准目录)用原路径。"""
        try:
            return str(Path(path).relative_to(self.working_dir)).replace(os.sep, "/")
        except ValueError:
            return str(path)

    @staticmethod
    def _read_denied(exc: Exception) -> ToolResponse:
        """把严格读授权异常映射成统一的 ACCESS_DENIED 响应(无 guard 时)。"""
        if isinstance(exc, SandboxApprovalRequired):
            message = (
                f"{DENIAL_MARKER} 读目标 '{exc.target.display_path}' 超出可读根, "
                "需要用户批准; 当前不允许访问。"
            )
        else:
            message = f"{DENIAL_MARKER} {exc}"
        return ToolResponse.error(code=ToolErrorCode.ACCESS_DENIED, message=message)

    def _approval_denied(self, kind: AccessKind, target: FsTarget) -> ToolResponse:
        """审批拒绝: 明确的策略/用户拒绝, 不提示换写法绕过。"""
        action = "读取" if kind is AccessKind.READ else "写入"
        return ToolResponse.error(
            code=ToolErrorCode.ACCESS_DENIED,
            message=(
                f"{APPROVAL_DENIED_MARKER} 工具 '{self.name}' 的{action}请求未获批准: "
                f"'{target.display_path}'。不要换写法绕过。"
            ),
        )

    def _authorize_access(self, kind: AccessKind, target: FsTarget,
                          tool_name: str) -> Optional[ToolResponse]:
        """读取/写入的统一授权入口。

        - 没有 access_guard: 沿用 Step 6 的严格 authorize_read/write, fail-closed;
        - 有 access_guard: 交给 ModeGuard.request_access 决定是否询问;
          ALLOW/ONCE/ALWAYS 放行, DENY 返回 ACCESS_DENIED。
        """
        self._permit = None
        guard = getattr(self, "_access_guard", None)

        if guard is None:
            try:
                if kind is AccessKind.READ:
                    authorize_read(self._current_policy(), target)
                else:
                    authorize_write(self._current_policy(), target)
            except SandboxApprovalRequired as exc:
                return self._read_denied(exc)
            except SandboxDenied as exc:
                if kind is AccessKind.READ:
                    return self._read_denied(exc)
                return ToolResponse.error(
                    code=ToolErrorCode.ACCESS_DENIED, message=str(exc)
                )
            return None

        try:
            verdict = ApprovalDecision(guard.request_access(
                ApprovalRequest(kind=kind, target=target, tool_name=tool_name)
            ))
        except Exception:
            # 审批器/guard 异常按拒绝处理, 保证 fail-closed。
            return self._approval_denied(kind, target)

        if verdict is ApprovalDecision.DENY:
            return self._approval_denied(kind, target)

        # ONCE 的写许可覆盖目标父目录, 以放行原子写临时文件与 .backups。
        if kind is AccessKind.WRITE and verdict in (
            ApprovalDecision.ONCE, ApprovalDecision.ALWAYS
        ):
            self._permit = AccessPermit(write_roots=(approval_root(target),))
        elif kind is AccessKind.READ and verdict is ApprovalDecision.ONCE:
            self._permit = AccessPermit(read_roots=(target,))
        return None

    def _prepare_read(self, path: str):
        """解析并授权读目标; 返回 (full_path, error_response)。"""
        try:
            target = self._resolve(path)
        except PathError as exc:
            return None, ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message=str(exc)
            )
        err = self._authorize_access(AccessKind.READ, target, self.name)
        if err:
            return None, err
        return Path(target.target_key), None

    def _prepare_write(self, path: str):
        """解析并授权写目标; 返回 (full_path, error_response)。"""
        try:
            target = self._resolve(path)
        except PathError as exc:
            return None, ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM, message=str(exc)
            )
        err = self._authorize_access(AccessKind.WRITE, target, self.name)
        if err:
            return None, err
        return Path(target.target_key), None

    def _authorize_derived(self, path: Path) -> Optional[ToolResponse]:
        """对派生路径(临时文件/备份)做二次围栏。

        这些路径由已授权的目标派生而来, 但可能被预置软链劫持(如
        `foo.py.tmp -> /外部文件` 或 `.backups -> /外部目录`)。此处重新
        解析 realpath 身份并授权, 软链指向围栏外时被拒。放行返回 None。

        ONCE 写批准时, 目标父目录已被记入本次 AccessPermit, 派生路径
        因此可通过; 许可在下一次 _authorize_access 时重置。
        """
        try:
            target = resolve_path(str(path), self.working_dir)
            permit = getattr(self, "_permit", None)
            if permit is not None and permit.allows_write(target):
                return None
            authorize_write(self._current_policy(), target)
        except PathError as exc:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message=str(exc))
        except SandboxDenied as exc:
            return ToolResponse.error(code=ToolErrorCode.ACCESS_DENIED, message=str(exc))
        return None

    def _backup_file(self, full_path: Path):
        """备份文件; 备份路径受二次围栏保护。返回 (backup_path, error)。

        备份目录与备份文件均需落在可写根内, 且拒绝经软链逃逸到围栏外:
        任一步被拒即返回 error 且不产生任何落盘。
        """
        backup_dir = full_path.parent / ".backups"
        err = self._authorize_derived(backup_dir)
        if err:
            return None, err
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{full_path.name}.{timestamp}.bak"
        backup_path = backup_dir / backup_name

        err = self._authorize_derived(backup_path)
        if err:
            return None, err

        shutil.copy2(full_path, backup_path)
        return backup_path, None
