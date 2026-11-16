"""ContextSummarizer - 摘要遮蔽、选区与摘要生成。

职责：在视图层遮蔽被摘要覆盖的消息，按完整保留区选出摘要区间，并生成摘要消息。
本轮摘要消息的 content 为空占位，不调用 LLM。
"""

from typing import List, Tuple

from ..core.config import Config
from ..core.message import Message


class ContextSummarizer:
    def __init__(self, config: Config):
        self.config = config

    def apply_summary_masking(self, messages: List[Message]) -> List[Message]:
        """返回遮蔽后的新列表。

        非 summary 消息按原顺序保留；遇到 summary 时，把它 replace 中 id
        对应的已累积消息删除，再把该 summary 前插到列表头。不修改输入。
        """
        masked: List[Message] = []
        for message in messages:
            if message.role != "summary":
                masked.append(message)
                continue
            replaced_ids = set(message.replace)
            masked = [
                candidate for candidate in masked if candidate.id not in replaced_ids
            ]
            masked.insert(0, message)
        return masked

    def select_summary_region(
        self, messages: List[Message]
    ) -> Tuple[List[Message], List[Message]]:
        """在遮蔽后的列表上返回 (摘要区间, 完整保留区)。

        摘要区间 = 旧摘要（若有）+ 压缩区；完整保留区从最近 N 轮的第一条 user 起。
        不足 N 轮时直接取倒数第 N 条 assistant，不做兜底。
        """
        summary = messages[0] if messages and messages[0].role == "summary" else None
        visible = messages[1:] if summary else messages
        assistant_indexes = [
            index
            for index, message in enumerate(visible)
            if message.role == "assistant"
        ]
        boundary = assistant_indexes[-self.config.context_summary_keep_rounds]
        for index in range(boundary, -1, -1):
            if visible[index].role == "user":
                boundary = index
                break

        selected = ([summary] if summary else []) + visible[:boundary]
        return selected, visible[boundary:]

    def summarize(self, compression: List[Message]) -> Message:
        """普通摘要：content 为空，replace 为压缩区消息 id。"""
        return Message(
            content="",
            role="summary",
            replace=[message.id for message in compression],
        )

    def summarize_incremental(
        self, previous: Message, compression: List[Message]
    ) -> Message:
        """增量摘要：replace 为旧摘要 id + 压缩区消息 id。"""
        return Message(
            content="",
            role="summary",
            replace=[previous.id] + [message.id for message in compression],
        )
