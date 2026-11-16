# 0002 上下文裁剪与轮次口径

- 状态：已接受（2026-11-16）

本轮把“轮”从 user 请求改为 assistant 消息计数（每条写入历史的 assistant 消息算一轮，失败兜底文本也占一轮），并在 `context_truncation_enabled` 分支启用 pruner：`role="tool"` 消息按“其后 assistant 消息数 < 10”判断是否在最近 10 轮内；近期超长内容保留前 1500/后 500 字符（可配置），中间用指向“记忆 i”的 marker 替代，旧消息整体替换为 marker；marker 的 i 是 `HistoryManager` 原始记录列表下标，全文仍留在 history，本轮不提供按 i 读取记忆的工具；只有替换结果严格短于原文才替换。取舍是用“最近 10 轮内仍可能裁剪最新工具输出”和“marker 不可直接读取”换取稳定的单条工具输出上限与 history 可回溯性；轮次口径改动会改变会话 `rounds` 的语义，后续摘要/召回需沿用同一口径。
