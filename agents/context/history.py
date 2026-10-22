"""HistoryManager - 历史消息管理器

职责：
- 消息追加（只追加，不编辑，缓存友好）
- 按追加顺序为每条消息分配唯一 id
- 构建发送给 LLM 的上下文（system_prompt + 快照）
- 会话序列化/反序列化
- 轮次边界检测
- token 计量（真实输入 token + 增量估算，判断是否需要压缩）
- 摘要（按 replace 遮蔽旧消息，保留最近若干完整轮次）
"""

from typing import TYPE_CHECKING, List, Optional, Dict, Any
from datetime import datetime
from ..core.message import Message

if TYPE_CHECKING:
    from ..core.config import Config
    from ..core.llm import HelloAgentsLLM


class NotEnoughRoundsError(ValueError):
    """保留轮数不少于当前轮数，无法确定摘要边界。

    Step 3 按计划先以异常暴露该未定义场景，调用方需显式处理。
    """


class HistoryManager:
    """历史管理器

    特性：
    - 只追加，不编辑（缓存友好）
    - 追加时自动分配自增 id（从 0 开始）
    - 支持会话保存/加载
    - token 计量：真实输入 token 校准 + 未计量消息的增量估算
    - 摘要：超出阈值时把较早的完整轮次压缩为一条 summary 消息

    用法示例：
    ```python
    manager = HistoryManager(min_retain_rounds=10)

    # 追加消息（自动分配 id）
    manager.append(Message("hello", "user"))
    manager.append(Message("hi", "assistant"))

    # 获取历史
    history = manager.get_history()

    # 取应用摘要遮蔽后的可见历史 / 发送给 LLM 的上下文
    snapshot = manager.build_snapshot()
    context = manager.build_context("You are a helpful assistant.")

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
        context_window: int = 128000,
        config: Optional["Config"] = None,
        llm: Optional["HelloAgentsLLM"] = None
    ):
        """初始化历史管理器

        Args:
            min_retain_rounds: 摘要时保留的最小完整轮次数
            compression_threshold: 压缩阈值（已用 token 超过窗口大小 * 阈值时需压缩）
            context_window: 模型上下文窗口大小（tokens）
            config: Agent 配置（摘要开关与摘要模型配置）；None 时只用简单摘要
            llm: 主 Agent 的 LLM，提供摘要 LLM 的 api key / base url 默认值
        """

        self._history: List[Message] = []
        self.min_retain_rounds = min_retain_rounds
        self.compression_threshold = compression_threshold
        self.context_window = context_window
        self.config = config
        self._llm = llm
        # 摘要专用 LLM（懒加载；不可用时降级为简单摘要）
        self._summary_llm = None
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

    def build_snapshot(self) -> List[Message]:
        """计算 _history 的可见快照（应用 summary 的 replace 遮蔽）

        规则：
        - 非 summary 消息直接进入快照；
        - 遇到 summary 消息时，把它 replace 两个端点之间的片段（闭区间）
          整体替换为该 summary。端点按其在快照里的位置定位，因此 replace
          可以指向更早的 summary。

        例如 _history=[0,1,2,3,4], 5 为摘要且 replace=[0,3]，快照为 [5,4]。

        Returns:
            应用遮蔽后的消息列表（新列表，元素仍引用原 Message）
        """
        snapshot: List[Message] = []
        for message in self._history:
            if message.role != "summary" or len(message.replace or []) < 2:
                snapshot.append(message)
                continue

            first_id, last_id = message.replace[0], message.replace[-1]
            first_pos = next((i for i, m in enumerate(snapshot) if m.id == first_id), None)
            last_pos = next((i for i, m in enumerate(snapshot) if m.id == last_id), None)

            if first_pos is None or last_pos is None:
                # 兜底：端点已被更早的摘要遮蔽，退化为按 id 区间过滤后追加
                low, high = min(first_id, last_id), max(first_id, last_id)
                snapshot = [m for m in snapshot if m.id is None or not (low <= m.id <= high)]
                snapshot.append(message)
                continue

            start, end = (first_pos, last_pos) if first_pos <= last_pos else (last_pos, first_pos)
            snapshot[start:end + 1] = [message]

        return snapshot

    def build_context(self, system_prompt: Optional[str] = None) -> List[Dict[str, Any]]:
        """依据 HistoryManager 的内容构建输入给大模型的完整上下文

        结构：system_prompt（可选）+ 应用摘要遮蔽后的历史快照。
        summary 消息经 Message.to_llm_dict() 发送时会降级为 user 角色。

        Args:
            system_prompt: 系统提示词；为空则不加 system 消息

        Returns:
            发送给 LLM 的消息字典列表
        """
        context: List[Dict[str, Any]] = []
        if system_prompt:
            context.append({"role": "system", "content": system_prompt})
        for message in self.build_snapshot():
            context.append(message.to_llm_dict())
        return context

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

    # -------------------- 摘要 --------------------

    # 摘要 prompt 中单条消息的最大字符数（避免摘要请求本身过大）
    _SUMMARY_CONTENT_LIMIT = 500

    def add_summary(self) -> Optional[Message]:
        """判断是否需要摘要，需要则生成并追加一条 summary 消息

        算法：
        1. should_compress() 为 False 时直接返回 None；
        2. 取快照，用 find_round_boundaries 定位"最老需要保留的轮次"的消息 id；
        3. 快照中位于该消息之前的消息即待压缩内容（前缀，天然是完整轮次）；
        4. 调用 summarize 生成摘要文本，replace 记录待压缩前缀在快照里的首尾 id。

        Returns:
            新追加的 summary 消息；未摘要时返回 None

        Raises:
            NotEnoughRoundsError: 保留轮数 >= 当前轮数，无法确定压缩边界
        """
        if not self.should_compress():
            return None

        snapshot = self.build_snapshot()
        boundaries = self.find_round_boundaries()
        if self.min_retain_rounds >= len(boundaries):
            raise NotEnoughRoundsError(
                f"保留轮数({self.min_retain_rounds}) >= 当前轮数({len(boundaries)})，无法确定摘要边界"
            )

        keep_index = boundaries[-self.min_retain_rounds]
        keep_id = self._history[keep_index].id
        keep_pos = next((i for i, m in enumerate(snapshot) if m.id == keep_id), None)
        if keep_pos is None:
            # 边界消息已被更早的摘要遮蔽（保留区与摘要区重叠），本轮不压缩
            return None

        to_compress = snapshot[:keep_pos]
        if not any(message.role != "summary" for message in to_compress):
            # 前缀里只剩上一份摘要，没有新增的可压缩内容，避免反复摘要
            return None

        summary = Message(
            content=self.summarize(to_compress),
            role="summary",
            replace=[to_compress[0].id, to_compress[-1].id]
        )
        self.append(summary)
        return summary

    def summarize(self, messages: List[Message]) -> str:
        """把一批消息压缩为一段摘要文本

        优先使用 config 里配置的摘要大模型；大模型不可用（未配置/调用失败）时
        降级为简单摘要（统计信息）。

        Args:
            messages: 需要压缩的消息列表

        Returns:
            摘要文本
        """
        if not messages:
            return ""

        if self.config is None or not getattr(self.config, "enable_smart_compression", False):
            return self._simple_summary(messages)

        try:
            summary_llm = self._get_summary_llm()
            response = summary_llm.invoke(
                [
                    {"role": "system", "content": "你是一个对话摘要助手，只保留对后续任务有用的信息。"},
                    {"role": "user", "content": self._build_summary_prompt(messages)},
                ],
                temperature=getattr(self.config, "summary_temperature", 0.3),
                max_tokens=getattr(self.config, "summary_max_tokens", 800),
            )
            text = (response.content or "").strip()
            return text or self._simple_summary(messages)
        except Exception as e:
            print(f"⚠️ 摘要大模型不可用（{e}），降级为简单摘要")
            return self._simple_summary(messages)

    def _build_summary_prompt(self, messages: List[Message]) -> str:
        """把消息列表拼成摘要 prompt"""
        lines = []
        for message in messages:
            content = message.content or ""
            if len(content) > self._SUMMARY_CONTENT_LIMIT:
                content = content[:self._SUMMARY_CONTENT_LIMIT] + "...(已截断)"
            lines.append(f"[{message.role}] {content}")
        transcript = "\n\n".join(lines)

        return (
            "请把下面的对话历史压缩为一段简洁的中文摘要，保留：任务目标、关键决策、"
            "已完成的工作、待处理事项、重要发现，以及涉及的文件/命令等关键信息。"
            "不要编造未出现的内容。\n\n"
            f"## 对话历史\n{transcript}\n\n"
            "## 输出要求\n只输出摘要正文，不要加标题或额外说明。"
        )

    @staticmethod
    def _simple_summary(messages: List[Message]) -> str:
        """简单摘要（只含统计信息），用于大模型不可用时降级"""
        rounds = sum(1 for message in messages if message.role == "user")
        assistant_count = sum(1 for message in messages if message.role == "assistant")
        tool_count = sum(1 for message in messages if message.role == "tool")

        return (
            f"此会话包含 {rounds} 轮对话：\n"
            f"- 用户消息：{rounds} 条\n"
            f"- 助手消息：{assistant_count} 条\n"
            f"- 工具消息：{tool_count} 条\n"
            f"- 总消息数：{len(messages)} 条"
        )

    def _get_summary_llm(self):
        """懒加载摘要专用 LLM

        api key / base url 未单独配置时复用主 Agent 的，模型未配置时同理。
        """
        if self._summary_llm is not None:
            return self._summary_llm

        from ..core.llm import HelloAgentsLLM

        config = self.config
        model = getattr(config, "summary_llm_model", None) or getattr(self._llm, "model", None)
        api_key = getattr(config, "summary_llm_api_key", None) or getattr(self._llm, "api_key", None)
        base_url = getattr(config, "summary_llm_base_url", None) or getattr(self._llm, "base_url", None)

        self._summary_llm = HelloAgentsLLM(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=getattr(config, "summary_temperature", 0.3),
            max_tokens=getattr(config, "summary_max_tokens", 800),
        )
        return self._summary_llm

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


