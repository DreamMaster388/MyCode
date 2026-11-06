from typing import Dict, Any, List, Optional, TYPE_CHECKING

from ..base import Tool, ToolParameter
from ..response import ToolResponse
from ..errors import ToolErrorCode
from .fs_access import _FsTool, _permit_scoped

if TYPE_CHECKING:
    from ..registry import ToolRegistry

class GrepTool(Tool, _FsTool):
    access_kind = "read"
    path_params = ("path",)

    def __init__(self, 
                 project_root: str = ".",
                 registry: Optional['ToolRegistry'] = None,
                 working_dir: Optional[str] = None,
                 policy=None,
                 policy_provider=None,
                 access_guard=None):
        super().__init__(name="Grep", 
                         description="Search for a pattern in files within the project's working directory and return matching lines.", 
                         expandable=False)
        self._init_fs(project_root, working_dir, policy, policy_provider, access_guard)
        self.registry = registry

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="pattern", 
                          description="The regex pattern to search for.", 
                          type="string", 
                          required=True),
            ToolParameter(name="path", 
                          description="The file or directory to search in (relative to project root). Defaults to the current directory.", 
                          type="string", 
                          required=False),
            ToolParameter(name="glob",
                            description="Optional glob pattern to filter files (e.g., '*.py').", 
                            type="string", 
                            required=False)
        ]

    @_permit_scoped
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

        cmd = [
        "rg",               # 优先使用 rg，若没有则回退到 grep -r
        "--line-number",    # 显示行号
        "--with-filename",  # 显示文件名
        "--regexp", pattern # 重要！防止 pattern 以 "-" 开头被误认为参数
        ]

        # 添加目标文件
        if parameters.get("glob"):
            cmd.extend(["--glob", parameters["glob"]])

        # 设置目标路径
        cmd.append(target_path)

        import subprocess
        import os

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,       # 必须设置超时，防止搜索大型 node_modules 卡死
                env=os.environ,   # 继承环境变量以便找到 rg 二进制
                cwd=str(self.working_dir)  # 相对路径按 working_dir 解析
            )
            # ripgrep 退出码：0=有匹配，1=无匹配，>1=错误
            if result.returncode == 0:
                return ToolResponse.success(text=result.stdout)
            elif result.returncode == 1:
                return ToolResponse.success(text="No matches found.")
            else:
                # 返回 stderr 给 AI，让它自己修正
                return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, 
                                          message=f"Grep error: {result.stderr}")

        except subprocess.TimeoutExpired:
            return ToolResponse.error(code=ToolErrorCode.TIMEOUT, 
                                      message="Grep command timed out.")
        except FileNotFoundError:
            return self._fallback_to_grep(parameters)

    def _fallback_to_grep(self, parameters: Dict[str, Any]) -> ToolResponse:
        """Fallback to grep if rg is not available."""
        pattern = parameters.get("pattern")
        target_path = parameters.get("path") or "."
        cmd = [
            "grep",
            "-r",               # 递归搜索
            "-n",               # 显示行号
            "-H",               # 显示文件名
            pattern,
            target_path
        ]

        import subprocess
        import os

        try:
            print(f"Executing fallback command: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env=os.environ,
                cwd=str(self.working_dir)
            )
            if result.returncode == 0:
                return ToolResponse.success(text=result.stdout)
            elif result.returncode == 1:
                return ToolResponse.success(text="No matches found.")
            else:
                return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, 
                                          message=f"Grep error: {result.stderr}")

        except subprocess.TimeoutExpired:
            return ToolResponse.error(code=ToolErrorCode.TIMEOUT, 
                                      message="Grep command timed out.")
