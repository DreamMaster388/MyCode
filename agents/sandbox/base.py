"""沙箱后端抽象。

这一层只回答"如何把任意 argv 关进内核围栏", 不做策略判定(策略在
`agents/fs/policy.py`)。调用方通过 `SandboxProvider` 拿到具体后端, 把任意
argv 包装成受限 argv 后交给 `subprocess` 执行。

"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, Sequence, runtime_checkable

if TYPE_CHECKING:
    from ..fs import SandboxPolicy


class SandboxUnavailableError(RuntimeError):
    """没有任何可用后端(或用户强制的后端不可用)。

    调用方必须 fail-closed: 拒绝执行外部命令, 而不是退回无围栏执行。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


# 被内核围栏拒绝时, 命令自身通常会把这些字样写到 stderr (EACCES/EROFS/EPERM)。
# 注意: 这只是"方言匹配", 正常权限错误也可能命中 —— 作为启发式使用。
_DEFAULT_DENIAL_SIGNATURES: tuple[str, ...] = (
    "read-only file system",
    "permission denied",
    "operation not permitted",
)


@dataclass(frozen=True)
class ConfinedCommand:
    """平台后端包装后的可执行命令。

    argv               : 直接交给 subprocess 的完整参数列表。
    backend            : 后端名 ('bwrap' / 'landlock' / 'none')。
    denial_signatures  : 出事时用于判定"这是策略拒绝而非命令报错"的 stderr 子串。
    enforcement        : 'full' 完全受限; 'partial' 仅部分维度受限(如无网络隔离)。
    """

    argv: tuple[str, ...]
    backend: str
    denial_signatures: tuple[str, ...] = field(default=_DEFAULT_DENIAL_SIGNATURES)
    enforcement: str = "full"
    env_overrides: dict[str, str] = field(default_factory=dict)

    def looks_like_denial(self, returncode: int, stderr: str | None) -> bool:
        """非零退出且 stderr 命中方言 -> 认为是内核围栏拒绝。"""
        if returncode == 0 or not self.denial_signatures:
            return False
        low = (stderr or "").lower()
        return any(sig in low for sig in self.denial_signatures)


@runtime_checkable
class SandboxBackend(Protocol):
    """一个沙箱后端。"""

    name: str

    def available(self) -> bool:
        """本机是否可用(探测一次, 实现内部应缓存)。"""
        ...

    def confine(self, argv: Sequence[str], policy: "SandboxPolicy") -> ConfinedCommand:
        """把任意 argv 包装成受限命令。不可用时应抛 SandboxUnavailableError。"""
        ...


def write_roots(policy: "SandboxPolicy") -> list[str]:
    """一次 workspace-write 执行允许写入的根(规范化、去重、仅取已存在目录)。

    一期只放行工作区 + 额外可写根 + 系统临时目录; 不放行 HOME / venv / 缓存。
    read-only / danger-full-access 返回空(前者无写根, 后者不包装)。
    """
    from ..fs import SandboxMode

    if policy.mode != SandboxMode.WORKSPACE_WRITE:
        return []
    candidates = [policy.workspace_root.display_path]
    candidates += [root.display_path for root in policy.extra_writable]
    candidates += ["/tmp", tempfile.gettempdir()]
    seen: set[str] = set()
    roots: list[str] = []
    for raw in candidates:
        real = os.path.realpath(raw)
        if real in seen or not os.path.isdir(real):
            continue
        seen.add(real)
        roots.append(real)
    return roots


# 系统命令运行所需的最小可读根(暂定值, 后续由读围栏测试收敛)。
# 除固定系统目录外, 还需包含当前解释器前缀(conda/venv): 否则围栏内
# `python -c pass` 连标准库都读不到。
def _interpreter_read_roots() -> tuple[str, ...]:
    """当前解释器/工具链的读根(随运行环境变化)。"""
    candidates = (
        getattr(sys, "base_prefix", ""),
        getattr(sys, "prefix", ""),
        os.path.dirname(os.path.realpath(sys.executable)),
    )
    roots: list[str] = []
    for raw in candidates:
        if raw and raw not in roots:
            roots.append(raw)
    return tuple(roots)


SYSTEM_READ_ROOTS: tuple[str, ...] = (
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/usr",
    "/etc",
    "/proc",
    "/dev",
    "/sys",
) + _interpreter_read_roots()


def read_roots(policy: "SandboxPolicy") -> list[str]:
    """一次执行允许读取的根(系统最小集 + 策略可读根)。

    - read_all(trusted) 直接返回 ["/"]: 全盘可读, 写仍由 write_roots 限制;
    - 可读根 = SYSTEM_READ_ROOTS + readable_roots(policy)(项目目录 + 用户批准根);
    - 与 write_roots 一致: realpath 归一、去重、只保留已存在目录;
    - danger-full-access 不包装执行, 返回空(表示不做读限制)。
    """
    from ..fs import SandboxMode, readable_roots

    if getattr(policy, "read_all", False):
        return ["/"]
    if policy.mode == SandboxMode.DANGER_FULL_ACCESS:
        return []
    candidates = list(SYSTEM_READ_ROOTS)
    candidates += [root.display_path for root in readable_roots(policy)]
    seen: set[str] = set()
    roots: list[str] = []
    for raw in candidates:
        real = os.path.realpath(raw)
        if real in seen or not os.path.isdir(real):
            continue
        seen.add(real)
        roots.append(real)
    return roots
