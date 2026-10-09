"""容器版编码工具：Read / Write / Edit / Bash / Grep / Glob。

与 `agents/tools/builtin` 的同名工具接口一致（供同一套 CodeAgent 使用），
但所有 I/O 都通过 DockerSandbox 在实例容器 /testbed 内执行，因此 Agent 操作
的是真实仓库、并能用仓库 conda env 跑测试。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.tools.base import Tool, ToolParameter
from agents.tools.errors import ToolErrorCode
from agents.tools.response import ToolResponse

from .docker_sandbox import DockerSandbox


class _ContainerTool(Tool):
    is_write_tool = False
    path_params: tuple[str, ...] = ()

    def __init__(self, name: str, description: str, sandbox: DockerSandbox) -> None:
        super().__init__(name=name, description=description, expandable=False)
        self.sandbox = sandbox


class ContainerReadTool(_ContainerTool):
    def __init__(self, sandbox: DockerSandbox) -> None:
        super().__init__(
            "Read",
            "Read a file from the repository (with 1-based line numbers) or list a directory. "
            "Use offset/limit (0-based line offset) for large files.",
            sandbox,
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="path", description="File or directory path (relative to /testbed or absolute).", type="string", required=True),
            ToolParameter(name="offset", description="0-based line offset to start reading from.", type="integer", required=False),
            ToolParameter(name="limit", description="Maximum number of lines to read.", type="integer", required=False),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        path = parameters.get("path")
        if not path:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="Missing required parameter: 'path'.")
        res = self.sandbox.read_text(path, int(parameters.get("offset") or 0), int(parameters.get("limit") or 2000))
        if res.rc == 0:
            return ToolResponse.success(text=res.out or "(empty)")
        if res.rc == 2:
            return ToolResponse.error(code=ToolErrorCode.NOT_FOUND, message=res.out.strip() or f"'{path}' not found")
        return ToolResponse.error(code=ToolErrorCode.INTERNAL_ERROR, message=(res.err or res.out).strip())


class ContainerWriteTool(_ContainerTool):
    is_write_tool = True
    path_params = ("path",)

    def __init__(self, sandbox: DockerSandbox) -> None:
        super().__init__(
            "Write",
            "Create a new file or overwrite an existing file with the given content.",
            sandbox,
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="path", description="File path to write.", type="string", required=True),
            ToolParameter(name="content", description="Full file content.", type="string", required=True),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        path = parameters.get("path")
        content = parameters.get("content")
        if not path or content is None:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="Missing required parameters: 'path' and 'content'.")
        res = self.sandbox.write_text(path, content)
        if res.ok:
            return ToolResponse.success(text=res.out.strip())
        return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, message=(res.err or res.out).strip())


class ContainerEditTool(_ContainerTool):
    is_write_tool = True
    path_params = ("path",)

    def __init__(self, sandbox: DockerSandbox) -> None:
        super().__init__(
            "Edit",
            "Replace an exact string in a file. 'old_string' must appear exactly once. "
            "Read the file first and include enough context to make it unique.",
            sandbox,
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="path", description="File path to edit.", type="string", required=True),
            ToolParameter(name="old_string", description="Exact existing text to replace.", type="string", required=True),
            ToolParameter(name="new_string", description="Replacement text.", type="string", required=True),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        path = parameters.get("path")
        if not path or "old_string" not in parameters or "new_string" not in parameters:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="Missing required parameters: 'path', 'old_string', 'new_string'.")
        res = self.sandbox.edit_text(path, parameters["old_string"], parameters["new_string"])
        if res.ok:
            return ToolResponse.success(text=res.out.strip())
        if res.rc in (2, 3):
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message=res.out.strip())
        return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, message=(res.err or res.out).strip())


class ContainerBashTool(_ContainerTool):
    """Bash 工具：命令在实例容器的仓库 conda 环境下执行。"""

    def __init__(self, sandbox: DockerSandbox) -> None:
        super().__init__(
            "Bash",
            "Execute a shell command inside the repository (working dir /testbed) with the project's "
            "conda environment activated. Use this to run tests, linters and build commands.",
            sandbox,
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="command", description="The full shell command to execute.", type="string", required=True),
            ToolParameter(name="description", description="A brief description of what the command does.", type="string", required=False),
            ToolParameter(name="timeout", description="Max seconds to wait (default 120, max 1200).", type="integer", required=False),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        command = parameters.get("command")
        if not command:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="Missing required parameter: 'command'.")
        try:
            timeout = int(parameters.get("timeout") or 120)
        except (TypeError, ValueError):
            timeout = 120
        timeout = max(1, min(timeout, 1200))
        res = self.sandbox.bash(command, timeout=timeout)
        text = res.out
        if res.err:
            text = f"{text}\n[stderr]\n{res.err}" if text else res.err
        if res.rc == 0:
            return ToolResponse.success(text=text or "(no output)")
        if res.rc == 124:
            return ToolResponse.error(code=ToolErrorCode.TIMEOUT, message=f"Command timed out after {timeout}s.\n{text}")
        return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, message=f"Command failed with exit code {res.rc}:\n{text}")


class ContainerGrepTool(_ContainerTool):
    def __init__(self, sandbox: DockerSandbox) -> None:
        super().__init__(
            "Grep",
            "Search file contents for a regex pattern inside the repository and return matching lines.",
            sandbox,
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="pattern", description="Regex pattern to search for.", type="string", required=True),
            ToolParameter(name="path", description="File or directory to search (default current dir).", type="string", required=False),
            ToolParameter(name="glob", description="Optional glob filter, e.g. '*.py'.", type="string", required=False),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        pattern = parameters.get("pattern")
        if not pattern:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="Missing required parameter: 'pattern'.")
        res = self.sandbox.grep(pattern, parameters.get("path") or ".", parameters.get("glob"))
        if res.rc in (0, 1):
            return ToolResponse.success(text=res.out.strip() or "No matches found.")
        return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, message=(res.err or res.out).strip())


class ContainerGlobTool(_ContainerTool):
    def __init__(self, sandbox: DockerSandbox) -> None:
        super().__init__(
            "Glob",
            "Find files by glob pattern (e.g. '**/*.py'). This is not a regex search; use Grep for content.",
            sandbox,
        )

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(name="pattern", description="Glob pattern, e.g. '**/*.py'.", type="string", required=True),
            ToolParameter(name="path", description="Starting directory (default current dir).", type="string", required=False),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        pattern = parameters.get("pattern")
        if not pattern:
            return ToolResponse.error(code=ToolErrorCode.INVALID_PARAM, message="Missing required parameter: 'pattern'.")
        res = self.sandbox.glob_files(pattern, parameters.get("path") or ".")
        if res.ok:
            return ToolResponse.success(text=res.out.strip() or "No files found.")
        return ToolResponse.error(code=ToolErrorCode.EXECUTION_ERROR, message=(res.err or res.out).strip())


def build_container_tools(sandbox: DockerSandbox) -> list[Tool]:
    return [
        ContainerReadTool(sandbox),
        ContainerWriteTool(sandbox),
        ContainerEditTool(sandbox),
        ContainerBashTool(sandbox),
        ContainerGrepTool(sandbox),
        ContainerGlobTool(sandbox),
    ]
