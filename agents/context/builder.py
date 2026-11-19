"""ContextBuilder - 从历史记录组装上下文。

职责：
- 组装 system 提示词、历史消息和临时消息。
- 截断、摘要两个上下文治理模块的编排入口；历史召回改由 Recall 工具承担。
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import Config
from ..core.message import Message
from .history import HistoryManager
from .pruner import ContextPruner
from .summary import ContextSummarizer, SummaryOutcome


class ContextBuilder:
    """从 HistoryManager 的原始记录构建发送给 LLM 的消息列表。"""

    def __init__(
        self,
        history: HistoryManager,
        config: Config,
        llm=None,
    ):
        self.history = history
        self.config = config
        self.pruner = ContextPruner(config)
        self.summarizer = ContextSummarizer(config, llm)
        # 最近一次 build 触发的摘要结果；由调用方消费一次后清空
        self._last_summary_outcome: Optional[SummaryOutcome] = None
        # 最近一次 build 返回列表的逐条类别标注；由调用方消费一次后清空
        self._last_context_labels: List[str] = []
        # 大模型侧返回的真实输入 token 数（锚点总量）
        self._history_token_count = 0
        # 锚点边界：该次实际发送视图的最大消息 id；None 表示还没有锚点
        self._history_token_count_max_id: Optional[int] = None
        # 计量对应的 history.revision；clear/load 后据此归零
        self._history_revision = history.revision

    def build(
        self,
        system_prompt: Optional[str] = None,
        extra_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """构建一次请求的上下文。

        Args:
            system_prompt: 系统提示词；为空则不加 system 消息。
            extra_messages: 只用于本次请求的临时消息（如收尾指令），不写入历史。

        Returns:
            发送给 LLM 的消息字典列表。
        """
        self._sync_token_state()
        self._last_summary_outcome = None
        self._last_context_labels = []

        messages = self.history.get_history()
        truncate = self.config.context_truncation_enabled
        summarize = self.config.context_summary_enabled

        if truncate:
            messages = self.pruner.prune(messages)
        if summarize:
            messages = self.summarizer.apply_summary_masking(messages)
            if (
                self._visible_assistant_count(messages) >= 2
                and self.should_summarize(messages, extra_messages)
            ):
                selected, keep = self.summarizer.select_summary_region(messages)
                if selected and selected[0].role == "summary":
                    outcome = self.summarizer.summarize_incremental(
                        selected[0], selected[1:]
                    )
                else:
                    outcome = self.summarizer.summarize(selected)
                self.history.append(outcome.message)
                self._last_summary_outcome = outcome
                messages = [outcome.message] + keep
        if not summarize:
            # 如果没有启用摘要，则删除历史中的摘要信息，这个分支只会在会话配置中途改变时触发
            messages = [message for message in messages if message.role != "summary"]

        context: List[Dict[str, Any]] = []
        labels: List[str] = []
        if system_prompt:
            context.append({"role": "system", "content": system_prompt})
            labels.append("system")
        for message in messages:
            context.append(message.to_llm_dict())
            labels.append(self._category_for(message))
        if extra_messages:
            context.extend(extra_messages)
            labels.extend(["conclude_instruction"] * len(extra_messages))
        self._last_context_labels = labels
        return context

    @staticmethod
    def _visible_assistant_count(messages: List[Message]) -> int:
        """遮蔽后的视图里可见 assistant 轮数；头部 summary 不计入。"""
        visible = messages[1:] if messages and messages[0].role == "summary" else messages
        return sum(1 for message in visible if message.role == "assistant")

    @staticmethod
    def _category_for(message: Message) -> str:
        """把发送视图中的一条消息映射到类别；summary 必须独立于 user。"""
        if message.role == "summary":
            return "summary_message"
        if message.role == "tool":
            return "tool_result"
        if message.role == "assistant":
            return "assistant"
        if message.role == "system":
            return "system"
        return "user"

    def take_context_labels(self) -> List[str]:
        """取出最近一次 build 的逐条类别标注，并清空。

        返回列表与 build() 的返回值一一对应；未 build 或已取过时返回空列表。
        """
        labels = self._last_context_labels
        self._last_context_labels = []
        return labels

    def take_summary_outcome(self) -> Optional[SummaryOutcome]:
        """取出最近一次 build 触发的摘要结果，并清空。

        未触发摘要时返回 None；同一结果只能取一次，避免调用方重复记账。
        """
        outcome = self._last_summary_outcome
        self._last_summary_outcome = None
        return outcome

    # -------------------- token 计量 --------------------

    def _sync_token_state(self) -> None:
        """history 被 clear/load 后，计量基线归零。"""
        if self.history.revision != self._history_revision:
            self.reset_token_state()

    def reset_token_state(self) -> None:
        """重置计量基线，使其对应当前 history。"""
        self._history_token_count = 0
        self._history_token_count_max_id = None
        self._history_revision = self.history.revision

    @staticmethod
    def _payload_text(payload: Dict[str, Any]) -> str:
        """实际发送 payload 的 JSON 文本；不含 history 内部字段。"""
        return json.dumps(payload, ensure_ascii=False)

    def record_prompt_tokens(self, prompt_tokens: int) -> None:
        """用大模型返回的真实输入 token 数校准锚点。

        边界取本次请求实际发送视图的最大消息 id；主循环在 build 之后、
        追加 assistant/tool 之前调用，因此当前 history 的最大 id 即该边界。
        """
        self._sync_token_state()
        self._history_token_count = prompt_tokens
        self._history_token_count_max_id = max(
            (message.id for message in self.history.get_history() if message.id is not None),
            default=-1,
        )
        self._history_revision = self.history.revision

    def _estimated_tail(self, messages: List[Message], extra_messages) -> Tuple[int, int]:
        """返回 (字符数, UTF-8 字节数)；有锚点时只统计边界之后的消息。"""
        char_count = 0
        byte_count = 0
        boundary = self._history_token_count_max_id
        for message in messages:
            if boundary is not None and (
                message.id is None or message.id <= boundary
            ):
                continue
            text = self._payload_text(message.to_llm_dict())
            char_count += len(text)
            byte_count += len(text.encode("utf-8"))
        for payload in extra_messages or []:
            text = self._payload_text(payload)
            char_count += len(text)
            byte_count += len(text.encode("utf-8"))
        return char_count, byte_count

    def should_summarize(
        self,
        messages: Optional[List[Message]] = None,
        extra_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """是否需要摘要：锚点总量 + 尾部双估算是否超过各自阈值。

        有锚点时只估算锚点边界后新增的实际视图消息与 extra；没有锚点时
        整个视图加 extra 都按尾部估算，锚点总量按 0 计。
        """
        self._sync_token_state()
        if messages is None:
            messages = self._current_view()
        char_count, byte_count = self._estimated_tail(messages, extra_messages)
        char_tokens = (char_count + 3) // 4
        byte_tokens = (byte_count + 3) // 4
        prompt_tokens = (
            self._history_token_count
            if self._history_token_count_max_id is not None
            else 0
        )
        return (
            prompt_tokens + char_tokens
            > self.config.context_window * self.config.compression_threshold
            or prompt_tokens + byte_tokens > self.config.context_window
        )

    def _current_view(self) -> List[Message]:
        """按当前配置生成实际发送视图（裁剪 + 摘要遮蔽）。"""
        messages = self.history.get_history()
        if self.config.context_truncation_enabled:
            messages = self.pruner.prune(messages)
        if self.config.context_summary_enabled:
            messages = self.summarizer.apply_summary_masking(messages)
        else:
            messages = [message for message in messages if message.role != "summary"]
        return messages
