"""工具响应协议

标准化的工具响应格式，提供结构化的状态、数据和错误信息。
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import json

class ToolStatus(Enum):
    """工具执行状态枚举"""
    SUCCESS = "success"  # 任务完全按预期执行
    PARTIAL = "partial"  # 结果可用但存在折扣（截断、回退、部分失败）
    ERROR = "error"  # 无有效结果（致命错误）


@dataclass
class ToolResponse:
    """工具响应数据类

    标准化的工具响应格式，包含：
    - status: 执行状态（success/partial/error）
    - text: 给 LLM 阅读的格式化文本
    - data: 结构化数据载荷
    - error_info: 错误信息（仅 status=error 时）
    - stats: 运行统计（时间、token等）
    - context: 上下文信息（参数、环境等）

    示例：
        >>> # 成功响应
        >>> resp = ToolResponse.success(
        ...     text="计算结果: 42",
        ...     data={"result": 42, "expression": "6*7"}
        ... )

        >>> # 错误响应
        >>> resp = ToolResponse.error(
        ...     code="INVALID_PARAM",
        ...     message="表达式不能为空"
        ... )
    """

    status: ToolStatus
    text: str
    data: Dict[str, Any] = field(default_factory=dict)
    """
    为什么不用 data: Dict[str, Any] = {}？因为 Python 的默认参数在函数定义时只求值一次，
    所有实例会共享同一个字典对象，导致相互污染。default_factory=dict 确保每个实例有独立的空字典，避免这个陷阱。
    """
    error_info: Optional[Dict[str, Any]] = None
    stats: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于序列化）"""
        result = {
            "status": self.status.value,
            "text": self.text,
            "data": self.data,
        }
        if self.error_info:
            result["error"] = self.error_info
        if self.stats:
            result["stats"] = self.stats
        if self.context:
            result["context"] = self.context
        return result

    def to_json(self) -> str:
        """ 转为JSON """
        """
        ensure_ascii=False — 不强制 ASCII 编码。默认为 True，会将中文等非 ASCII 字符转义；设为 False 则直接输出原始字符（可读性更好）
        indent=2 — 输出缩进 2 个空格，使 JSON 格式化显示（美观、易读）。默认为 None（紧凑输出，无换行缩进）
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "ToolResponse":
        """ 从字典创建 ToolResponse """
        status_str = data.get("status", "success")
        status = ToolStatus(status_str)

        return cls(
            status=status,
            text=data.get("text", ""),
            data=data.get("data", {}),
            error_info=data.get("error"),
            stats=data.get("stats"),
            context=data.get("context")
        )

    @classmethod
    def success(
        cls,
        text: str,
        data: Optional[Dict[str, Any]] = None,
        stats: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> 'ToolResponse':
        """快速创建成功响应

        Args:
            text: 给 LLM 阅读的文本
            data: 结构化数据
            stats: 运行统计
            context: 上下文信息
        """
        return cls(
            status=ToolStatus.SUCCESS,
            text=text,
            data=data or {},
            stats=stats,
            context=context
        )

    @classmethod
    def partial(
            cls,
            text: str,
            data: Optional[Dict[str, Any]] = None,
            stats: Optional[Dict[str, Any]] = None,
            context: Optional[Dict[str, Any]] = None
    ) -> 'ToolResponse':
        """快速创建部分成功响应

        Args:
            text: 给 LLM 阅读的文本（应说明部分成功的原因）
            data: 结构化数据
            stats: 运行统计
            context: 上下文信息
        """
        return cls(
            status=ToolStatus.PARTIAL,
            text=text,
            data=data or {},
            stats=stats,
            context=context
        )

    @classmethod
    def error(
            cls,
            code: str,
            message: str,
            stats: Optional[Dict[str, Any]] = None,
            context: Optional[Dict[str, Any]] = None
    ) -> 'ToolResponse':
        """快速创建错误响应

        Args:
            code: 错误码（来自 ToolErrorCode）
            message: 错误消息
            stats: 运行统计
            context: 上下文信息
        """
        return cls(
            status=ToolStatus.ERROR,
            text=message,
            data={},
            error_info={"code": code, "message": message},
            stats=stats,
            context=context
        )