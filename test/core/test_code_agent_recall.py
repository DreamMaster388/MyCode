"""CodeAgent 按 context_recall_enabled 注册 Recall 工具。"""
from agents.agent.code_agent import CodeAgent
from agents.core.config import Config
from agents.core.message import Message
from agents.tools.registry import ToolRegistry


def _agent(recall: bool) -> CodeAgent:
    return CodeAgent(
        name="A",
        llm=object(),
        tool_registry=ToolRegistry(),
        config=Config(
            context_recall_enabled=recall,
            trace_enabled=False,
            skills_enabled=False,
            session_enabled=False,
            subagent_enabled=False,
            todowrite_enabled=False,
            devlog_enabled=False,
        ),
        max_steps=1,
    )


def test_recall_tool_registered_when_switch_on():
    agent = _agent(True)

    assert "Recall" in agent.tool_registry.list_tools()


def test_recall_tool_absent_when_switch_off():
    agent = _agent(False)

    assert "Recall" not in agent.tool_registry.list_tools()


def test_recall_tool_schema_visible_when_switch_on():
    agent = _agent(True)

    names = [schema["function"]["name"] for schema in agent._build_tool_schemas()]
    assert "Recall" in names


def test_registered_recall_reads_from_agent_history():
    agent = _agent(True)
    agent.add_message(Message("原始工具输出", "tool", tool_call_id="c1"))

    response = agent.tool_registry.execute_tool("Recall", {"id": 0})

    assert response.status.value == "success"
    assert "原始工具输出" in response.text
