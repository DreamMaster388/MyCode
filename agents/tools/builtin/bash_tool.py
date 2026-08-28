from typing import Dict, Any, List, Optional, TYPE_CHECKING

from ..base import Tool, ToolParameter
from ..response import ToolResponse
from ..errors import ToolErrorCode

if TYPE_CHECKING:
    from ..registry import ToolRegistry

class BashTool(Tool):
    def __init__(self, 
                 registry: Optional['ToolRegistry'] = None):
        super().__init__(name="bash", 
                         description="Execute a shell command in the project's " \
                         "working directory and return its output. Use this to run build/test/lint commands, " \
                         "install dependencies, inspect the environment, and automate any task operable from the terminal.", 
                         expandable=False)
        self.registry = registry

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="command", 
                          description="The bash command to execute.", 
                          type=str, 
                          required=True)
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        command = parameters.get("command")
        if not command:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, 
                                        message="Missing required parameter: 'command'.")

        import subprocess

        try:
            print(f"Executing command: {command}")
            result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return ToolResponse.success(text=result.stdout)
        except subprocess.CalledProcessError as e:
            return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, 
                                        message=f"Command failed with exit code {e.returncode}: {e.stderr}")