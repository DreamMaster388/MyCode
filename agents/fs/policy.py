"""沙箱策略: 模式 + 可写根, 以及统一的写授权判定。

文件工具与 bash 写重定向都消费这里的 writable_roots / authorize_write,
保证"文件工具不能写而 bash 能写"这类不对称不会出现。
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


@dataclass(frozen=True)
class SandboxPolicy:
    """一次调用生效的策略。

    workspace_root : 工作区根(已解析为稳定身份)。
    extra_writable : 额外可写根(默认空; 需要临时目录等时显式追加)。
    """

    mode: SandboxMode
    workspace_root: FsTarget
    extra_writable: tuple[FsTarget, ...] = ()


class SandboxDenied(Exception):
    """写操作被策略拒绝。code 为稳定错误码, 携带模式与目标展示路径。"""

    def __init__(self, message: str, mode: SandboxMode, display_path: str,
                 code: str = "FS_SANDBOX_DENIED") -> None:
        super().__init__(message)
        self.code = code
        self.mode = mode
        self.display_path = display_path


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


def authorize_write(policy: SandboxPolicy, target: FsTarget) -> None:
    """写授权: 放行返回 None, 拒绝抛 SandboxDenied。"""
    mode = _as_mode(policy.mode)
    if mode == SandboxMode.DANGER_FULL_ACCESS:
        return
    if mode == SandboxMode.READ_ONLY:
        raise SandboxDenied(
            "写操作被拒绝: read-only 模式禁止任何写入",
            mode, target.display_path,
        )
    roots = writable_roots(policy)
    if any(is_within(root, target) for root in roots):
        return
    root_display = ", ".join(root.display_path for root in roots) or "(无)"
    raise SandboxDenied(
        f"写目标 '{target.display_path}' 超出可写根 [{root_display}], 已拦截。",
        mode, target.display_path,
    )