"""验收测试：LLM usage 解析、缓存命中采集与流式降级（spec R1 数据源 / R2 / R3 / R8）。

独立说明：本文件按 `docs/sdlc/feat/eval-add-metrics/spec.md` 的 R2/R3（以及
R1 的 usage 数据来源、R8 的旧 usage 键语义）推导断言；不修改实现与既有测试。

全部用例在宿主机运行：用 SimpleNamespace 伪造 OpenAI SDK 的响应对象与流式
chunk，不联网、不调用真实模型。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.core.llm_adapters import OpenAIAdapter, _parse_usage
from agents.core.llm_response import LLMStreamChunkType

_MISSING = object()


# ------------------------------------------------------------------ 构造 --

def _raw(prompt=100, completion=50, total=150, **extra):
    raw = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }
    raw.update(extra)
    return raw


def _usage_obj(prompt=100, completion=50, total=150, cache_hit=40):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        prompt_cache_hit_tokens=cache_hit,
    )


class _FakeCompletions:
    def __init__(self, payload):
        self._payload = payload

    def create(self, **kwargs):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.chat = SimpleNamespace(completions=_FakeCompletions(payload))


def _adapter(payload):
    adapter = OpenAIAdapter(
        api_key="acceptance-key",
        base_url="http://localhost:9/v1",
        timeout=1,
        model="fake-model",
    )
    adapter._client = _FakeClient(payload)
    return adapter


def _chunk(content=None, usage=_MISSING, finish_reason=None):
    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=None),
                finish_reason=finish_reason,
            )
        ]
    )
    if usage is not _MISSING:
        chunk.usage = usage
    return chunk


def _completion(content="ok", usage=_MISSING):
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=None)
            )
        ],
        model="fake-model",
    )
    if usage is not _MISSING:
        response.usage = usage
    return response


# =================================================================== R2 ====
# 缓存命中优先 prompt_cache_hit_tokens，回退 prompt_tokens_details.cached_tokens。

def test_r2_reads_deepseek_prompt_cache_hit_tokens():
    usage = _parse_usage(_raw(prompt_cache_hit_tokens=40))

    assert usage["cache_hit_tokens"] == 40
    assert usage["cache_hit_available"] is True


def test_r2_falls_back_to_openai_compatible_cached_tokens():
    usage = _parse_usage(_raw(prompt_tokens_details={"cached_tokens": 30}))

    assert usage["cache_hit_tokens"] == 30
    assert usage["cache_hit_available"] is True


def test_r2_deepseek_field_wins_over_openai_details():
    usage = _parse_usage(
        _raw(
            prompt_cache_hit_tokens=40,
            prompt_tokens_details={"cached_tokens": 70},
        )
    )

    assert usage["cache_hit_tokens"] == 40


def test_r2_supports_sdk_object_attribute_style():
    raw = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        prompt_cache_hit_tokens=None,
        prompt_tokens_details=SimpleNamespace(cached_tokens=25),
    )

    usage = _parse_usage(raw)

    assert usage["cache_hit_tokens"] == 25
    assert usage["cache_hit_available"] is True


def test_r2_non_stream_calls_use_shared_parser():
    for method, args in (
        ("invoke", ()),
        ("invoke_with_tools", ([],)),
    ):
        adapter = _adapter(_completion(usage=_usage_obj()))
        result = getattr(adapter, method)([{"role": "user", "content": "hi"}], *args)

        assert result.usage["input_tokens"] == 100
        assert result.usage["output_tokens"] == 50
        assert result.usage["cache_hit_tokens"] == 40
        assert result.usage["cache_hit_available"] is True


def test_r2_legacy_stream_invoke_uses_shared_parser():
    adapter = _adapter([_chunk("hi"), _chunk(usage=_usage_obj())])

    list(adapter.stream_invoke([{"role": "user", "content": "hi"}]))

    usage = adapter.last_status.usage
    assert usage["input_tokens"] == 100
    assert usage["cache_hit_tokens"] == 40
    assert usage["cache_hit_available"] is True


def test_r2_stream_invoke_with_tools_done_chunk_carries_new_fields():
    adapter = _adapter([_chunk("hi"), _chunk(usage=_usage_obj())])

    chunks = list(
        adapter.stream_invoke_with_tools([{"role": "user", "content": "hi"}], [])
    )
    done = chunks[-1]

    assert done.type == LLMStreamChunkType.DONE
    assert done.usage["input_tokens"] == 100
    assert done.usage["output_tokens"] == 50
    assert done.usage["cache_hit_tokens"] == 40
    assert done.usage["cache_hit_available"] is True


# =================================================================== R3 ====
# 无缓存字段：记 0 且标记不可用；真实的 0 命中必须与"不可用"可区分。

@pytest.mark.parametrize(
    "raw",
    [
        _raw(),
        _raw(prompt_tokens_details={}),
        _raw(prompt_cache_hit_tokens=None),
        _raw(prompt_tokens_details={"cached_tokens": None}),
    ],
    ids=["no-cache-field", "empty-details", "deepseek-none", "cached-none"],
)
def test_r3_missing_cache_field_records_zero_and_marks_unavailable(raw):
    usage = _parse_usage(raw)

    assert usage["cache_hit_tokens"] == 0
    assert usage["cache_hit_available"] is False


def test_r3_measured_zero_hit_is_available_not_missing():
    for raw in (
        _raw(prompt_cache_hit_tokens=0),
        _raw(prompt_tokens_details={"cached_tokens": 0}),
    ):
        usage = _parse_usage(raw)

        assert usage["cache_hit_tokens"] == 0
        assert usage["cache_hit_available"] is True


def test_r3_empty_usage_degrades_to_empty_dict():
    for raw in (None, {}, 0):
        assert _parse_usage(raw) == {}


# =================================================================== R8 ====
# usage 旧键（prompt/completion/total_tokens）保留，语义不变。

def test_r8_legacy_usage_keys_preserved():
    usage = _parse_usage(_raw(prompt_cache_hit_tokens=40))

    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 50
    assert usage["total_tokens"] == 150
    assert usage["input_tokens"] == usage["prompt_tokens"]
    assert usage["output_tokens"] == usage["completion_tokens"]


def test_r8_total_tokens_falls_back_to_input_plus_output():
    usage = _parse_usage({"prompt_tokens": 7, "completion_tokens": 3})

    assert usage["total_tokens"] == 10


# ============================================================ 流式降级 ====
# 响应 usage 缺失 / 末 chunk 无 usage：不抛异常，usage 降级为空（调用方按 0 计）。

def test_stream_invoke_without_usage_does_not_break_and_reports_empty():
    # chunk 带 usage=None 以及完全没有 usage 属性两种 SDK 形态。
    adapter = _adapter([_chunk("hi", usage=None), _chunk("there")])

    list(adapter.stream_invoke([{"role": "user", "content": "hi"}]))

    assert adapter.last_status.usage == {}


def test_stream_invoke_with_tools_last_chunk_without_usage_yields_empty_usage():
    adapter = _adapter([_chunk("hi", usage=None), _chunk("done")])

    chunks = list(
        adapter.stream_invoke_with_tools([{"role": "user", "content": "hi"}], [])
    )

    assert chunks[-1].type == LLMStreamChunkType.DONE
    assert chunks[-1].usage == {}


def test_invoke_without_usage_reports_empty_usage():
    adapter = _adapter(_completion())

    response = adapter.invoke([{"role": "user", "content": "hi"}])

    assert response.usage == {}
