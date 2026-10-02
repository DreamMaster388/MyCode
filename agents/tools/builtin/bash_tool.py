from typing import Dict, Any, List, Optional, TYPE_CHECKING

from ..base import Tool, ToolParameter
from ..response import ToolResponse
from ..errors import ToolErrorCode
from ...fs import SandboxMode, SandboxPolicy, build_policy

if TYPE_CHECKING:
    from ..registry import ToolRegistry

class BashTool(Tool):
    def __init__(self, 
                 registry: Optional['ToolRegistry'] = None,
                 project_root: str = ".", 
                 policy: Optional[SandboxPolicy] = None):
        super().__init__(name="Bash", 
                         description="Execute a shell command in the project's " \
                         "working directory and return its output. Use this to run build/test/lint commands, " \
                         "install dependencies, inspect the environment, and automate any task operable from the terminal.", 
                         expandable=False)
        self.registry = registry
        # 与文件工具/ModeGuard 共享同一策略与同一根
        self.policy = policy or build_policy(SandboxMode.WORKSPACE_WRITE, project_root)
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

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        command = parameters.get("command")
        if not command:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, 
                                        message="Missing required parameter: 'command'.")

        import subprocess

        try:
            result = subprocess.run(
                command, shell=True, check=True, cwd=self.workdir,   # 新增 cwd
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                timeout=parameters.get("timeout", 30),
            )
            return ToolResponse.success(text=result.stdout)
        except subprocess.CalledProcessError as e:
            return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, 
                                        message=f"Command failed with exit code {e.returncode}: {e.stderr}")