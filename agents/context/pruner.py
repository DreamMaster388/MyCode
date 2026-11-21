"""ContextPruner - 上下文裁剪。

职责：对工具消息做视图级裁剪，作用于构建时的视图，不改写 history 中的原始记录。

所有工具消息使用同一套中间裁剪规则，不区分最近轮与旧消息：单条消息的裁剪
形态只取决于自身内容与历史下标，会话增长时已发送前缀保持稳定，以提升缓存命中率。
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
        pruned: List[Message] = []

        for index, message in enumerate(messages):
            if message.role != "tool":
                pruned.append(message)
                continue

            new_content = self._prune(message.content, index)
            if new_content is None:
                pruned.append(message)
            else:
                pruned.append(message.model_copy(update={"content": new_content}))

        return pruned

    def _prune(self, content: str, index: int) -> Optional[str]:
        """工具消息统一中间裁剪：保留头尾，中间放 marker。"""
        if len(content) < self.config.context_truncation_threshold:
            return None

        head = self.config.context_truncation_head_chars
        tail = self.config.context_truncation_tail_chars
        removed = len(content) - head - tail
        marker = (
            f"已裁剪，详情参见记忆{index}中偏移{head}，长度{removed}"
            "；完整内容可通过调用Recall工具查看"
        )
        tail_text = content[-tail:] if tail > 0 else ""
        candidate = content[:head] + "\n" + marker + "\n" + tail_text

        if len(candidate) < len(content):
            return candidate
        return None
