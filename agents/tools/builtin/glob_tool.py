from typing import Dict, Any, List, Optional, TYPE_CHECKING

from ..base import Tool, ToolParameter
from ..response import ToolResponse
from ..errors import ToolErrorCode
from .fs_access import _FsTool

if TYPE_CHECKING:
    from ..registry import ToolRegistry


class GlobTool(Tool, _FsTool):
    access_kind = "read"
    path_params = ("path",)

    def __init__(self, 
                 project_root: str = ".",
                 registry: Optional['ToolRegistry'] = None,
                 working_dir: Optional[str] = None,
                 policy=None,
                 policy_provider=None):
        super().__init__(name="Glob", 
                         description="Search for files matching a pattern in the working directory and return the names of the files found.", 
                         expandable=False)
        self._init_fs(project_root, working_dir, policy, policy_provider)
        self.registry = registry

    def get_parameters(self) -> List[ToolParameter]:
            return [
                ToolParameter(name="pattern", 
                              description="Glob pattern to match (e.g. '**/*.py'). This is NOT a regex; use the grep tool for regex searches.", 
                              type="string", 
                              required=True),
                ToolParameter(name="path", 
                              description="The starting directory to search in (relative to project root). Defaults to the current directory.", 
                              type="string", 
                              required=False),
            ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        pattern = parameters.get("pattern")
        
        if not pattern:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, 
                                        message="Missing required parameters: 'pattern'.")

        # 读路径先过策略: 可读根之外直接拒绝, 不启动 rg。
        target_path = parameters.get("path") or "."
        _, err = self._prepare_read(target_path)
        if err:
            return err

        import subprocess
        import os

        cmd = [
            "rg",               # 优先使用 rg，若没有则回退到 find
            "--files",          # 只列出文件名
            "--glob", pattern   # 重要！防止 pattern 以 "-" 开头被误认为参数
        ]

        # 设置目标路径
        cmd.append(target_path)

        try:
            result = subprocess.run(cmd, 
                                    check=True, 
                                    stdout=subprocess.PIPE, 
                                    stderr=subprocess.PIPE, 
                                    text=True,
                                    timeout=30,       # 必须设置超时，防止搜索大型 node_modules 卡死
                                    env=os.environ,   # 继承环境变量以便找到 rg 二进制
                                    cwd=str(self.working_dir)  # 相对路径按 working_dir 解析
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
