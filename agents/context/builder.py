"""ContextBuilder - 从历史记录组装上下文。

职责：
- 组装 system 提示词、历史消息和临时消息。
- 截断、摘要两个上下文治理模块的编排入口；历史召回改由 Recall 工具承担。
"""

from typing import Any, Dict, List, Optional

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
        # 大模型侧返回的真实输入 token 数（覆盖 history[0, _history_token_count_idx)）
        self._history_token_count = 0
        # 上述 token 数对应的历史区间右边界
        self._history_token_count_idx = 0
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

        messages = self.history.get_history()
        truncate = self.config.context_truncation_enabled
        summarize = self.config.context_summary_enabled

        if truncate:
            messages = self.pruner.prune(messages)
        if summarize:
            messages = self.summarizer.apply_summary_masking(messages)
            if self.should_summarize():
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
        if system_prompt:
            context.append({"role": "system", "content": system_prompt})
        for message in messages:
            context.append(message.to_llm_dict())
        if extra_messages:
            context.extend(extra_messages)
        return context

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
        self._history_token_count_idx = 0
        self._history_revision = self.history.revision

    def estimate_message_tokens(self, message: Message) -> int:
        """按字符数估算单条消息的 token（1 token ≈ 4 字符）。"""
        return len(str(message)) // 4

    def record_prompt_tokens(self, prompt_tokens: int) -> None:
        """用大模型返回的真实输入 token 数校准计量基线。"""
        self._sync_token_state()
        self._history_token_count = prompt_tokens
        self._history_token_count_idx = len(self.history.get_history())
        self._history_revision = self.history.revision

    def should_summarize(self) -> bool:
        """是否需要摘要：已计量 token + 未计量尾部估算超过窗口 × 阈值。"""
        self._sync_token_state()
        total = self._history_token_count
        history = self.history.get_history()
        idx = min(max(self._history_token_count_idx, 0), len(history))
        for message in history[idx:]:
            total += self.estimate_message_tokens(message)
        return total > self.config.context_window * self.config.compression_threshold
