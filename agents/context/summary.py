"""ContextSummarizer - 上下文摘要压缩。

职责：把较早的完整轮次压缩成一条摘要消息。本轮为空实现。
"""

from typing import List, Optional

from ..core.config import Config
from ..core.message import Message


class ContextSummarizer:
    def __init__(self, config: Config):
        self.config = config

    def maybe_summarize(self, messages: List[Message]) -> Optional[Message]:
        """返回一条摘要消息，或 None 表示本轮不摘要。

        本轮为空实现：始终返回 None，不调用 LLM、不改写 history。
        """
        return None
