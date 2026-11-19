"""Ticket 03 验收：summary/收尾类别与摘要调用次数。

用假 LLM 同时提供摘要 invoke 与主循环流式返回；不联网、不调用真实模型。
"""
from __future__ import annotations

import json

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


class _SummaryLLM:
    model = "fake-model"

    def __init__(
        self,
        summary_usage=None,
        summary_error=None,
        main_usage=None,
        main_tool_calls=None,
        conclude_usage=None,
    ):
        self.summary_usage = {} if summary_usage is None else summary_usage
        self.summary_error = summary_error
        self.main_usage = main_usage or _usage(100)
        self.main_tool_calls = main_tool_calls
        self.conclude_usage = conclude_usage or _usage(50)
        self.summary_calls = 0

    def invoke(self, messages, **kwargs):
        self.summary_calls += 1
        if self.summary_error is not None:
            raise self.summary_error
        return LLMResponse(
            content="摘要正文",
            model=self.model,
            usage=dict(self.summary_usage),
        )

    def stream_invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        yield _done(self.main_usage, tool_calls=self.main_tool_calls)

    def invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        return LLMResponse(
            content="concluded",
            model=self.model,
            usage=dict(self.conclude_usage),
        )


class _NoopTool(Tool):
    def __init__(self):
        super().__init__("Noop", "noop")

    def run(self, parameters):
        return ToolResponse.success("ok")

    def get_parameters(self):
        return []


def _config(**overrides) -> Config:
    values = dict(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )
    values.update(overrides)
    return Config(**values)


def _summary_config() -> Config:
    return _config(
        context_summary_enabled=True,
        context_window=100,
        compression_threshold=0.5,
        context_summary_keep_rounds=2,
    )


def _agent(llm, config: Config, max_steps: int = 3) -> CodeAgent:
    registry = ToolRegistry()
    registry.register_tool(_NoopTool())
    return CodeAgent(
        name="summary-usage",
        llm=llm,
        config=config,
        tool_registry=registry,
        system_prompt="SYS",
        max_steps=max_steps,
    )


def _seed_history(agent: CodeAgent) -> None:
    for content, role in (
        ("u0", "user"),
        ("a1", "assistant"),
        ("u2", "user"),
        ("a3", "assistant"),
        ("u4", "user"),
        ("a5", "assistant"),
    ):
        agent.history_manager.append(Message(content, role))


def test_summary_message_and_call_are_separate_categories():
    llm = _SummaryLLM(summary_usage=_usage(40), main_usage=_usage(100))
    agent = _agent(llm, _summary_config())
    _seed_history(agent)
    agent.context_builder.record_prompt_tokens(51)

    agent.run("fix it")
    usage = agent._session_metadata["context_usage"]

    assert usage["summary_calls"] == 1
    assert usage["categories"]["summary_call"] == 40
    assert usage["categories"]["summary_message"] > 0
    assert sum(usage["categories"].values()) == 140
    assert agent._session_metadata["input_tokens"] == 140
    assert agent._session_metadata["total_steps"] == 1


def test_trigger_without_real_summary_call_counts_but_costs_zero():
    llm = _SummaryLLM(summary_error=RuntimeError("boom"), main_usage=_usage(100))
    agent = _agent(llm, _summary_config())
    _seed_history(agent)
    agent.context_builder.record_prompt_tokens(51)

    agent.run("fix it")
    usage = agent._session_metadata["context_usage"]

    assert usage["summary_calls"] == 1
    assert usage["categories"]["summary_call"] == 0
    assert usage["categories"]["summary_message"] > 0
    assert sum(usage["categories"].values()) == 100
    assert agent._session_metadata["input_tokens"] == 100


def test_conclude_instruction_is_its_own_category():
    llm = _SummaryLLM(
        main_usage=_usage(100),
        main_tool_calls=[ToolCall(id="c1", name="Noop", arguments="{}")],
        conclude_usage=_usage(50),
    )
    agent = _agent(llm, _config(), max_steps=1)

    agent.run("fix it")
    usage = agent._session_metadata["context_usage"]

    assert usage["summary_calls"] == 0
    assert usage["categories"]["conclude_instruction"] > 0
    assert sum(usage["categories"].values()) == 150
    assert agent._session_metadata["input_tokens"] == 150
    assert agent._session_metadata["total_steps"] == 1
    assert usage["tools"]["Noop"]["calls"] == 1


def test_summary_triggered_by_conclude_build_is_counted():
    llm = _SummaryLLM(
        summary_usage=_usage(40),
        main_usage=_usage(100),
        main_tool_calls=[ToolCall(id="c1", name="Noop", arguments="x" * 400)],
        conclude_usage=_usage(50),
    )
    agent = _agent(llm, _summary_config(), max_steps=1)
    _seed_history(agent)

    agent.run("fix it")
    usage = agent._session_metadata["context_usage"]

    assert llm.summary_calls == 1
    assert usage["summary_calls"] == 1
    assert usage["categories"]["summary_call"] == 40
    assert sum(usage["categories"].values()) == 190
    assert agent._session_metadata["input_tokens"] == 190
