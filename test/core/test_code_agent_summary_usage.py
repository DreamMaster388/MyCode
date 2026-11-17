"""摘要 usage 记账、预算与 trace 契约（Seam 3：CodeAgent.stream_run）。"""
from agents.agent.code_agent import CodeAgent
from agents.core.config import Config
from agents.core.llm_response import (
    LLMResponse,
    LLMStreamChunk,
    LLMStreamChunkType,
    ToolCall,
)
from agents.core.message import Message
from agents.tools.base import Tool
from agents.tools.registry import ToolRegistry
from agents.tools.response import ToolResponse


def _usage(input_tokens, output_tokens, cached_tokens=0, available=True):
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_hit_tokens": cached_tokens,
        "cache_hit_available": available,
    }


class _NoopTool(Tool):
    def __init__(self):
        super().__init__("Noop", "noop")

    def run(self, parameters):
        return ToolResponse.success("ok")

    def get_parameters(self):
        return []


class _SummaryAwareLLM:
    model = "fake-model"

    def __init__(
        self,
        summary_content="## 当前任务\n- 摘要 [id=0]",
        summary_usage=None,
        summary_error=None,
        main_usage=None,
        main_tool_calls=None,
        conclude_usage=None,
    ):
        self.summary_content = summary_content
        self.summary_usage = {} if summary_usage is None else summary_usage
        self.summary_error = summary_error
        self.main_usage = main_usage or _usage(10, 1, 0)
        self.main_tool_calls = main_tool_calls
        self.conclude_usage = conclude_usage or _usage(5, 1, 0)
        self.summary_calls = 0
        self.main_calls = 0
        self.conclude_calls = 0
        self.summary_messages = None

    def invoke(self, messages, **kwargs):
        self.summary_calls += 1
        self.summary_messages = messages
        if self.summary_error is not None:
            raise self.summary_error
        return LLMResponse(
            content=self.summary_content,
            model=self.model,
            usage=dict(self.summary_usage),
        )

    def stream_invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.main_calls += 1
        yield LLMStreamChunk(
            type=LLMStreamChunkType.DONE,
            tool_calls=self.main_tool_calls,
            usage=dict(self.main_usage),
        )

    def invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.conclude_calls += 1
        return LLMResponse(
            content="concluded",
            model=self.model,
            usage=dict(self.conclude_usage),
        )


class _TraceRecorder:
    def __init__(self):
        self.events = []

    def log_event(self, event, payload, step=None):
        self.events.append((event, payload, step))


def _config(**overrides):
    return Config(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
        context_summary_enabled=True,
        context_window=100,
        compression_threshold=0.5,
        context_summary_keep_rounds=2,
        **overrides,
    )


def _agent(llm, max_steps=3, max_run_tokens=0, with_tool=True, **overrides):
    registry = ToolRegistry()
    if with_tool:
        registry.register_tool(_NoopTool())
    return CodeAgent(
        name="summary-usage",
        llm=llm,
        tool_registry=registry,
        system_prompt="SYS",
        config=_config(**overrides),
        max_steps=max_steps,
        max_run_tokens=max_run_tokens,
    )


def _seed_history(agent):
    for content, role in (
        ("u0", "user"),
        ("a1", "assistant"),
        ("u2", "user"),
        ("a3", "assistant"),
        ("u4", "user"),
        ("a5", "assistant"),
    ):
        agent.history_manager.append(Message(content, role))


def _run(agent):
    events = list(agent.stream_run("fix it"))
    return events, agent._session_metadata


def test_summary_usage_is_counted_in_run_totals(capsys):
    llm = _SummaryAwareLLM(
        summary_usage=_usage(7, 3, 2),
        main_usage=_usage(10, 1, 0),
    )
    agent = _agent(llm, with_tool=False)
    recorder = _TraceRecorder()
    agent.trace_logger = recorder
    _seed_history(agent)
    agent.context_builder.record_prompt_tokens(51)

    _, meta = _run(agent)

    assert llm.summary_calls == 1
    assert llm.main_calls == 1
    assert meta["input_tokens"] == 17
    assert meta["output_tokens"] == 4
    assert meta["total_tokens"] == 21
    assert meta["cached_tokens"] == 2
    assert meta["cache_hit_available"] is True
    assert meta["total_steps"] == 1
    summary_events = [event for event in recorder.events if event[0] == "summary"]
    assert len(summary_events) == 1
    payload = summary_events[0][1]
    assert payload["mode"] == "normal"
    assert payload["degraded"] is False
    assert payload["usage"] == _usage(7, 3, 2)
    assert payload["latency_ms"] is not None
    assert payload["content"].startswith("【历史对话摘要】")
    assert capsys.readouterr().out == ""


def test_summary_empty_usage_marks_cache_unavailable():
    llm = _SummaryAwareLLM(summary_usage={}, main_usage=_usage(10, 1, 0))
    agent = _agent(llm)
    _seed_history(agent)
    agent.context_builder.record_prompt_tokens(51)

    _, meta = _run(agent)

    assert meta["input_tokens"] == 10
    assert meta["output_tokens"] == 1
    assert meta["total_tokens"] == 11
    assert meta["cache_hit_available"] is False


def test_summary_without_real_usage_does_not_flip_cache_marker():
    llm = _SummaryAwareLLM(
        summary_error=RuntimeError("boom"),
        main_usage=_usage(10, 1, 0),
    )
    agent = _agent(llm)
    recorder = _TraceRecorder()
    agent.trace_logger = recorder
    _seed_history(agent)
    agent.context_builder.record_prompt_tokens(51)

    _, meta = _run(agent)

    assert llm.summary_calls == 1
    assert meta["input_tokens"] == 10
    assert meta["output_tokens"] == 1
    assert meta["total_tokens"] == 11
    assert meta["cache_hit_available"] is True
    summary_events = [event for event in recorder.events if event[0] == "summary"]
    assert len(summary_events) == 1
    assert summary_events[0][1]["degraded"] is True
    assert "boom" in summary_events[0][1]["error"]
    assert summary_events[0][1]["usage"] is None


def test_summary_usage_is_included_in_run_budget():
    llm = _SummaryAwareLLM(
        summary_usage=_usage(10, 0, 0),
        main_usage=_usage(10, 1, 0),
        main_tool_calls=[ToolCall(id="c1", name="Noop", arguments="{}")],
        conclude_usage=_usage(5, 1, 0),
    )
    agent = _agent(llm, max_run_tokens=15)
    _seed_history(agent)
    agent.context_builder.record_prompt_tokens(51)

    events, meta = _run(agent)

    assert events[-1].data["status"] == "budget"
    assert llm.summary_calls == 1
    assert llm.conclude_calls == 1
    assert meta["input_tokens"] == 25
    assert meta["output_tokens"] == 2
    assert meta["total_tokens"] == 27
    assert meta["total_steps"] == 1


def test_summary_usage_from_conclude_build_is_counted():
    llm = _SummaryAwareLLM(
        summary_usage=_usage(7, 3, 0),
        main_usage=_usage(51, 1, 0),
        main_tool_calls=[ToolCall(id="c1", name="Noop", arguments="{}")],
        conclude_usage=_usage(5, 1, 0),
    )
    agent = _agent(llm, max_steps=1)
    recorder = _TraceRecorder()
    agent.trace_logger = recorder
    _seed_history(agent)

    events, meta = _run(agent)

    assert events[-1].data["status"] == "max_steps"
    assert llm.summary_calls == 1
    assert llm.conclude_calls == 1
    assert meta["input_tokens"] == 63
    assert meta["output_tokens"] == 5
    assert meta["total_tokens"] == 68
    assert meta["total_steps"] == 1
    summary_events = [event for event in recorder.events if event[0] == "summary"]
    assert len(summary_events) == 1
    assert summary_events[0][1]["mode"] == "normal"
