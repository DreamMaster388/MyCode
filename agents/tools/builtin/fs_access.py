"""文件类工具共享的路径解析与读写授权能力。

Read/Write/Edit/MultiEdit/Grep/Glob 都从这里取得:
- 统一的 project_root / working_dir 解析基准;
- 每次调用可变的策略来源(policy_provider), 以便用户批准目录立即生效;
- 读授权(authorize_read)与写授权(authorize_write)的拒绝映射。

审批接入前, 读根之外的访问按拒绝处理; 审批层接入后由子类/上层替换
_prepare_read / _prepare_write 的决策路径。
"""
from __future__ import annotations

import shutil
from datetime import datetime
import os
from pathlib import Path
from typing import Optional

from ...fs import (
    FsTarget,
    PathError,
    SandboxApprovalRequired,
    SandboxDenied,
    SandboxMode,
    SandboxPolicy,
    authorize_read,
    authorize_write,
    build_policy,
    resolve_path,
)
from ..errors import ToolErrorCode
from ..response import ToolResponse

# 与内核围栏的拒绝标记保持一致, 便于模型识别"这是策略拒绝"。
DENIAL_MARKER = "[sandbox: file access denied]"


class _FsTool:
    """文件工具共享的路径解析与写授权能力。

    - project_root: 工作区根(策略边界)。
    - working_dir : 相对路径的解析基准(默认 = project_root)。
    - policy      : 沙箱策略; 未提供时按 project_root 生成 workspace-write。
    - policy_provider: 可选的动态策略来源; 每次调用优先使用它, 使批准目录即时生效。
    """

    project_root: Path
    working_dir: Path
    policy: SandboxPolicy

    def _init_fs(self, project_root=".", working_dir=None, policy=None,
                 policy_provider=None) -> None:
        self.project_root = Path(project_root).resolve()
        self.working_dir = Path(working_dir).resolve() if working_dir else self.project_root
        if policy is None:
            policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(self.project_root))
        self.policy = policy
        # policy_provider 缺省时一直使用构造时的静态策略。
        self._policy_provider = policy_provider

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
        """把读授权异常映射成统一的 ACCESS_DENIED 响应。"""
        if isinstance(exc, SandboxApprovalRequired):
            message = (
                f"{DENIAL_MARKER} 读目标 '{exc.target.display_path}' 超出可读根, "
                "需要用户批准; 当前不允许访问。"
            )
        else:
            message = f"{DENIAL_MARKER} {exc}"
        return ToolResponse.error(code=ToolErrorCode.ACCESS_DENIED, message=message)

    def _prepare_read(self, path: str):
        """解析并授权读目标; 返回 (full_path, error_response)。"""
        try:
            target = self._resolve(path)
            authorize_read(self._current_policy(), target)
        except PathError as exc:
            return None, ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message=str(exc))
        except (SandboxApprovalRequired, SandboxDenied) as exc:
            return None, self._read_denied(exc)
        return Path(target.target_key), None

    def _prepare_write(self, path: str):
        """解析并授权写目标; 返回 (full_path, error_response)。"""
        try:
            target = self._resolve(path)
            authorize_write(self._current_policy(), target)
        except PathError as exc:
            return None, ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message=str(exc))
        except SandboxDenied as exc:
            return None, ToolResponse.error(code=ToolErrorCode.ACCESS_DENIED, message=str(exc))
        return Path(target.target_key), None

    def _authorize_derived(self, path: Path) -> Optional[ToolResponse]:
        """对派生路径(临时文件/备份)做二次围栏。

        这些路径由已授权的目标派生而来, 但可能被预置软链劫持(如
        `foo.py.tmp -> /外部文件` 或 `.backups -> /外部目录`)。此处重新
        解析 realpath 身份并授权, 软链指向围栏外时被拒。放行返回 None。
        """
        try:
            target = resolve_path(str(path), self.working_dir)
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
