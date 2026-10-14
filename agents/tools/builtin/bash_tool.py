import os
from typing import Dict, Any, List, Optional, TYPE_CHECKING

from ..base import Tool, ToolParameter
from ..response import ToolResponse
from ..errors import ToolErrorCode
from ...fs import SandboxMode, SandboxPolicy, build_policy
from ...sandbox import SandboxProvider, SandboxUnavailableError

if TYPE_CHECKING:
    from ..registry import ToolRegistry

class BashTool(Tool):
    def __init__(self,
                 registry: Optional['ToolRegistry'] = None,
                 project_root: str = ".",
                 policy: Optional[SandboxPolicy] = None,
                 policy_provider=None,
                 sandbox_backend: str = "auto"):
        super().__init__(name="Bash",
                         description="Execute a shell command in the project's " \
                         "working directory and return its output. Use this to run build/test/lint commands, " \
                         "install dependencies, inspect the environment, and automate any task operable from the terminal.",
                         expandable=False)
        self.registry = registry
        # 与文件工具/ModeGuard 共享同一策略与同一根
        self.policy = policy or build_policy(SandboxMode.WORKSPACE_WRITE, project_root)
        # 可选的动态策略来源: 每轮执行时按当前模式(plan/build)取沙箱策略,
        # 使 plan 模式真正落到只读围栏上。缺省则一直用 self.policy。
        self._policy_provider = policy_provider
        self._sandbox_backend = sandbox_backend
        self.workdir = self.policy.workspace_root.display_path

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="command", 
                          description="The full Shell command to execute. It's recommended to use && to chain dependent commands and | to build pipelines.", 
                          type="string", 
                          required=True),
            ToolParameter(name="description", 
                            description="A brief description of what the command does.", 
                            type="string", 
                            required=False),
            ToolParameter(name="timeout", 
                        description="The maximum time (in seconds) to wait for the command to complete.", 
                        type="integer", 
                        required=False)
        ]

    def _current_policy(self) -> SandboxPolicy:
        if self._policy_provider is not None:
            try:
                return self._policy_provider()
            except Exception:
                pass
        return self.policy

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        command = parameters.get("command")
        if not command:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM,
                                        message="Missing required parameter: 'command'.")

        import subprocess

        policy = self._current_policy()
        provider = SandboxProvider(policy, backend=self._sandbox_backend)
        try:
            confined = provider.confine(command)
        except SandboxUnavailableError as exc:
            # fail-closed: 没有内核围栏就不执行, 绝不退回无围栏执行。
            return ToolResponse.error(
                code=ToolErrorCode.PERMISSION_DENIED,
                message=f"沙箱不可用, 已拒绝执行 bash(fail-closed): {exc}")

        env = dict(os.environ)
        env.update(confined.env_overrides)
        try:
            result = subprocess.run(
                confined.argv, shell=False, close_fds=True, cwd=self.workdir,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                timeout=parameters.get("timeout", 30), env=env,
            )
        except subprocess.TimeoutExpired:
            return ToolResponse.error(
                code=ToolErrorCode.TIMEOUT,
                message=f"Command timed out after {parameters.get('timeout', 30)}s.")
        except OSError as exc:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=f"Command failed to start: {exc}")

        if result.returncode == 0:
            return ToolResponse.success(text=result.stdout)

        if confined.looks_like_denial(result.returncode, result.stderr):
            mode = getattr(policy.mode, "value", policy.mode)
            return ToolResponse.error(
                code=ToolErrorCode.PERMISSION_DENIED,
                message=(
                    f"[sandbox: file access denied under {mode} mode] "
                    f"这是一次策略拒绝, 不是命令本身的问题; 不要换写法绕过。\n"
                    f"{result.stderr.strip()}"
                ))
        return ToolResponse.error(
            code=ToolErrorCode.EXECUTION_ERROR,
            message=f"Command failed with exit code {result.returncode}: {result.stderr}")
