# 术语表

## 原始记录

`HistoryManager` 按追加顺序保存的 `user` / `assistant` / `tool` 消息，是构建上下文的唯一事实来源；摘要消息也存放在同一列表中，但不属于原始记录。

避免使用：原始历史、裸消息。

## 轮（LLM 轮）

以 assistant 消息计数：每条写入历史的 assistant 消息算一轮，正常路径下对应一次成功的模型返回（工具调用中间回复、最终纯文本回答），失败兜底写入的错误文本也占一轮；不以用户请求或 user 消息为单位。

避免使用：用户轮、请求轮（指代本定义时）。

## 记忆 i

裁剪 marker 中的指路坐标：i 是 `HistoryManager` 原始记录列表的下标（不含 system、含 summary），指向的全文仍保留在 history 中；本轮不提供按 i 读取记忆的工具。

避免使用：消息 id（`Message.id` 与列表下标在 clear/load 后可能不一致）。

## 三开关全关

截断、摘要、历史召回三个开关都关闭时的上下文构建方式：`system` 提示词 + 历史直接拼接，`summary` 消息被丢弃；不做裁剪、摘要或召回，也不做溢出检查。

## 截断模块（pruner）

三个上下文治理模块之一，对应开关 `context_truncation_enabled`，实现载体是 `pruner.py`；按“轮”和字符数裁剪 `role="tool"` 消息的 content，用指向“记忆 i”的 marker 替代被裁部分，不改写 history，且只在替换结果严格短于原文时替换。

避免使用：旧 `ObservationTruncator` 的“截断”（预览+落盘，已删除）。

## 摘要模块（summary）

三个上下文治理模块之一，对应开关 `context_summary_enabled`，实现载体是 `summary.py`；把较早的完整轮次压缩成一条 `role="summary"` 的消息。本轮为空实现/跳过，`replace` 字段保留但不消费。

避免使用：压缩（容易与裁剪混淆）。

## 历史召回（recall）

三个上下文治理模块之一，对应开关 `context_recall_enabled`；从历史中找回与当前任务相关的旧消息并重新注入上下文。本轮仅保留开关，无独立模块。

避免使用：历史找回、检索、回忆。

## 跳过 / 空实现

摘要、历史召回等本轮未实现的治理模块统一写“跳过”或“空实现”，分支里直接 `pass`。

## 上下文管理器

本项目指 `agents/context/history.py` 的 `HistoryManager`，负责原始记录与摘要的存储；不新增 `ContextManager` 门面。

避免使用：上下文构建器（builder 是另一个东西）。

## 上下文治理模块

截断、摘要、历史召回三个独立模块，分别由三个配置开关控制；截断（pruner）已实现，摘要与历史召回仍为空实现。
