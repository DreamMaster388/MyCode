"""LLM响应对象定义"""

from typing import Optional, Dict, List
from dataclasses import dataclass, field
from enum import Enum


@dataclass
class ToolCall:
    """统一的工具调用对象"""
    id: str
    name: str
    arguments: str

    def to_dict(self) -> Dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class LLMToolResponse:
    """统一的工具调用响应对象"""
    content: Optional[str]
    tool_calls: List[ToolCall]
    model: str
    usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    reasoning_content: Optional[str] = None  # 新增：思考/推理过程


@dataclass
class LLMResponse:
    """
    统一的LLM响应对象
    
    包含响应内容、推理过程（thinking model）、token使用统计、耗时等信息
    """

    content: str
    """ 回复内容 """

    model: str
    """ 实际使用的模型名称，方便进行成本核算 """

    usage: Dict[str, int] = field(default_factory=dict)
    """ Token使用统计：{"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150} """
    """ Python 的默认参数在定义时求值，usage: Dict[str, int] = {} 会让所有实例共享同一个空字典对象，
    一个实例修改 usage 会影响其他实例（可变对象陷阱）。
    default_factory=dict 确保每次 __init__ 时都调用 dict()
    """

    latency_ms: int = 0
    """ 调用耗时（ms）"""

    reasoning_content: Optional[str] = None
    """ 推理过程，只有思考模式有此字段 """

    def __str__(self):
        """ 向后兼容：直接打印返回content """
        return self.content

    def __repr__(self):
        """ 详细信息展示，给开发者看，调试模式可以看见详细信息 """
        parts = [
            f"LLMResponse(model={self.model})",
            f"latency_ms={self.latency_ms}",
        ]
        if self.reasoning_content:
            parts.append(f"reasoning_content={self.reasoning_content}")
        parts.append(f"content={self.content}")

        # ", ".join(parts) 是用 ", " 作为分隔符，把列表 parts 中的所有字符串拼接成一个字符串。
        return ", ".join(parts)

    def to_dict(self) -> Dict:
        """ 转为字典格式，方便日志记录 """
        result = {
            "content": self.content,
            "model": self.model,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
        }
        if self.reasoning_content:
            result["reasoning_content"] = self.reasoning_content

        return result


@dataclass
class StreamStats:
    """
    流式调用的统计信息
    
    在流式调用结束后可通过 llm.last_call_stats 获取

    流式场景中，content 已经被消费掉了，剩的只有 metadata

    LLMResponse 表示"一次完整调用结果"，而 StreamStats 是流结束后的残留统计
    """

    model: str
    """实际使用的模型名称"""

    usage: Dict[str, int] = field(default_factory=dict)
    """Token使用统计"""

    latency_ms: int = 0
    """调用耗时（毫秒）"""

    reasoning_content: Optional[str] = None
    """推理过程（仅thinking model）"""

    def to_dict(self) -> Dict:
        """转换为字典格式"""
        result = {
            "model": self.model,
            "usage": self.usage,
            "latency_ms": self.latency_ms,
        }
        if self.reasoning_content:
            result["reasoning_content"] = self.reasoning_content
        return result


class LLMStreamChunkType(str, Enum):
    """流式切片类型"""
    CONTENT = "content"  # 正文增量
    THINKING = "thinking"  # 思考/推理过程增量
    TOOL_CALL = "tool_call"  # 工具调用增量
    DONE = "done"  # 一轮结束：tool_calls 汇总 + usage + 累计 reasoning


@dataclass
class LLMStreamChunk:
    """流式调用返回的单个切片

    一次模型调用会吐出一串这类对象：
    - THINKING: 思考过程增量（仅 thinking model 有）
    - CONTENT:  正文增量
    - TOOL_CALL: 工具调用增量（可选，逐段渲染）
    - DONE:      一轮结束，包含 tool_calls 汇总 + usage + reasoning_content
    """

    type: LLMStreamChunkType
    text: str = ""
    tool_calls: Optional[List[ToolCall]] = None
    usage: Dict[str, int] = field(default_factory=dict)
    reasoning_content: Optional[str] = None
    finish_reason: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "type": self.type.value,
            "text": self.text,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls] if self.tool_calls else None,
            "usage": self.usage,
            "reasoning_content": self.reasoning_content,
            "finish_reason": self.finish_reason,
        }
