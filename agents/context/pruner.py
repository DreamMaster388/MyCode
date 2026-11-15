"""ContextPruner - 上下文裁剪。

职责：对工具输出等上下文做预算内裁剪，作用于构建时的视图，不改写 history
中的原始记录。本轮为空实现。
"""

from typing import List

from ..core.config import Config
from ..core.message import Message


class ContextPruner:
    def __init__(self, config: Config):
        self.config = config

    def prune(self, messages: List[Message]) -> List[Message]:
        """返回裁剪后的消息列表。

        本轮为空实现：原样返回一份新列表，不读写文件、不改写 history。
        """
        return list(messages)
