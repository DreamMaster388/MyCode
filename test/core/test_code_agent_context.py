"""CodeAgent 上下文行为：工具结果全文进入下一次请求。"""
from pathlib import Path

from agents.agent.code_agent import CodeAgent
from agents.core.config import Config
from agents.core.llm_response import LLMStreamChunk, LLMStreamChunkType, ToolCall
from agents.tools.base import Tool
from agents.tools.registry import ToolRegistry
from agents.tools.response import ToolResponse


class _LongOutputTool(Tool):
    def __init__(self, output: str):
        super().__init__("LongOutput", "返回超长文本")
        self.output = output
        self.calls = 0

    def run(self, parameters):
        self.calls += 1
        return ToolResponse.success(self.output)

    def get_parameters(self):
        return []


class _RecordingLLM:
    model = "fake-model"

    def __init__(self):
        self.requests = []
        self._round = 0

    def stream_invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.requests.append(list(messages))
        if self._round == 0:
            self._round += 1
            yield LLMStreamChunk(
                type=LLMStreamChunkType.DONE,
                tool_calls=[ToolCall(id="call-1", name="LongOutput", arguments="{}")],
                usage={"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            )
        else:
            self._round += 1
            yield LLMStreamChunk(type=LLMStreamChunkType.CONTENT, text="done")
            yield LLMStreamChunk(
                type=LLMStreamChunkType.DONE,
                usage={"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22},
            )

    def invoke_with_tools(self, *args, **kwargs):
        raise AssertionError("not used")

    def invoke(self, *args, **kwargs):
        raise AssertionError("not used")


def _config() -> Config:
    return Config(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )


def test_tool_output_full_text_reaches_next_request(tmp_path):
    long_text = "\n".join(f"line {i}" for i in range(3000))
    registry = ToolRegistry()
    tool = _LongOutputTool(long_text)
    registry.register_tool(tool)
    llm = _RecordingLLM()
    agent = CodeAgent(
        name="context-check",
        llm=llm,
        tool_registry=registry,
        system_prompt="SYS",
        config=_config(),
        max_steps=3,
    )

    list(agent.stream_run("run"))

    assert tool.calls == 1
    assert len(llm.requests) == 2
    second = llm.requests[1]
    tool_messages = [m for m in second if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"] == long_text
    assert tool_messages[0]["tool_call_id"] == "call-1"
    assert "完整内容见" not in tool_messages[0]["content"]
    assert list(Path(tmp_path).iterdir()) == []


def test_agent_records_prompt_tokens_on_builder():
    registry = ToolRegistry()
    registry.register_tool(_LongOutputTool("ok"))
    config = _config()
    config.context_window = 30
    config.compression_threshold = 0.5
    agent = CodeAgent(
        name="context-check",
        llm=_RecordingLLM(),
        tool_registry=registry,
        system_prompt="SYS",
        config=config,
        max_steps=3,
    )

    list(agent.stream_run("run"))

    assert agent.context_builder.should_compress() is True
