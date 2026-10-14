"""后端选择: 把一条命令按当前策略包装成受限执行。

`auto` 默认按 `bwrap -> landlock` 优先(隔离从强到弱), 也可显式强制某一后端。
全部不可用时抛 `SandboxUnavailableError`, 由调用方 fail-closed。
"""
from __future__ import annotations

from typing import Optional

from ..fs import SandboxMode, SandboxPolicy
from .base import ConfinedCommand, SandboxBackend, SandboxUnavailableError
from .bwrap import BwrapBackend
from .landlock import LandlockBackend

_PREFERENCE = ("bwrap", "landlock")


class SandboxProvider:
    """按策略把命令包装成受限命令。"""

    def __init__(self, policy: SandboxPolicy, backend: str = "auto") -> None:
        self.policy = policy
        self.backend = backend
        self._resolved: Optional[SandboxBackend] = None
        self._resolved_done = False

    def _candidates(self) -> list[SandboxBackend]:
        if self.backend == "auto":
            return [BwrapBackend(), LandlockBackend()]
        if self.backend == "bwrap":
            return [BwrapBackend()]
        if self.backend == "landlock":
            return [LandlockBackend()]
        return []

    def _select(self) -> Optional[SandboxBackend]:
        if self._resolved_done:
            return self._resolved
        self._resolved_done = True
        for candidate in self._candidates():
            if candidate.available():
                self._resolved = candidate
                break
        return self._resolved

    def confine(self, command: str) -> ConfinedCommand:
        """包装命令。danger-full-access 原样执行; 无可用后端则 fail-closed。"""
        if self.policy.mode == SandboxMode.DANGER_FULL_ACCESS:
            return ConfinedCommand(
                argv=("bash", "-c", command),
                backend="none",
                denial_signatures=(),
            )
        selected = self._select()
        if selected is None:
            raise SandboxUnavailableError(
                "没有可用的沙箱后端(需要 bwrap 或 Landlock); 已按 fail-closed 拒绝执行 bash。"
            )
        return selected.confine(command, self.policy)
