"""bubblewrap 后端(可选)。

bwrap 隔离更强(挂载命名空间 + PID 命名空间), 但依赖非特权 user namespace。
在禁用 userns 的环境(如 AppArmor 的 apparmor_restrict_unprivileged_userns=1)
里探测会失败, provider 会自动回退到 Landlock。
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional

from ..fs import SandboxMode, SandboxPolicy
from .base import ConfinedCommand, SandboxUnavailableError, write_roots

_available_cache: Optional[bool] = None


def _mode_value(mode) -> str:
    return getattr(mode, "value", mode)


class BwrapBackend:
    """bubblewrap 后端。"""

    name = "bwrap"

    def available(self) -> bool:
        global _available_cache
        if _available_cache is None:
            _available_cache = self._probe()
        return _available_cache

    def _probe(self) -> bool:
        if shutil.which("bwrap") is None:
            return False
        probe = [
            "bwrap", "--ro-bind", "/", "/", "--dev", "/dev",
            "--unshare-pid", "--die-with-parent", "--", "/bin/true",
        ]
        try:
            result = subprocess.run(
                probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def confine(self, command: str, policy: SandboxPolicy) -> ConfinedCommand:
        if not self.available():
            raise SandboxUnavailableError(
                "bwrap 不可用(缺少 bwrap 或 user namespace 被禁用), 已按 fail-closed 拒绝执行。"
            )
        argv = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--unshare-pid",
            "--die-with-parent",
        ]
        if policy.mode == SandboxMode.WORKSPACE_WRITE:
            argv += ["--tmpfs", "/tmp"]
            for root in write_roots(policy):
                if root == "/tmp":
                    continue
                argv += ["--bind", root, root]
        argv += ["--", "bash", "-c", command]
        return ConfinedCommand(
            argv=tuple(argv),
            backend=self.name,
            env_overrides={"LC_ALL": "C"},
        )
