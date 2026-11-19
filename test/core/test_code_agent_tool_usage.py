"""Ticket 02 验收：工具区。

假 LLM 发出 tool_call，真实 CodeAgent 执行假工具并回填 tool 消息；
断言按工具名聚合的输入 token 与调用次数。不联网、不执行真实工具。
"""
from __future__ import annotations

from agents.agent.code_agent import CodeAgent
from agents.context.usage import ContextUsageTracker
from agents.core.config import Config
from agents.core.llm_response import LLMStreamChunk, LLMStreamChunkType, ToolCall
from agents.tools.base import Tool
from agents.tools.registry import ToolRegistry
from agents.tools.response import ToolResponse


def _usage(prompt_tokens: int) -> dict:
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": 1,
        "total_tokens": prompt_tokens + 1,
        "input_tokens": prompt_tokens,
        "output_tokens": 1,
        "cache_hit_tokens": 0,
        "cache_hit_available": True,
    }


def _done(usage: dict, tool_calls=None) -> LLMStreamChunk:
    return LLMStreamChunk(
        type=LLMStreamChunkType.DONE,
        text="",
        tool_calls=tool_calls,
        usage=usage,
    )


class _FakeLLM:
    model = "fake-model"

    def __init__(self, rounds):
        self.rounds = list(rounds)

    def stream_invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        for chunk in self.rounds.pop(0):
            yield chunk

    def invoke_with_tools(self, *args, **kwargs):
        raise AssertionError("本用例不应触发收尾")

    def invoke(self, *args, **kwargs):
        raise AssertionError("本用例不应触发收尾")


class _NoopTool(Tool):
    def __init__(self):
        super().__init__("Noop", "noop")

    def run(self, parameters):
        return ToolResponse.success("ok")

    def get_parameters(self):
        return []


def _agent(llm) -> CodeAgent:
    registry = ToolRegistry()
    registry.register_tool(_NoopTool())
    config = Config(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )
    return CodeAgent(
        name="tool-usage",
        llm=llm,
        config=config,
        tool_registry=registry,
        system_prompt="SYS",
        max_steps=5,
    )


def test_tool_area_aggregates_args_and_result_by_name():
    llm = _FakeLLM(
        [
            [_done(_usage(100), tool_calls=[ToolCall(id="c1", name="Noop", arguments="{}")])],
            [_done(_usage(50))],
        ]
    )
    agent = _agent(llm)

    agent.run("fix it")
    usage = agent._session_metadata["context_usage"]

    assert usage["tools"]["Noop"]["calls"] == 1
    assert usage["tools"]["Noop"]["input_tokens"] > 0
    assert usage["tools"]["Noop"]["input_tokens"] <= (
        usage["categories"]["assistant"] + usage["categories"]["tool_result"]
    )
    assert usage["categories"]["tool_schema"] > 0
    assert "tool_schema" not in usage["tools"]


def test_unknown_tool_and_argument_error_still_count():
    llm = _FakeLLM(
        [
            [
                _done(
                    _usage(100),
                    tool_calls=[
                        ToolCall(id="c1", name="Noop", arguments="{bad"),
                        ToolCall(id="c2", name="Ghost", arguments="{}"),
                    ],
                )
            ],
            [_done(_usage(50))],
        ]
    )
    agent = _agent(llm)

    agent.run("fix it")
    usage = agent._session_metadata["context_usage"]

    assert usage["tools"]["Noop"]["calls"] == 1
    assert usage["tools"]["Ghost"]["calls"] == 1
    assert usage["tools"]["Noop"]["input_tokens"] > 0
    assert usage["tools"]["Ghost"]["input_tokens"] > 0


def test_tool_tokens_accumulate_when_context_is_resent():
    def _tokens(rounds):
        agent = _agent(_FakeLLM(rounds))
        agent.run("fix it")
        return agent._session_metadata["context_usage"]["tools"]["Noop"]["input_tokens"]

    one_call = _tokens(
        [
            [_done(_usage(100), tool_calls=[ToolCall(id="c1", name="Noop", arguments="{}")])],
            [_done(_usage(50))],
        ]
    )
    two_calls = _tokens(
        [
            [_done(_usage(100), tool_calls=[ToolCall(id="c1", name="Noop", arguments="{}")])],
            [_done(_usage(100), tool_calls=[ToolCall(id="c2", name="Noop", arguments="{}")])],
            [_done(_usage(50))],
        ]
    )

    assert two_calls > one_call


def _tracker_usage(messages, labels, prompt_tokens=10000):
    tracker = ContextUsageTracker()
    tracker.begin_request(messages, labels)
    tracker.commit_request(prompt_tokens)
    return tracker.to_dict()


def test_multi_tool_call_split_follows_arguments_length():
    usage = _tracker_usage(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "Bash", "arguments": "{}"}},
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {"name": "Read", "arguments": "x" * 212},
                    },
                ],
            }
        ],
        ["assistant"],
    )

    bash = usage["tools"]["Bash"]["input_tokens"]
    read = usage["tools"]["Read"]["input_tokens"]
    assert bash > 0
    # arguments 长度比 2:212；按整段 call JSON 分摊时只有约 1:4
    assert read > 50 * bash
    assert bash + read <= usage["categories"]["assistant"]


def test_tool_result_tokens_map_to_originating_tool():
    def _tokens(with_result):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "Noop", "arguments": "{}"}}
                ],
            }
        ]
        labels = ["assistant"]
        if with_result:
            messages.append(
                {"role": "tool", "tool_call_id": "c1", "content": "x" * 500}
            )
            labels.append("tool_result")
        usage = _tracker_usage(messages, labels)
        assert "unknown" not in usage["tools"]
        return usage["tools"]["Noop"]["input_tokens"]

    assert _tokens(True) > _tokens(False) + 100
