"""bubblewrap 后端(可选, 降级方案)。

bwrap 隔离更强(挂载命名空间 + PID 命名空间), 但依赖非特权 user namespace。
在禁用 userns 的环境(如 AppArmor 的 apparmor_restrict_unprivileged_userns=1)
里探测会失败。

**读围栏说明**: bwrap 仍 `--ro-bind / /` 挂载整盘, 只能提供写围栏,
**不能限制读范围**; 只有 Landlock 后端会按 read_roots 收窄读范围。
provider 降级到本后端时, 读限制不可用。
对 trusted(read_all) 而言, 这种"全盘只读 + 写根 bind"的组合正好符合
"读全盘、写仍限项目/批准目录"的语义。
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional, Sequence

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

    def confine(self, argv: Sequence[str], policy: SandboxPolicy) -> ConfinedCommand:
        if not self.available():
            raise SandboxUnavailableError(
                "bwrap 不可用(缺少 bwrap 或 user namespace 被禁用), 已按 fail-closed 拒绝执行。"
            )
        # --ro-bind / / 始终提供全盘只读; trusted 的 read_all 无需额外处理,
        # 写范围仍由下面的 write_roots bind 决定。
        wrapped = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--unshare-pid",
            "--die-with-parent",
        ]
        if policy.mode == SandboxMode.WORKSPACE_WRITE:
            wrapped += ["--tmpfs", "/tmp"]
            for root in write_roots(policy):
                if root == "/tmp":
                    continue
                wrapped += ["--bind", root, root]
        wrapped += ["--", *[str(arg) for arg in argv]]
        return ConfinedCommand(
            argv=tuple(wrapped),
            backend=self.name,
            env_overrides={"LC_ALL": "C"},
        )
