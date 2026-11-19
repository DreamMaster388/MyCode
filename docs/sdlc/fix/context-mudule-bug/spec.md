# Spec: 摘要保留区不足与触发估算偏差修复

本文取代 [context-summarizer/spec.md](../../feat/context-summarizer/spec.md) 的区间选择与触发估算契约；摘要生成、降级、usage/trace 继续按 [context-summary-generation/spec.md](../../feat/context-summary-generation/spec.md)。

## 问题

摘要触发时，如果可见 assistant 轮数少于 `context_summary_keep_rounds`，区间选择会取越界下标，`build()` 直接抛 `IndexError`；保留区自己就超过阈值时，还会生成空压缩区摘要并每步重复。

触发判断用 `len(str(message)) // 4` 估算 history 原始尾部：assistant 的 `tool_calls` 参数完全不参与，中文按 4 字符/token 算，触发偏晚；同时裁剪、遮蔽后的实际视图没有参与估算，加载会话后又按原始 history 全量重算，触发偏早。

## 方案

- 触发后按可见 assistant 轮数切选区：不足 2 轮不摘要；2 轮及以上保留最近轮次、压缩更早消息，压缩区始终非空，保留区至少 1 轮，不再强制从 user 开始。
- 触发估算改为「最近一次真实输入 + 锚点后实际视图新增消息」，同时用字符数和 UTF-8 字节数两种估算，分别守住压缩阈值和窗口上限。
- 保留区自身仍超阈值时逐轮让出，不再产生空压缩区摘要。
- 不新增配置、依赖或 tokenizer；摘要生成、降级、usage/trace 不变。

## 用户故事

1. 作为编码智能体用户，我想要提前触发摘要时不再直接报错，以便任务能继续。
2. 作为编码智能体用户，我想要中文和工具参数很多的上下文也能在超窗之前触发摘要，以便不因估算偏低而硬失败。
3. 作为编码智能体用户，我想要裁剪或加载会话后不因重复计数而过早摘要，以便不浪费摘要调用和上下文。
4. 作为编码智能体用户，我想要保留区自身超阈值时逐轮让出，以便不产生空摘要、也不每步重复压缩同一批消息。
5. 作为维护者，我想要保留区、压缩区的边界规则固定且可测试，以便后续调整摘要策略不回归。

## 实现决策

### 选区

- 可见消息是遮蔽后的列表去掉头部 summary（若有）；A 是可见 assistant 数。
- A < 2：不摘要，不调用摘要 LLM、不 append、不产生 outcome，返回遮蔽后的完整视图（含旧摘要，若有）。
- A ≥ 2：`keep = min(context_summary_keep_rounds, A - 1)`；边界取倒数第 `keep` 条 assistant；压缩区是边界之前的全部可见消息，保留区是边界到末尾。
- 不回退到 user：保留区可以从 assistant 开头，触发它的 user 请求在压缩区，由摘要承载。
- 有旧摘要时摘要区间是旧摘要 + 压缩区，走增量摘要；否则摘要区间是压缩区，走普通摘要。新摘要 append，本次返回 `[新摘要] + 保留区`。
- 触发后，A ≤ 配置值时每次 build 让出最旧 1 轮；A > 配置值时保留配置值轮，压缩其余。保留区自身仍超阈值时，下一次 build 继续让出，直到 A = 1 或低于阈值。
- `select_summary_region` 只处理 A ≥ 2，不修改输入；A < 2 由 builder 拦截。

### 触发估算

- 锚点 = 最近一次主循环成功请求的真实 `prompt_tokens`，以及该次实际发送视图的最大消息 id；`record_prompt_tokens` 刷新，clear/load 清空。
- 尾部 = 当前实际视图（pruner 裁剪、summary 遮蔽后）中 id 大于锚点边界的消息，加本次 `extra_messages`；没有锚点时 P 按 0 计，尾部是整个视图加 extra。
- 估算对象是实际发送 payload（`role/content/tool_calls/tool_call_id`）的 JSON，不是 `str(message)`；`C = ceil(字符数 / 4)`，`B = ceil(UTF-8 字节数 / 4)`。
- 触发条件是 `P + C > context_window × compression_threshold` 或 `P + B > context_window`，严格大于，任一成立即触发。
- 有锚点时，系统提示词和 tool schema 已在 P 中计过，尾部不重复估算它们。锚点只由主循环请求刷新，收尾请求与摘要调用不刷新。
- 锚点范围内旧消息变小时的偏差不做额外补偿，见《补充说明》的已知限制。

### 取舍

- 用严格 assistant 切分换确定性和无空压缩区，牺牲保留区的自包含。
- 用字符 + UTF-8 双估算换中文和工具参数的低估防护，牺牲单一口径的一致性。
- 用「只加新增消息」换实现简单，牺牲裁剪/摘要缩小时的即时准确性。
- 用不引入 tokenizer 换零依赖，牺牲估算精度。

## 测试决策

### Seam A：`ContextBuilder.build()`（含 `record_prompt_tokens`）

- A < 2：不产生摘要调用、不 append、无 outcome，返回遮蔽后的完整视图（含旧摘要）。
- A = 2：压缩区包含触发保留 assistant 的 user 请求，保留区从该 assistant 开始，只保留 1 轮。
- A ≤ 配置值：连续触发时保留区逐轮减少，不会出现空压缩区摘要，最终停在 1 轮并跳过。
- A > 配置值：保留恰好配置值轮，压缩其余可见消息。
- 有旧摘要：走增量摘要，摘要区间包含旧摘要与压缩区。
- 触发：字符条件与字节条件分别可单独触发；等于阈值不触发。
- 尾部：只统计锚点后新增的实际视图消息与 extra；工具参数、裁剪后的工具内容、额外消息进入估算；被遮蔽的旧消息不重复计入；clear/load 后重新按整个视图估算。
- 不测真实 tokenizer、具体 token 数值、prompt 文本、摘要质量、CodeAgent 接线。

### Seam B：`ContextSummarizer.select_summary_region()`

- A = 2、A ≤ 配置值、A > 配置值时，keep 与边界符合选区契约，压缩区非空、保留区至少 1 轮。
- 有旧摘要时摘要区间包含旧摘要；无旧摘要时不包含。
- 保留区从 assistant 开始时，不回退到 user。
- 输入列表与消息对象不被修改。
- 不测 A < 2、token 估算、LLM 调用。

## 不在范围

- 引入 tokenizer 或按模型词表精确计数。
- 为 A = 1 的超窗、无锚点/加载后的估算偏差设计补偿或新配置。
- 输出 token 预留、上下文溢出后的重试或降级。
- 摘要质量、prompt、降级内容、usage/trace 口径的改动。
- CodeAgent、收尾路径、会话持久化的新行为。

## 补充说明

- 默认值不变：`context_summary_keep_rounds=10`、`context_window=128000`、`compression_threshold=0.8`；`context_summary_keep_rounds` 小于 1 仍回退 10；没有新增配置或依赖。
- 所有估算都是近似值，不是 tokenizer 精确计数。
- 已知限制：A = 1 且仍超阈值时跳过摘要，可能超窗；裁剪/摘要缩小锚点范围内旧消息时，到下一次真实 usage 前会短暂高估；无锚点时 system/tool schema 未计入估算；字符阈值对中文仍偏低，依赖字节硬阈值兜底。
- 术语见 [GLOSSARY.md](../../../../GLOSSARY.md)：保留区、动态让出、锚点、尾部估算、双阈值。
- 取舍见《实现决策》的取舍小节；本轮不新增 ADR。
