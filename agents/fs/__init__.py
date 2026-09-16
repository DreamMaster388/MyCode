from __future__ import annotations

from typing import Union

from .policy import (
    SandboxDenied,
    SandboxMode,
    SandboxPolicy,
    authorize_write,
    writable_roots,
)
from .target import FsTarget, PathError, is_within, resolve_path


def build_policy(mode: Union[SandboxMode, str], workspace_root: str) -> SandboxPolicy:
    """按模式与工作区根构造策略; workspace_root 会先解析成稳定身份。"""
    return SandboxPolicy(
        mode=mode if isinstance(mode, SandboxMode) else SandboxMode(mode),
        workspace_root=resolve_path(workspace_root, workspace_root),
    )


__all__ = [
    "FsTarget", "PathError", "resolve_path", "is_within",
    "SandboxMode", "SandboxPolicy", "SandboxDenied",
    "authorize_write", "writable_roots", "build_policy",
]