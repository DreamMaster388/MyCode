"""ContextPruner - 上下文裁剪。

职责：对工具消息做视图级裁剪，作用于构建时的视图，不改写 history 中的原始记录。
"""

from typing import List, Optional

from ..core.config import Config
from ..core.message import Message


class ContextPruner:
    def __init__(self, config: Config):
        self.config = config

    def prune(self, messages: List[Message]) -> List[Message]:
        """返回裁剪后的消息列表。

        只处理 role="tool" 的消息；未裁剪的消息保持原对象，被裁剪的消息返回副本。
        """
        total_assistants = sum(
            1 for message in messages if message.role == "assistant"
        )
        assistants_before = 0
        pruned: List[Message] = []

        for index, message in enumerate(messages):
            if message.role == "assistant":
                assistants_before += 1
                pruned.append(message)
                continue
            if message.role != "tool":
                pruned.append(message)
                continue

            if (
                total_assistants - assistants_before
                < self.config.context_truncation_rounds
            ):
                new_content = self._prune_recent(message.content, index)
            else:
                new_content = self._prune_old(message.content, index)

            if new_content is None:
                pruned.append(message)
            else:
                pruned.append(message.model_copy(update={"content": new_content}))

        return pruned

    def _prune_recent(self, content: str, index: int) -> Optional[str]:
        """最近轮内的工具消息：保留头尾，中间放 marker。"""
        if len(content) < self.config.context_truncation_threshold:
            return None

        head = self.config.context_truncation_head_chars
        tail = self.config.context_truncation_tail_chars
        removed = len(content) - head - tail
        marker = f"已裁剪，详情参见记忆{index}中偏移{head}，长度{removed}"
        tail_text = content[-tail:] if tail > 0 else ""
        candidate = content[:head] + "\n" + marker + "\n" + tail_text

        if len(candidate) < len(content):
            return candidate
        return None

    def _prune_old(self, content: str, index: int) -> Optional[str]:
        """轮次范围外的工具消息：整体替换为 marker。"""
        marker = f"已裁剪，详情参见记忆{index}中偏移0，长度{len(content)}"
        if len(marker) < len(content):
            return marker
        return None
