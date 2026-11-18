"""ContextUsageTracker - 把每次请求的真实输入 token 按类别归集。

只处理输入 token；类别合计在拿到真实 prompt_tokens 后按长度份额精确分配。
工具区是同一批输入 token 的第二视角，按工具名合并参数与结果，不与类别区相加。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

CATEGORY_KEYS = (
    "system",
    "tool_schema",
    "user",
    "assistant",
    "tool_result",
    "summary_message",
    "summary_call",
    "conclude_instruction",
)


def _payload_length(value: Any) -> int:
    """按实际发送 payload 的 JSON 字符串长度计量，避免把内部字段算进去。"""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _prompt_tokens(usage: Optional[Dict[str, Any]]) -> int:
    if not usage:
        return 0
    value = usage.get("prompt_tokens")
    if not isinstance(value, int):
        value = usage.get("input_tokens")
    return value if isinstance(value, int) and value > 0 else 0


def _category_from_message(message: Dict[str, Any]) -> str:
    role = message.get("role")
    if role == "system":
        return "system"
    if role == "assistant":
        return "assistant"
    if role == "tool":
        return "tool_result"
    if role == "summary":
        return "summary_message"
    return "user"


def _allocate(total: int, lengths: Sequence[int]) -> List[int]:
    """最大余数法：整数分配之和严格等于 total。"""
    if total <= 0 or not lengths:
        return [0] * len(lengths)
    denominator = sum(lengths)
    if denominator <= 0:
        return [0] * len(lengths)

    raw = [total * length / denominator for length in lengths]
    allocated = [int(value) for value in raw]
    remaining = total - sum(allocated)
    order = sorted(
        range(len(lengths)),
        key=lambda index: (raw[index] - allocated[index], lengths[index]),
        reverse=True,
    )
    for index in order[:remaining]:
        allocated[index] += 1
    return allocated


@dataclass
class _Segment:
    category: str
    length: int


@dataclass
class _ToolSegment:
    tool_name: str
    length: int


@dataclass
class _PendingRequest:
    segments: List[_Segment]
    tool_segments: List[_ToolSegment]
    total_length: int


class ContextUsageTracker:
    """一次 run 的输入 token 归集器。"""

    def __init__(self) -> None:
        self._categories: Dict[str, int] = {key: 0 for key in CATEGORY_KEYS}
        self._tools: Dict[str, Dict[str, int]] = {}
        self._summary_calls = 0
        self._pending: Optional[_PendingRequest] = None
        self._call_names: Dict[str, str] = {}

    def begin_request(
        self,
        messages: List[Dict[str, Any]],
        labels: List[str],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """记录一次主循环/收尾请求的类别段、长度与工具第二视角。"""
        self._pending = None
        segments: List[_Segment] = []
        tool_segments: List[_ToolSegment] = []
        if tools:
            segments.append(_Segment("tool_schema", _payload_length(tools)))

        # 先登记 tool_call_id -> 工具名，供 tool 结果按 id 归名。
        for message in messages:
            for tool_call in message.get("tool_calls") or []:
                call_id = tool_call.get("id")
                function = tool_call.get("function") or {}
                name = function.get("name") or "unknown"
                if call_id:
                    self._call_names[call_id] = name

        for index, message in enumerate(messages):
            label = labels[index] if index < len(labels) else None
            category = label if label in CATEGORY_KEYS else _category_from_message(message)
            length = _payload_length(message)
            segments.append(_Segment(category, length))

            if category == "assistant":
                calls = message.get("tool_calls") or []
                if calls:
                    raw_lengths = [_payload_length(call) for call in calls]
                    raw_total = sum(raw_lengths)
                    if raw_total > length and raw_total > 0:
                        lengths = [int(item * length / raw_total) for item in raw_lengths]
                    else:
                        lengths = raw_lengths
                    for tool_call, call_length in zip(calls, lengths):
                        function = tool_call.get("function") or {}
                        name = function.get("name") or "unknown"
                        tool_segments.append(_ToolSegment(name, call_length))
            elif category == "tool_result":
                call_id = message.get("tool_call_id")
                name = self._call_names.get(call_id) or "unknown"
                tool_segments.append(_ToolSegment(name, length))

        self._pending = _PendingRequest(
            segments=segments,
            tool_segments=tool_segments,
            total_length=sum(segment.length for segment in segments),
        )

    def commit_request(self, prompt_tokens: Optional[int]) -> None:
        """用真实输入 token 按长度份额落账；无 usage 时只清空待处理请求。"""
        pending = self._pending
        self._pending = None
        if pending is None or not isinstance(prompt_tokens, int) or prompt_tokens <= 0:
            return
        if pending.total_length <= 0:
            return

        allocations = _allocate(
            prompt_tokens, [segment.length for segment in pending.segments]
        )
        for segment, tokens in zip(pending.segments, allocations):
            self._categories[segment.category] += tokens

        for segment in pending.tool_segments:
            if segment.length <= 0:
                continue
            tokens = int(prompt_tokens * segment.length / pending.total_length)
            if tokens:
                entry = self._tools.setdefault(
                    segment.tool_name, {"input_tokens": 0, "calls": 0}
                )
                entry["input_tokens"] += tokens

    def count_tool_call(self, name: str) -> None:
        """模型每发出一个 tool_call 计一次；含未知工具与参数解析失败。"""
        tool_name = name or "unknown"
        entry = self._tools.setdefault(tool_name, {"input_tokens": 0, "calls": 0})
        entry["calls"] += 1

    def abort_request(self) -> None:
        """请求失败且没有真实 usage 时丢弃待处理段。"""
        self._pending = None

    def record_summary_call(self, usage: Optional[Dict[str, Any]]) -> None:
        """摘要触发即计数；有真实输入 token 时整次归入 summary_call。"""
        self._summary_calls += 1
        prompt_tokens = _prompt_tokens(usage)
        if prompt_tokens:
            self._categories["summary_call"] += prompt_tokens

    def to_dict(self) -> Dict[str, Any]:
        """输出 run 元数据中的 context_usage；固定形状，便于 eval/前端消费。"""
        return {
            "categories": dict(self._categories),
            "tools": {
                name: dict(entry) for name, entry in self._tools.items()
            },
            "summary_calls": self._summary_calls,
        }
