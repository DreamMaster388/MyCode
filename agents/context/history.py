"""HistoryManager - 历史记录存储。

职责：
- 原始消息追加（只追加，不编辑，缓存友好）
- 按追加顺序为每条消息分配唯一 id
- 原始记录与摘要的统一存储
- 会话序列化/反序列化
- 轮次边界检测

"""

from datetime import datetime
from typing import Any, Dict, List

from ..core.message import Message


class HistoryManager:
    """原始记录与摘要的存储。

    特性：
    - 只追加，不编辑（缓存友好）
    - 追加时自动分配自增 id（从 0 开始）
    - 支持会话保存/加载
    - revision 在 clear/load 时递增，供 builder 重置派生状态
    """

    def __init__(self):
        self._history: List[Message] = []
        # 下一个待分配的 id（从 0 开始，只增不减，保证 id 唯一且递增）
        self._next_id: int = 0
        # clear/load 时递增；append 不变。builder 用它判断派生状态是否失效。
        self._revision: int = 0

    @property
    def revision(self) -> int:
        """存储版本号；clear/load 后变化。"""
        return self._revision

    def append(self, message: Message) -> Message:
        """追加消息（只追加，不编辑）

        未携带 id 的消息按追加顺序分配自增 id；已携带 id 的消息保留原 id，
        并把 _next_id 推进到其后，避免后续分配重复。

        Args:
            message: 要追加的消息

        Returns:
            追加后的消息（已带 id）
        """
        if message.id is None:
            message.id = self._next_id
            self._next_id += 1
        elif message.id >= self._next_id:
            self._next_id = message.id + 1

        self._history.append(message)
        return message

    def get_history(self) -> List[Message]:
        """获取历史副本。

        Returns:
            历史消息列表的副本
        """
        return self._history.copy()

    def clear(self) -> None:
        """清空历史。

        id 计数器保持只增不减，与既有行为一致；revision 递增以通知派生状态。
        """
        self._history.clear()
        self._revision += 1

    def estimate_rounds(self) -> int:
        """预估完整轮次数。

        一轮定义：1 user 消息 + N 条 assistant/tool/summary 消息。

        Returns:
            完整轮次数
        """
        rounds = 0
        i = 0
        while i < len(self._history):
            if self._history[i].role == "user":
                rounds += 1
                i += 1
                while i < len(self._history) and self._history[i].role != "user":
                    i += 1
            else:
                i += 1
        return rounds

    def find_round_boundaries(self) -> List[int]:
        """查找每轮的起始索引。

        Returns:
            每轮起始索引列表，例如 [0, 3, 7, 10]
        """
        return [i for i, message in enumerate(self._history) if message.role == "user"]

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于会话保存）。

        Returns:
            包含历史和元数据的字典
        """
        return {
            "history": [message.to_dict() for message in self._history],
            "next_id": self._next_id,
            "created_at": datetime.now().isoformat(),
            "rounds": self.estimate_rounds(),
        }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """从字典加载（用于会话恢复）。

        Args:
            data: 序列化的历史数据
        """
        self._history = [
            Message.from_dict(message_data)
            for message_data in data.get("history", [])
        ]

        # 已有 id 的最大值（空历史时为 -1，下一个 id 即 0）
        existing_max = max(
            (message.id for message in self._history if message.id is not None),
            default=-1,
        )
        # 优先使用保存的 next_id，但不得小于已有 id 的最大值 + 1
        raw_next = data.get("next_id")
        next_id = existing_max + 1 if raw_next is None else int(raw_next)
        self._next_id = max(next_id, existing_max + 1)
        self._revision += 1
