"""Context usage 汇总与终端格式化。

输入是每实例结果（含 context_usage），输出 totals/averages 与可直接打印的行；
本地 eval 与容器 eval 共用，避免明细与汇总口径分叉。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from agents.context.usage import CATEGORY_KEYS


def empty_context_usage() -> Dict[str, Any]:
    """固定形状的零值，供早退/无用量实例使用。"""
    return {
        "categories": {key: 0 for key in CATEGORY_KEYS},
        "tools": {},
        "summary_calls": 0,
    }


def _ensure_usage(raw: Any) -> Dict[str, Any]:
    usage = empty_context_usage()
    if not isinstance(raw, dict):
        return usage
    categories = raw.get("categories")
    if isinstance(categories, dict):
        for key in CATEGORY_KEYS:
            value = categories.get(key, 0)
            usage["categories"][key] = value if isinstance(value, int) else 0
    tools = raw.get("tools")
    if isinstance(tools, dict):
        for name, item in tools.items():
            if not isinstance(item, dict):
                continue
            usage["tools"][str(name)] = {
                "input_tokens": int(item.get("input_tokens", 0) or 0),
                "calls": int(item.get("calls", 0) or 0),
            }
    summary_calls = raw.get("summary_calls", 0)
    usage["summary_calls"] = summary_calls if isinstance(summary_calls, int) else 0
    return usage


def summarize_context_usage(results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """对每实例明细求和/取均值；分母为全部实例，早退实例按 0 计。"""
    records = list(results)
    count = len(records)
    totals = {
        "categories": {key: 0 for key in CATEGORY_KEYS},
        "tools": {},
        "summary_calls": 0,
    }

    for record in records:
        usage = _ensure_usage(record.get("context_usage"))
        for key in CATEGORY_KEYS:
            totals["categories"][key] += usage["categories"][key]
        for name, item in usage["tools"].items():
            entry = totals["tools"].setdefault(
                name, {"input_tokens": 0, "calls": 0}
            )
            entry["input_tokens"] += item["input_tokens"]
            entry["calls"] += item["calls"]
        totals["summary_calls"] += usage["summary_calls"]

    denominator = count if count else 1
    averages = {
        "categories": {
            key: totals["categories"][key] / denominator for key in CATEGORY_KEYS
        },
        "tools": {
            name: {
                "input_tokens": item["input_tokens"] / denominator,
                "calls": item["calls"] / denominator,
            }
            for name, item in totals["tools"].items()
        },
        "summary_calls": totals["summary_calls"] / denominator if count else 0.0,
    }
    return {"count": count, "totals": totals, "averages": averages}


def format_context_usage(summary: Dict[str, Any]) -> List[str]:
    """生成终端可打印的类别/工具/摘要汇总行。"""
    totals = summary["totals"]
    averages = summary["averages"]
    lines = ["输入 token 类别（总量/均值）:"]
    for key in CATEGORY_KEYS:
        lines.append(
            f"  {key}: {totals['categories'][key]} / "
            f"{averages['categories'][key]:.1f}"
        )
    if totals["tools"]:
        lines.append("工具（调用次数/输入 token）:")
        for name in sorted(totals["tools"]):
            item = totals["tools"][name]
            lines.append(f"  {name}: {item['calls']} / {item['input_tokens']}")
    lines.append(
        f"摘要调用次数: {totals['summary_calls']} / {averages['summary_calls']:.1f}"
    )
    return lines
