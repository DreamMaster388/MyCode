"""Landlock 后端: 通过独立启动器给 bash 子进程套上内核写围栏。

本后端的 confine() 产出 `[python, landlock_exec.py, --mode ..., --write-root ..., --, bash, -c, cmd]`。
之所以走独立脚本(而非在本进程内 preexec_fn), 是为了避开 preexec_fn 的线程安全坑,
并让"围栏只作用于 bash 子进程、不关住 agent 自身"这一点天然成立。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from ..fs import SandboxPolicy
from .base import (
    ConfinedCommand,
    SandboxUnavailableError,
    write_roots,
)

_LAUNCHER = Path(__file__).resolve().parent / "landlock_exec.py"

# 后端可用性探测有副作用(起子进程), 缓存结果避免每次命令都探测。
_available_cache: Optional[bool] = None


def _mode_value(mode) -> str:
    return getattr(mode, "value", mode)


class LandlockBackend:
    """Linux Landlock 后端。"""

    name = "landlock"

    def __init__(self, launcher: Path = _LAUNCHER) -> None:
        self._launcher = launcher

    def available(self) -> bool:
        global _available_cache
        if _available_cache is None:
            _available_cache = self._probe()
        return _available_cache

    def _probe(self) -> bool:
        if not hasattr(os, "O_PATH"):  # Landlock 路径规则依赖 O_PATH(Linux)
            return False
        if not self._launcher.is_file():
            return False
        try:
            result = subprocess.run(
                [sys.executable, str(self._launcher), "--probe"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def confine(self, command: str, policy: SandboxPolicy) -> ConfinedCommand:
        if not self.available():
            raise SandboxUnavailableError(
                "Landlock 不可用(内核/启动器探测失败), 已按 fail-closed 拒绝执行。"
            )
        argv = [
            sys.executable,
            str(self._launcher),
            "--mode",
            _mode_value(policy.mode),
        ]
        for root in write_roots(policy):
            argv += ["--write-root", root]
        argv += ["--", "bash", "-c", command]
        return ConfinedCommand(
            argv=tuple(argv),
            backend=self.name,
            # 强制 C locale: 内核/bash 的拒绝信息才会是英文, 供 denial 方言匹配。
            env_overrides={"LC_ALL": "C"},
        )
