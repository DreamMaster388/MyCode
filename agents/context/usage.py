"""ContextUsageTracker - 把每次请求的真实输入 token 按类别归集。

只处理输入 token；类别合计在拿到真实 prompt_tokens 后按长度份额精确分配。
工具区是同一批输入 token 的第二视角：从类别分配值里切出参数与结果份额，
按工具名汇总，不与类别区相加。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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


def _arguments_length(function: Dict[str, Any]) -> int:
    """工具参数的发送长度：字符串按字符数，其余按序列化 JSON 计。"""
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        return len(arguments)
    return _payload_length(arguments) if arguments is not None else 0


@dataclass
class _ToolCall:
    tool_name: str
    arguments_length: int


@dataclass
class _Segment:
    category: str
    length: int
    tool_calls: List[_ToolCall] = field(default_factory=list)
    tool_result_name: Optional[str] = None


@dataclass
class _PendingRequest:
    segments: List[_Segment]


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
        """记录一次主循环/收尾请求的类别段与各类别内的工具调用。"""
        self._pending = None
        segments: List[_Segment] = []
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
            segment = _Segment(category, _payload_length(message))
            if category == "assistant":
                for tool_call in message.get("tool_calls") or []:
                    function = tool_call.get("function") or {}
                    segment.tool_calls.append(
                        _ToolCall(
                            tool_name=function.get("name") or "unknown",
                            arguments_length=_arguments_length(function),
                        )
                    )
            elif category == "tool_result":
                call_id = message.get("tool_call_id")
                segment.tool_result_name = self._call_names.get(call_id) or "unknown"
            segments.append(segment)

        self._pending = _PendingRequest(segments=segments)

    def commit_request(self, prompt_tokens: Optional[int]) -> None:
        """用真实输入 token 按长度份额落账；无 usage 时只清空待处理请求。"""
        pending = self._pending
        self._pending = None
        if pending is None or not isinstance(prompt_tokens, int) or prompt_tokens <= 0:
            return

        allocations = _allocate(
            prompt_tokens, [segment.length for segment in pending.segments]
        )
        for segment, tokens in zip(pending.segments, allocations):
            self._categories[segment.category] += tokens
            if segment.category == "assistant":
                self._attribute_tool_parameters(segment, tokens)
            elif segment.category == "tool_result" and segment.tool_result_name:
                self._add_tool_tokens(segment.tool_result_name, tokens)

    def _attribute_tool_parameters(self, segment: _Segment, tokens: int) -> None:
        """从 assistant 段的分配值里切出参数份额，按参数长度分给各工具。"""
        lengths = [call.arguments_length for call in segment.tool_calls]
        total_length = sum(lengths)
        if tokens <= 0 or total_length <= 0 or segment.length <= 0:
            return
        parameter_tokens = int(tokens * total_length / segment.length)
        for call, share in zip(segment.tool_calls, _allocate(parameter_tokens, lengths)):
            self._add_tool_tokens(call.tool_name, share)

    def _add_tool_tokens(self, tool_name: str, tokens: int) -> None:
        if tokens <= 0:
            return
        entry = self._tools.setdefault(
            tool_name or "unknown", {"input_tokens": 0, "calls": 0}
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
