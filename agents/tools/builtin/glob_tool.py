from typing import Dict, Any, List, Optional, TYPE_CHECKING

from ..base import Tool, ToolParameter
from ..response import ToolResponse
from ..errors import ToolErrorCode

if TYPE_CHECKING:
    from ..registry import ToolRegistry


class GlobTool(Tool):
    def __init__(self, 
                 project_root: str = ".",
                 registry: Optional['ToolRegistry'] = None):
        super().__init__(name="glob", 
                         description="Search for files matching a pattern in the working directory and return the names of the files found.", 
                         expandable=False)
        self.project_root = project_root
        self.registry = registry

    def get_parameters(self) -> List[ToolParameter]:
            return [
                ToolParameter(name="pattern", 
                              description="The regex pattern to search for.", 
                              type=str, 
                              required=True),
                ToolParameter(name="org_path", 
                              description="Starting directory to search.", 
                              type=str, 
                              required=False),
            ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        pattern = parameters.get("pattern")
        
        if not pattern:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, 
                                        message="Missing required parameters: 'pattern'.")

        import subprocess
        import os

        cmd = [
            "rg",               # 优先使用 rg，若没有则回退到 find
            "--files",          # 只列出文件名
            "--glob", pattern   # 重要！防止 pattern 以 "-" 开头被误认为参数
        ]

        # 设置目标路径
        target_path = parameters.get("org_path", ".")
        cmd.append(target_path)

        try:
            print(f"Executing command: {' '.join(cmd)}")
            result = subprocess.run(cmd, 
                                    check=True, 
                                    stdout=subprocess.PIPE, 
                                    stderr=subprocess.PIPE, 
                                    text=True,
                                    timeout=30,       # 必须设置超时，防止搜索大型 node_modules 卡死
                                    env=os.environ,   # 继承环境变量以便找到 rg 二进制
                                    cwd=self.project_root  # 在项目根目录下执行
                                   )
            # ripgrep 退出码：0=有匹配，1=无匹配，>1=错误
            if result.returncode == 0:
                return ToolResponse.success(text=result.stdout)
            elif result.returncode == 1:
                return ToolResponse.success(text="")  # 无匹配返回空字符串
            else:
                return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, 
                                            message=f"Command failed with exit code {result.returncode}: {result.stderr}")
        except subprocess.CalledProcessError as e:
            return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, 
                                        message=f"Command failed with exit code {e.returncode}: {e.stderr}")