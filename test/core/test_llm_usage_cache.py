"""usage 解析与计价单测：全部在宿主机跑，不需要真实模型或 Docker。"""
from types import SimpleNamespace

from agents.core.llm_adapters import _parse_usage
from eval.container_runner import estimate_cost


def test_cache_hit_fallback():
    """DeepSeek 优先、OpenAI 兼容格式回退、缺失记 0 且标记不可用。"""
    deepseek = SimpleNamespace(
        prompt_tokens=1000, completion_tokens=200, total_tokens=1200,
        prompt_cache_hit_tokens=400,
    )
    usage = _parse_usage(deepseek)
    assert usage["input_tokens"] == 1000
    assert usage["output_tokens"] == 200
    assert usage["cache_hit_tokens"] == 400
    assert usage["cache_hit_available"] is True
    # 旧键保持，调用方不用改
    assert usage["prompt_tokens"] == 1000
    assert usage["total_tokens"] == 1200

    openai_style = {
        "prompt_tokens": 800, "completion_tokens": 100, "total_tokens": 900,
        "prompt_tokens_details": {"cached_tokens": 300},
    }
    usage = _parse_usage(openai_style)
    assert usage["cache_hit_tokens"] == 300
    assert usage["cache_hit_available"] is True

    missing = SimpleNamespace(prompt_tokens=500, completion_tokens=50, total_tokens=550)
    usage = _parse_usage(missing)
    assert usage["cache_hit_tokens"] == 0
    assert usage["cache_hit_available"] is False

    assert _parse_usage(None) == {}
    assert _parse_usage({}) == {}


def test_cost_formula():
    """未命中输入 × 2 + 缓存 × 0.04 + 输出 × 8，单位元/百万 token。"""
    cost = estimate_cost(1000, 200, 400)
    expected = (600 * 2 + 400 * 0.04 + 200 * 8) / 1_000_000
    assert abs(cost - expected) < 1e-12
    assert abs(cost - 0.002816) < 1e-12

    # 缓存命中数大于输入时，未命中输入按 0 截断，不出现负价。
    assert abs(estimate_cost(100, 0, 300) - (300 * 0.04) / 1_000_000) < 1e-12
    # 空用量为 0。
    assert estimate_cost(0, 0, 0) == 0.0
