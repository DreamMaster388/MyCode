"""验收测试：CodeAgent 在 stream_run 中为六项指标累计数据源（spec R1/R3/R5/R8）。

独立说明：本文件按 `docs/sdlc/feat/eval-add-metrics/spec.md` 推导断言，
用假 LLM 驱动真实 CodeAgent 主循环；不联网、不调用真实模型。
"""
from __future__ import annotations

from agents.agent.code_agent import CodeAgent
from agents.core.config import Config
from agents.core.llm_response import LLMStreamChunk, LLMStreamChunkType, ToolCall
from agents.tools.registry import ToolRegistry


# ------------------------------------------------------------------ 辅助 --

def _usage(input_tokens, output_tokens, cached_tokens, available=True):
    """适配器解析后的标准 usage 形状。"""
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_hit_tokens": cached_tokens,
        "cache_hit_available": available,
    }


def _done(usage, tool_calls=None):
    return LLMStreamChunk(
        type=LLMStreamChunkType.DONE,
        text="",
        tool_calls=tool_calls,
        usage=usage,
    )


def _tool_call_round(usage):
    return [_done(usage, tool_calls=[ToolCall(id="call-1", name="Bash", arguments="{}")])]


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
        raise AssertionError("本用例不应触发 _conclude")

    def invoke(self, *args, **kwargs):
        raise AssertionError("本用例不应触发 _conclude")


def _config():
    return Config(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
    )


def _agent(rounds, max_steps=6):
    return CodeAgent(
        name="acceptance",
        llm=_FakeLLM(rounds),
        config=_config(),
        tool_registry=ToolRegistry(),
        max_steps=max_steps,
    )


def _run(agent):
    events = list(agent.stream_run("fix it"))
    return events, agent._session_metadata


# =================================================================== R1 ====
# stream_run 每轮消费 usage，累计输入/输出/缓存进会话元数据。

def test_r1_stream_run_accumulates_input_output_cached_tokens():
    agent = _agent(
        [
            _tool_call_round(_usage(100, 20, 40)),
            [_done(_usage(50, 10, 0))],
        ]
    )

    events, meta = _run(agent)

    assert meta["input_tokens"] == 150
    assert meta["output_tokens"] == 30
    assert meta["cached_tokens"] == 40
    assert meta["cache_hit_available"] is True
    assert events[-1].type.value == "agent_finish"


def test_r1_no_usage_rounds_keep_metrics_zero():
    agent = _agent([[_done({})]])

    _, meta = _run(agent)

    assert meta["input_tokens"] == 0
    assert meta["output_tokens"] == 0
    assert meta["cached_tokens"] == 0
    assert meta["total_tokens"] == 0


# =================================================================== R5 ====
# total_steps 每轮 step 计一次；请求次数复用它。

def test_r5_total_steps_counts_each_llm_round():
    llm = _FakeLLM(
        [
            _tool_call_round(_usage(100, 20, 40)),
            [_done(_usage(50, 10, 0))],
        ]
    )
    agent = CodeAgent(
        name="acceptance",
        llm=llm,
        config=_config(),
        tool_registry=ToolRegistry(),
        max_steps=6,
    )

    _, meta = _run(agent)

    assert llm.calls == 2
    assert meta["total_steps"] == 2


# =================================================================== R8 ====
# total_tokens 仍为输入+输出。

def test_r8_total_tokens_equals_input_plus_output():
    agent = _agent(
        [
            _tool_call_round(_usage(100, 20, 40)),
            [_done(_usage(50, 10, 0))],
        ]
    )

    _, meta = _run(agent)

    assert meta["total_tokens"] == meta["input_tokens"] + meta["output_tokens"]


# =================================================================== R3 ====
# 缓存字段缺失：命中记 0，可用性标记为否。

def test_r3_missing_cache_fields_record_zero_and_unavailable():
    usage_without_cache = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "input_tokens": 100,
        "output_tokens": 20,
    }
    agent = _agent([[_done(usage_without_cache)]])

    _, meta = _run(agent)

    assert meta["cached_tokens"] == 0
    assert meta["cache_hit_available"] is False


def test_r3_one_round_reports_cache_unavailable():
    agent = _agent(
        [
            _tool_call_round(_usage(100, 20, 40, available=False)),
            [_done(_usage(50, 10, 0))],
        ]
    )

    _, meta = _run(agent)

    assert meta["cache_hit_available"] is False


# ============================================================ 流式降级 ====
# 末个 chunk 不带 usage：按 0 计，且缓存可用性置否，不中断运行。

def test_stream_usage_missing_degrades_to_zero_and_unavailable():
    agent = _agent([[_done({})]])

    _, meta = _run(agent)

    assert meta["total_steps"] == 1
    assert meta["input_tokens"] == 0
    assert meta["output_tokens"] == 0
    assert meta["cached_tokens"] == 0
    assert meta["cache_hit_available"] is False


def test_any_round_without_usage_marks_cache_unavailable():
    # 第二轮没有任何 usage：无法证明缓存全程可用，标记必须为否（plan 风险项）。
    agent = _agent(
        [
            _tool_call_round(_usage(100, 20, 40)),
            [_done({})],
        ]
    )

    _, meta = _run(agent)

    assert meta["total_steps"] == 2
    assert meta["cache_hit_available"] is False
