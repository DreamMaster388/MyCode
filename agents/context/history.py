"""HistoryManager - 历史消息管理器

职责：
- 消息追加（只追加，不编辑，缓存友好）
- 按追加顺序为每条消息分配唯一 id
- 构建发送给 LLM 的上下文（build_context，暂为空实现）
- 会话序列化/反序列化
- 轮次边界检测
- token 计量（真实输入 token + 增量估算，判断是否需要压缩）
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
from ..core.message import Message


class HistoryManager:
    """历史管理器

    特性：
    - 只追加，不编辑（缓存友好）
    - 追加时自动分配自增 id（从 0 开始）
    - 支持会话保存/加载
    - token 计量：真实输入 token 校准 + 未计量消息的增量估算

    用法示例：
    ```python
    manager = HistoryManager(min_retain_rounds=10)

    # 追加消息（自动分配 id）
    manager.append(Message("hello", "user"))
    manager.append(Message("hi", "assistant"))

    # 获取历史
    history = manager.get_history()

    # 构建发送给 LLM 的上下文
    context = manager.build_context()

    # 序列化
    data = manager.to_dict()

    # 反序列化
    manager.load_from_dict(data)
    ```
    """

    def __init__(
        self,
        min_retain_rounds: int = 10,
        compression_threshold: float = 0.8,
        context_window: int = 128000
    ):
        """初始化历史管理器

        Args:
            min_retain_rounds: 摘要时保留的最小完整轮次数
            compression_threshold: 压缩阈值（已用 token 超过窗口大小 * 阈值时需压缩）
            context_window: 模型上下文窗口大小（tokens）
        """

        self._history: List[Message] = []
        self.min_retain_rounds = min_retain_rounds
        self.compression_threshold = compression_threshold
        self.context_window = context_window
        # 下一个待分配的 id（从 0 开始，只增不减，保证 id 唯一且递增）
        self._next_id: int = 0
        # 大模型侧返回的真实输入 token 数（覆盖 _history[0, _history_token_count_idx)）
        self._history_token_count: int = 0
        # 上述 token 数对应的历史区间右边界，即已计量区间为 _history[0, idx)
        self._history_token_count_idx: int = 0

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
        """获取历史副本

        Returns:
            历史消息列表的副本
        """
        return self._history.copy()

    def clear(self) -> None:
        """清空历史（同时重置 token 计量）"""
        self._history.clear()
        self._history_token_count = 0
        self._history_token_count_idx = 0

    def estimate_rounds(self) -> int:
        """预估完整轮次数

        一轮定义：1 user 消息 + N 条 assistant/tool/summary 消息

        Returns:
            完整轮次数
        """
        rounds = 0
        i = 0
        while i < len(self._history):
            if self._history[i].role == "user":
                rounds += 1
                # 跳过这一轮的后续消息
                i += 1
                while i < len(self._history) and self._history[i].role != "user":
                    i += 1
            else:
                i = i + 1
        return rounds

    def find_round_boundaries(self) -> List[int]:
        """查找每轮的起始索引

        Returns:
            每轮起始索引列表，例如 [0, 3, 7, 10]
        """
        boundaries = []
        for i, msg in enumerate(self._history):
            if msg.role == "user":
                boundaries.append(i)
        return boundaries

    def build_context(self) -> List[Dict[str, Any]]:
        """根据完整历史构建"该发给模型的内容"

        说明：本步骤只做来源记录，上下文构建留待后续步骤实现，
        当前为空实现，调用方不得依赖其返回值。

        Returns:
            发送给 LLM 的消息字典列表（当前固定为空列表）
        """
        return []

    # -------------------- token 计量 --------------------

    def estimate_message_tokens(self, message: Message) -> int:
        """估算单条消息消耗的 token

        按字符数估算（1 token ≈ 4 字符），只用于未计量区间的增量估算。

        Args:
            message: 待估算的消息

        Returns:
            估算的 token 数
        """
        return len(str(message)) // 4

    def should_compress(self) -> bool:
        """判断当前历史是否需要压缩

        口径：已计量的真实输入 token（覆盖 _history[0, idx)）
        + idx 之后各消息的估算 token > 上下文窗口 * 压缩阈值。

        Returns:
            是否需要压缩
        """
        total = self._history_token_count
        idx = min(max(self._history_token_count_idx, 0), len(self._history))
        for message in self._history[idx:]:
            total += self.estimate_message_tokens(message)
        return total > self.context_window * self.compression_threshold

    def record_prompt_tokens(self, prompt_tokens: int) -> None:
        """用大模型返回的真实输入 token 数校准计量基线

        校准后，已计量区间推进到当前完整历史（即本次请求回放的历史 + 当前输入）。
        注意 prompt_tokens 含 system 提示词与工具 schema 的开销，因此略偏保守。

        Args:
            prompt_tokens: 大模型返回的输入 token 数
        """
        self._history_token_count = prompt_tokens
        self._history_token_count_idx = len(self._history)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于会话保存）

        Returns:
            包含历史和元数据的字典
        """
        return {
            "history": [msg.to_dict() for msg in self._history],
            "next_id": self._next_id,
            "created_at": datetime.now().isoformat(),
            "rounds": self.estimate_rounds()
        }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """从字典加载（用于会话恢复）

        Args:
            data: 序列化的历史数据
        """
        self._history = [
            Message.from_dict(msg_data)
            for msg_data in data.get("history", [])
        ]
        # 加载后的历史没有对应的真实 prompt 计量，计量基线归零，
        # 全部按估算值参与 should_compress 判断
        self._history_token_count = 0
        self._history_token_count_idx = 0
        # 已有 id 的最大值（空历史时为 -1，下一个 id 即 0）
        existing_max = max(
            (msg.id for msg in self._history if msg.id is not None),
            default=-1
        )
        # 优先使用保存的 next_id，但不得小于已有 id 的最大值 + 1
        raw_next = data.get("next_id")
        next_id = existing_max + 1 if raw_next is None else int(raw_next)
        next_id = max(next_id, existing_max + 1)
        # 旧格式记录可能没有 id：按顺序补号，保证加载后 id 完备且唯一
        for msg in self._history:
            if msg.id is None:
                msg.id = next_id
                next_id += 1
        self._next_id = next_id


