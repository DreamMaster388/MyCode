"""沙箱策略: 模式 + 可读根 + 可写根, 以及统一的读/写授权判定。

文件工具直接调用这里的 authorize_read / authorize_write; bash 内核围栏按同一套
可读/可写根(readable_roots / writable_roots) 构造规则, 保证两侧边界一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union

from .target import FsTarget, is_within


class SandboxMode(str, Enum):
    """沙箱模式。继承 str 便于与字符串比较/序列化。"""

    READ_ONLY = "read-only"                     # 禁止一切写入
    WORKSPACE_WRITE = "workspace-write"         # 只允许写到可写根之内
    DANGER_FULL_ACCESS = "danger-full-access"   # 不限制


class AccessKind(str, Enum):
    """一次路径访问的类别。继承 str 便于日志/序列化。"""

    READ = "read"
    WRITE = "write"


class AccessDecision(str, Enum):
    """策略对一次访问的判定。

    ALLOW: 落在允许根内, 直接放行;
    ASK  : 需要用户审批(由上层 ModeGuard 处理);
    DENY : 策略明确拒绝, 不进入审批(如 read-only 模式的写)。
    """

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class SandboxPolicy:
    """一次调用生效的策略。

    workspace_root : 工作区根(已解析为稳定身份), 同时也是默认可读/可写根。
    extra_writable : 额外可写根(用户批准目录、临时目录等; 默认空)。
    extra_readable : 额外可读根(用户批准目录; 默认空)。
    """

    mode: SandboxMode
    workspace_root: FsTarget
    extra_writable: tuple[FsTarget, ...] = ()
    extra_readable: tuple[FsTarget, ...] = ()


class SandboxDenied(Exception):
    """访问被策略拒绝。code 为稳定错误码, 携带模式与目标展示路径。"""

    def __init__(self, message: str, mode: SandboxMode, display_path: str,
                 code: str = "FS_SANDBOX_DENIED") -> None:
        super().__init__(message)
        self.code = code
        self.mode = mode
        self.display_path = display_path


class SandboxApprovalRequired(Exception):
    """访问落在允许根之外, 需要用户审批。

    审批层(Step 7 的 ModeGuard)会消费该异常; 未接入审批时调用方应按拒绝处理。
    """

    def __init__(self, message: str, kind: AccessKind, target: FsTarget,
                 mode: SandboxMode) -> None:
        super().__init__(message)
        self.code = "FS_APPROVAL_REQUIRED"
        self.kind = kind
        self.target = target
        self.mode = mode
        self.display_path = target.display_path


def _as_mode(mode: Union[SandboxMode, str]) -> SandboxMode:
    return mode if isinstance(mode, SandboxMode) else SandboxMode(mode)


def writable_roots(policy: SandboxPolicy) -> list[FsTarget]:
    """当前策略下的可写根列表(单一来源)。

    只有 workspace-write 才有可写根; read-only / danger-full-access 返回空。
    """
    if _as_mode(policy.mode) != SandboxMode.WORKSPACE_WRITE:
        return []
    seen: set[str] = set()
    roots: list[FsTarget] = []
    for root in (policy.workspace_root, *policy.extra_writable):
        if root.target_key not in seen:
            seen.add(root.target_key)
            roots.append(root)
    return roots


def readable_roots(policy: SandboxPolicy) -> list[FsTarget]:
    """当前策略下的可读根列表: 用户批准根 + 项目目录根。

    与 writable_roots 不同, 可读根与 mode 无关(任何模式都要能读项目与批准目录);
    系统命令所需的最小读根由沙箱层(SYSTEM_READ_ROOTS)补充。
    """
    seen: set[str] = set()
    roots: list[FsTarget] = []
    for root in (policy.workspace_root, *policy.extra_readable):
        if root.target_key not in seen:
            seen.add(root.target_key)
            roots.append(root)
    return roots


def evaluate_read(policy: SandboxPolicy, target: FsTarget) -> AccessDecision:
    """读判定: 可读根内放行, 根外进入审批。

    danger-full-access 表示不限制, 直接放行。系统读根只服务于命令沙箱,
    不在这里参与判定, 避免 Python 文件工具读 /proc、/etc 等系统路径。
    """
    mode = _as_mode(policy.mode)
    if mode == SandboxMode.DANGER_FULL_ACCESS:
        return AccessDecision.ALLOW
    if any(is_within(root, target) for root in readable_roots(policy)):
        return AccessDecision.ALLOW
    return AccessDecision.ASK


def evaluate_write(policy: SandboxPolicy, target: FsTarget) -> AccessDecision:
    """写判定: 可写根内放行, 根外进入审批, read-only 明确拒绝。

    danger-full-access 不限制; read-only 的拒绝不可审批, 避免 plan 模式
    被一次批准绕过。
    """
    mode = _as_mode(policy.mode)
    if mode == SandboxMode.DANGER_FULL_ACCESS:
        return AccessDecision.ALLOW
    if mode == SandboxMode.READ_ONLY:
        return AccessDecision.DENY
    if any(is_within(root, target) for root in writable_roots(policy)):
        return AccessDecision.ALLOW
    return AccessDecision.ASK


def authorize_read(policy: SandboxPolicy, target: FsTarget) -> None:
    """严格读授权: 放行返回 None, 待审批抛 SandboxApprovalRequired。"""
    decision = evaluate_read(policy, target)
    mode = _as_mode(policy.mode)
    if decision == AccessDecision.ALLOW:
        return
    if decision == AccessDecision.ASK:
        roots = readable_roots(policy)
        root_display = ", ".join(root.display_path for root in roots) or "(无)"
        raise SandboxApprovalRequired(
            f"读目标 '{target.display_path}' 超出可读根 [{root_display}], 需要用户批准。",
            AccessKind.READ, target, mode,
        )
    raise SandboxDenied(
        f"读目标 '{target.display_path}' 被策略拒绝。",
        mode, target.display_path,
    )


def authorize_write(policy: SandboxPolicy, target: FsTarget) -> None:
    """严格写授权: 放行返回 None, 拒绝抛 SandboxDenied。

    根外路径在这里按拒绝处理(SandboxDenied), 审批接入后由上层改用
    evaluate_write + ModeGuard.request_access, 不改变本函数的严格语义。
    """
    decision = evaluate_write(policy, target)
    mode = _as_mode(policy.mode)
    if decision == AccessDecision.ALLOW:
        return
    if decision == AccessDecision.DENY:
        raise SandboxDenied(
            "写操作被拒绝: read-only 模式禁止任何写入",
            mode, target.display_path,
        )
    roots = writable_roots(policy)
    root_display = ", ".join(root.display_path for root in roots) or "(无)"
    raise SandboxDenied(
        f"写目标 '{target.display_path}' 超出可写根 [{root_display}], 已拦截。",
        mode, target.display_path,
    )