"""收尾与摘要调用的 usage 计入本次 run 的自检（假 LLM/假历史，不需要网络与 Docker）。"""
import json
from types import SimpleNamespace

from agents.agent.code_agent import CodeAgent
from agents.context.history import HistoryManager
from agents.core.config import Config
from agents.core.message import Message
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


class FakeSummaryLLM:
    """摘要专用 LLM 的替身：用来验证 HistoryManager 侧的 usage 记录。"""

    def __init__(self, usage):
        self.usage = usage

    def invoke(self, messages, **kwargs):
        return LLMResponse(content="摘要正文", model="summary-model", usage=dict(self.usage))


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


class StubHistory:
    """替掉 HistoryManager：只保留主循环用到的接口，摘要调用则由测试直接给 usage。"""

    def __init__(self, summary_usage=None):
        self.last_summary_usage = {}
        self.summary_usage = summary_usage
        self.summary_calls = 0

    def build_context(self, system_prompt):
        return []

    def append(self, message):
        return message

    def record_prompt_tokens(self, prompt_tokens):
        return None

    def add_summary(self):
        self.summary_calls += 1
        # None 模拟"没走大模型"，{} 模拟"调用了但供应商没回 usage"
        self.last_summary_usage = None if self.summary_usage is None else dict(self.summary_usage)
        return SimpleNamespace(id=1, replace=[0, 0], content="摘要")


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


def test_summary_usage_is_counted():
    """摘要调用同样计费，需计入总量；未触发摘要（None）时不重复计数。"""
    agent = _agent(max_steps=1)
    agent.history_manager = StubHistory(summary_usage=_usage(50, 5))
    agent.run("fix it")

    meta = _meta(agent)
    assert agent.history_manager.summary_calls == 1
    assert meta["input_tokens"] == 350  # 100 + 50 + 200
    assert meta["output_tokens"] == 35  # 10 + 5 + 20
    assert meta["total_tokens"] == 385


def test_summary_without_llm_call_is_not_counted():
    """历史管理器没走大模型（降级/未触发）时返回 None，不得把"没调用"记成"无 usage"。"""
    agent = _agent(max_steps=1)
    agent.history_manager = StubHistory(summary_usage=None)
    agent.run("fix it")

    meta = _meta(agent)
    assert meta["input_tokens"] == 300
    assert meta["cache_hit_available"] is True


def test_summary_call_without_usage_marks_cache_unavailable():
    """摘要调用了但没回 usage：缓存不可用（与"没调用"区分），token 记 0。"""
    agent = _agent(max_steps=1)
    agent.history_manager = StubHistory(summary_usage={})
    agent.run("fix it")

    meta = _meta(agent)
    assert agent.history_manager.summary_calls == 1
    assert meta["input_tokens"] == 300  # 摘要无 usage 不计 token，收尾照计入
    assert meta["cache_hit_available"] is False


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


def test_history_summary_usage_tristate():
    """HistoryManager 区分：未调用 None、调用无 usage {}、调用有 usage 记实际值。"""
    hm = HistoryManager(config=Config(), llm=SimpleNamespace(model="main"))
    assert hm.last_summary_usage is None

    hm._summary_llm = FakeSummaryLLM({})
    hm.summarize([Message("u", "user")])
    assert hm.last_summary_usage == {}

    hm._summary_llm = FakeSummaryLLM(_usage(50, 5, 7))
    hm.summarize([Message("u", "user")])
    assert hm.last_summary_usage["input_tokens"] == 50
    assert hm.last_summary_usage["cache_hit_tokens"] == 7
