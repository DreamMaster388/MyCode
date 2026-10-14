"""内核沙箱: 把 bash 子进程关进内核写围栏。

对外入口:
- `SandboxProvider`      选择后端并把命令包装成受限 argv。
- `SandboxUnavailableError` 无可用后端时抛出的信号(调用方 fail-closed)。
- `ConfinedCommand`      包装结果(argv / 后端 / denial 方言 / env 覆盖)。
"""
from __future__ import annotations

from .base import (
    ConfinedCommand,
    SandboxBackend,
    SandboxUnavailableError,
    write_roots,
)
from .bwrap import BwrapBackend
from .landlock import LandlockBackend
from .provider import SandboxProvider

__all__ = [
    "SandboxProvider",
    "SandboxBackend",
    "SandboxUnavailableError",
    "ConfinedCommand",
    "BwrapBackend",
    "LandlockBackend",
    "write_roots",
]
