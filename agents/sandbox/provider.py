"""后端选择: 把任意 argv 按当前策略包装成受限执行。

`auto` 优先 **landlock -> bwrap**:
- Landlock 同时提供读写围栏(读根/写根), 是唯一能限制读的后端;
- Landlock 不可用时降级到 bwrap: bwrap 仍 `--ro-bind / /`, **只有写围栏,
  读范围不受限(读围栏不可用)**;
- 全部不可用时抛 `SandboxUnavailableError`, 由调用方 fail-closed。

也可显式指定 backend 以便测试/排障。
"""
from __future__ import annotations

from typing import Optional, Sequence

from ..fs import SandboxMode, SandboxPolicy
from .base import ConfinedCommand, SandboxBackend, SandboxUnavailableError
from .bwrap import BwrapBackend
from .landlock import LandlockBackend

_PREFERENCE = ("landlock", "bwrap")
_BACKENDS = {"landlock": LandlockBackend, "bwrap": BwrapBackend}


class SandboxProvider:
    """按策略把任意 argv 包装成受限命令。"""

    def __init__(self, policy: SandboxPolicy, backend: str = "auto") -> None:
        self.policy = policy
        self.backend = backend
        self._resolved: Optional[SandboxBackend] = None
        self._resolved_done = False

    def _candidates(self) -> list[SandboxBackend]:
        names = _PREFERENCE if self.backend == "auto" else (self.backend,)
        return [_BACKENDS[name]() for name in names if name in _BACKENDS]

    def _select(self) -> Optional[SandboxBackend]:
        if self._resolved_done:
            return self._resolved
        self._resolved_done = True
        for candidate in self._candidates():
            if candidate.available():
                self._resolved = candidate
                break
        return self._resolved

    def wrap(self, argv: Sequence[str],
             policy: Optional[SandboxPolicy] = None) -> ConfinedCommand:
        """把任意 argv 包装成受限命令。

        policy 省略时使用构造时的策略; danger-full-access 原样执行(不包装);
        无可用后端时 fail-closed 抛 SandboxUnavailableError。

        注意: 自动选择落在 bwrap 时只保证写围栏, 读围栏不可用。
        """
        policy = policy or self.policy
        raw = tuple(str(arg) for arg in argv)
        if not raw:
            raise ValueError("wrap() 需要非空 argv")
        if policy.mode == SandboxMode.DANGER_FULL_ACCESS:
            return ConfinedCommand(
                argv=raw,
                backend="none",
                denial_signatures=(),
            )
        selected = self._select()
        if selected is None:
            raise SandboxUnavailableError(
                "没有可用的沙箱后端(需要 landlock 或 bwrap); 已按 fail-closed 拒绝执行。"
            )
        return selected.confine(raw, policy)
