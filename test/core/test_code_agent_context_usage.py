"""Ticket 01 验收：类别区与元数据骨架。

用假 LLM 驱动真实 CodeAgent 主循环，断言实际发送视图的类别分配与元数据形状；
不联网、不调用真实模型、不执行真实工具。
"""
from __future__ import annotations

from agents.agent.code_agent import CodeAgent
from agents.core.config import Config
from agents.core.llm_response import LLMStreamChunk, LLMStreamChunkType, ToolCall
from agents.tools.base import Tool
from agents.tools.registry import ToolRegistry
from agents.tools.response import ToolResponse

CATEGORY_KEYS = {
    "system",
    "tool_schema",
    "user",
    "assistant",
    "tool_result",
    "summary_message",
    "summary_call",
    "conclude_instruction",
}


def _usage(prompt_tokens: int, completion_tokens: int = 1) -> dict:
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
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
        self.calls = 0

    def stream_invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.calls += 1
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


def _config() -> Config:
    return Config(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )


def _agent(llm, with_tool: bool = True) -> CodeAgent:
    registry = ToolRegistry()
    if with_tool:
        registry.register_tool(_NoopTool())
    return CodeAgent(
        name="context-usage",
        llm=llm,
        config=_config(),
        tool_registry=registry,
        system_prompt="SYS",
        max_steps=5,
    )


def test_categories_accumulate_across_rounds_and_sum_to_input():
    llm = _FakeLLM(
        [
            [_done(_usage(100), tool_calls=[ToolCall(id="c1", name="Noop", arguments="{}")])],
            [_done(_usage(50))],
        ]
    )
    agent = _agent(llm)

    agent.run("fix it")
    usage = agent._session_metadata["context_usage"]

    assert set(usage["categories"]) == CATEGORY_KEYS
    assert sum(usage["categories"].values()) == 150
    for key in ("system", "user", "assistant", "tool_result"):
        assert usage["categories"][key] > 0
    assert usage["categories"]["summary_message"] == 0
    assert usage["categories"]["summary_call"] == 0
    assert usage["categories"]["conclude_instruction"] == 0
    assert usage["summary_calls"] == 0

    assert agent._session_metadata["input_tokens"] == 150
    assert agent._session_metadata["total_steps"] == 2
    assert llm.calls == 2


def test_missing_usage_leaves_all_categories_zero():
    agent = _agent(_FakeLLM([[_done({})]]), with_tool=False)

    agent.run("fix it")
    usage = agent._session_metadata["context_usage"]

    assert set(usage["categories"]) == CATEGORY_KEYS
    assert sum(usage["categories"].values()) == 0
    assert usage["tools"] == {}
    assert usage["summary_calls"] == 0
    assert agent._session_metadata["input_tokens"] == 0
