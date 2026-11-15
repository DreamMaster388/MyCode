"""收尾调用的 usage 计入本次 run 的自检（假 LLM，不需要网络与 Docker）。"""
import json

from agents.agent.code_agent import CodeAgent
from agents.core.config import Config
from agents.core.llm_response import LLMResponse, LLMStreamChunk, LLMStreamChunkType, ToolCall
from agents.tools.registry import ToolRegistry


def _usage(prompt, completion, cache=0, available=True):
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "input_tokens": prompt,
        "output_tokens": completion,
        "cache_hit_tokens": cache,
        "cache_hit_available": available,
    }


class FakeLLM:
    """一轮带工具调用把循环撑满，收尾走 invoke_with_tools。"""

    model = "fake-model"

    def __init__(self):
        self.stream_usage = _usage(100, 10, 40)
        self.conclude_usage = _usage(200, 20, 0)
        self.conclude_calls = 0

    def stream_invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        yield LLMStreamChunk(
            type=LLMStreamChunkType.DONE,
            tool_calls=[ToolCall(id="c1", name="Bash", arguments=json.dumps({"command": "ls"}))],
            usage=dict(self.stream_usage),
        )

    def invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        self.conclude_calls += 1
        return LLMResponse(content="收尾结论", model=self.model, usage=dict(self.conclude_usage))

    def invoke(self, messages, **kwargs):
        self.conclude_calls += 1
        return LLMResponse(content="降级结论", model=self.model, usage=dict(self.conclude_usage))


def _agent(max_steps=1):
    return CodeAgent(
        name="UsageCheck",
        llm=FakeLLM(),
        tool_registry=ToolRegistry(),
        max_steps=max_steps,
        config=Config(trace_enabled=False, skills_enabled=False, subagent_enabled=False,
                      todowrite_enabled=False, devlog_enabled=False, session_enabled=False),
    )


def _meta(agent):
    return agent._session_metadata


def test_conclude_usage_is_counted():
    """max_steps 触顶时收尾调用是真实花费，必须计入总量；tokens 仍等于输入+输出。"""
    agent = _agent(max_steps=1)
    agent.run("fix it")

    meta = _meta(agent)
    assert meta["total_steps"] == 1
    assert agent.llm.conclude_calls == 1
    assert meta["input_tokens"] == 300  # 100（主循环）+ 200（收尾）
    assert meta["output_tokens"] == 30  # 10 + 20
    assert meta["total_tokens"] == meta["input_tokens"] + meta["output_tokens"] == 330
    assert meta["cached_tokens"] == 40
    assert meta["cache_hit_available"] is True


def test_conclude_failure_does_not_flip_cache_marker():
    """主/降级收尾通道都失败（无真实调用）时，不应把缓存可用性翻成不可用。"""

    class FailingConcludeLLM(FakeLLM):
        def invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
            raise RuntimeError("boom")

        def invoke(self, messages, **kwargs):
            raise RuntimeError("boom")

    agent = _agent(max_steps=1)
    agent.llm = FailingConcludeLLM()
    agent.run("fix it")

    meta = _meta(agent)
    assert meta["input_tokens"] == 100
    assert meta["total_tokens"] == 110
    assert meta["cache_hit_available"] is True
