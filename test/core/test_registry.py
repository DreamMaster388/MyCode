"""ToolRegistry 工具名大小写不敏感查找的单元测试。"""
from typing import Any, Dict, List

from agents.tools.base import Tool, ToolParameter
from agents.tools.registry import ToolRegistry
from agents.tools.response import ToolResponse


class _EchoTool(Tool):
    def __init__(self, name: str = "Echo"):
        super().__init__(name=name, description="echo", expandable=False)

    def get_parameters(self) -> List[ToolParameter]:
        return []

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        return ToolResponse.success(text="ok")


def test_get_tool_is_case_insensitive():
    registry = ToolRegistry()
    registry.register_tool(_EchoTool("Bash"))
    assert registry.get_tool("Bash") is registry.get_tool("bash")
    assert registry.get_tool("BASH") is registry.get_tool("Bash")
    assert registry.get_tool("BaSh") is registry.get_tool("Bash")


def test_get_tool_unknown_returns_none():
    registry = ToolRegistry()
    registry.register_tool(_EchoTool("Read"))
    assert registry.get_tool("nope") is None
    assert registry.get_tool("ReadTool") is None


def test_execute_tool_resolves_case():
    registry = ToolRegistry()
    registry.register_tool(_EchoTool("Grep"))
    response = registry.execute_tool("grep", {})
    assert response.text == "ok"


def test_unregister_is_case_insensitive():
    registry = ToolRegistry()
    registry.register_tool(_EchoTool("Glob"))
    registry.unregister("gLoB")
    assert registry.get_tool("Glob") is None
