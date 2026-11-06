"""内核沙箱: 把任意 argv 关进内核读写围栏。

对外入口:
- `SandboxProvider.wrap()` 选择后端并把任意 argv 包装成受限 argv。
- `SandboxUnavailableError` 无可用后端时抛出的信号(调用方 fail-closed)。
- `ConfinedCommand`      包装结果(argv / 后端 / denial 方言 / env 覆盖)。
- `read_roots` / `write_roots`  一次执行允许读/写的根。
"""
from __future__ import annotations

from .base import (
    SYSTEM_READ_ROOTS,
    ConfinedCommand,
    SandboxBackend,
    SandboxUnavailableError,
    read_roots,
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
    "SYSTEM_READ_ROOTS",
    "read_roots",
    "write_roots",
]
