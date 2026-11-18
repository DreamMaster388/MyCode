"""Recall 工具 - 按历史下标召回原始消息。

模型通过 pruner 生成的“记忆 i”（偏移、长度）指路信息，调用本工具读取
HistoryManager 中对应消息的原文；只读内存，不落盘、不接入沙箱与 ModeGuard。
"""

from typing import Any, Dict, List, Optional

from ..base import Tool, ToolParameter
from ..errors import ToolErrorCode
from ..response import ToolResponse


class RecallTool(Tool):
    """按 HistoryManager 消息列表下标召回原始消息或内容切片。

    调用方式：
    - Recall(id)：返回整条消息（含 role 与下标）；
    - Recall(id, offset, length)：返回 content[offset : offset + length] 切片。

    下标口径与 pruner marker 中的“记忆 i”一致：HistoryManager 原始记录
    列表下标（不含 system，含 summary）。
    """

    def __init__(self, history):
        """初始化 RecallTool。

        Args:
            history: HistoryManager 实例；持有引用以读到最新历史。
        """
        super().__init__(
            name="Recall",
            description="""按历史下标召回原始消息。

当上下文中的内容被裁剪、出现“记忆 i 中偏移 X，长度 Y”的指路信息，或你需要
精确回溯早期对话原文时，用本工具取回。

参数：
- id：历史消息列表下标（必填）。
- offset：消息内容的起始字符位置（可选）。
- length：要读取的字符数（可选）。

只给 id 时返回整条消息；offset 与 length 同时给出时返回对应片段；
任一缺失时回退为整条消息。""",
            expandable=False,
        )
        self.history = history

    def get_parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="id",
                type="integer",
                description="历史消息列表下标（对应“记忆 i”中的 i）",
                required=True,
            ),
            ToolParameter(
                name="offset",
                type="integer",
                description="消息内容的起始字符位置；与 length 同时给出才切片",
                required=False,
            ),
            ToolParameter(
                name="length",
                type="integer",
                description="要读取的字符数；与 offset 同时给出才切片",
                required=False,
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        try:
            message_id = self._as_int(parameters.get("id"))
        except (TypeError, ValueError):
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="参数 id 必须是整数下标",
            )
        if message_id is None:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="缺少必需参数 id",
            )

        history = self.history.get_history()
        if not 0 <= message_id < len(history):
            return ToolResponse.error(
                code=ToolErrorCode.NOT_FOUND,
                message=(
                    f"历史中不存在下标为 {message_id} 的消息"
                    f"（当前共 {len(history)} 条消息）"
                ),
            )

        message = history[message_id]
        offset = self._optional_int(parameters.get("offset"))
        length = self._optional_int(parameters.get("length"))

        if offset is None or length is None:
            content = message.content
            header = f"[记忆{message_id}] [{message.role}]"
            data = {"id": message_id, "role": message.role, "content": content}
        else:
            content = message.content[offset : offset + length]
            header = (
                f"[记忆{message_id}] [{message.role}] 偏移{offset}，长度{length}"
            )
            data = {
                "id": message_id,
                "role": message.role,
                "offset": offset,
                "length": length,
                "content": content,
            }

        return ToolResponse.success(text=f"{header}\n{content}", data=data)

    @staticmethod
    def _as_int(value: Any) -> Optional[int]:
        """把参数转为整数；None 表示未提供，非法值抛 ValueError。"""
        if value is None:
            return None
        if isinstance(value, bool):
            raise TypeError("bool 不是合法下标")
        return int(value)

    def _optional_int(self, value: Any) -> Optional[int]:
        """可选整数参数：缺失或非法时返回 None（回退为整条消息）。"""
        try:
            return self._as_int(value)
        except (TypeError, ValueError):
            return None
