# Spec: 摘要内容选择与摘要遮蔽

作者：lyd。状态：已批准。

一句话：在 `context_summary_enabled` 分支接通摘要——按 token 触发，在裁剪后的视图上做摘要遮蔽，选出“摘要 + 压缩区”并调用空实现的普通/增量摘要函数，生成 content 为空的摘要消息 append 进 history，返回 `[摘要, 完整保留区]`。

## 问题

长任务里上下文持续增长，但摘要模块仍是空实现：`should_compress` 没有调用方，超过窗口阈值也不会压缩；摘要开关打开时，旧的 summary 消息和原文还会同时进入上下文。`Message.replace` 目前按区间端点理解，无法表达多条不连续被遮蔽的消息。

结果是：用户只能看着长会话不断膨胀，无法“保留最近工作、把更早轮次压成摘要”；开发者也无法在摘要开关语义、摘要消息格式和遮蔽算法上形成稳定契约，后续接入真正的摘要 LLM 时还要重做数据结构。

## 方案

在 builder 的摘要分支接通整条链路：

- `should_compress` 改名 `should_summarize`，口径不变；每次 `build()` 在摘要开关打开时判断一次。
- 需要摘要时，先对裁剪后的消息列表做遮蔽：非 summary 消息保留，summary 消息把它 `replace` 中的消息从视图删除，再把 summary 前插到列表头。最终视图是 `[摘要, 若干消息]` 或没有摘要时的 `[若干消息]`。
- 在遮蔽后的列表上划分 `[摘要, 压缩区, 完整保留区]`：完整保留区为最近 N 轮（N 可配置，默认 10），起点回退到第 N 近 assistant 之前最近的一条 user；摘要与压缩区就是送进摘要函数的内容。
- 没有旧摘要时走普通摘要函数，只输入压缩区；有旧摘要时走增量摘要函数，输入旧摘要和压缩区。两个函数本轮只构造 `role="summary"`、`content=""`、`replace` 为直接遮蔽 id 完整列表的消息，不调用 LLM。
- 新摘要 append 到 history，本次 build 返回 `[新摘要] + 完整保留区`；不需要摘要时使用遮蔽后的列表继续组装。
- `replace` 从区间端点改为完整 id 列表。
- 保留区不足 N 轮就触发压缩、压缩区为空、异常多摘要历史不在本轮处理，只在文档说明。

## 用户故事

1. 作为编码智能体用户，我想要长会话超过 token 阈值时自动把较早轮次压成摘要，以便窗口留给最近工作。
2. 作为编码智能体用户，我想要最近 10 轮完整保留，以便模型仍能基于最近上下文继续推理。
3. 作为编码智能体用户，我想要完整保留区从 user 请求开始，以便模型看到完整的最近任务而不是半截对话。
4. 作为编码智能体用户，我想要摘要开关打开后旧摘要与被遮蔽原文不再同时出现，以便上下文不重复、不浪费 token。
5. 作为智能体开发者，我想要 `replace` 记录被遮蔽消息的完整 id 列表，以便精确表达不连续遮蔽，而不是只能表示区间端点。
6. 作为智能体开发者，我想要遮蔽算法总是把摘要放在列表头、删除被遮蔽消息，以便同一份 history 每次都能生成稳定视图。
7. 作为智能体开发者，我想要无旧摘要时走普通摘要、有旧摘要时走增量摘要，以便后续接入摘要 LLM 时语义清晰。
8. 作为智能体开发者，我想要摘要消息 append 进 history 而不是改写旧消息，以便 history 保持 append-only、原文仍可审计。
9. 作为智能体开发者，我想要本次 build 返回的摘要和保留区都已经过裁剪，以便摘要接入不会绕过 pruner。
10. 作为智能体开发者，我想要保留轮数可配置、非法值回退默认，以便按模型窗口和任务类型调参。
11. 作为维护者，我想要本轮摘要函数是空实现、不调用 LLM、不访问网络，以便测试确定、不增加费用。
12. 作为测试维护者，我想要遮蔽、区间、触发和两种摘要函数的确定性测试，且不覆盖未定义情况，以便快速验证契约。
13. 作为会话持久化用户，我想要摘要消息按当前 Message 格式序列化（`replace` 为 id 列表），以便保存/加载后视图仍可重建。
14. 作为文档读者，我想要明确“保留区未达上限就触发压缩”不在本轮范围，以便不误以为该情况已被处理。

## 实现决策

### 模块职责

- **summary 模块**：`ContextSummarizer`，无状态，参数来自 Config。提供四个公开接口：
  - `apply_summary_masking(messages)`：纯函数，返回遮蔽后的新列表，不修改输入 Message。
  - `select_summary_region(messages)`：在遮蔽后的列表上返回“摘要区间”（可选旧摘要 + 压缩区）和“完整保留区”。
  - `summarize(compression)`：普通摘要，返回完整摘要 Message。
  - `summarize_incremental(previous, compression)`：增量摘要，返回完整摘要 Message。
- **builder**：`should_summarize()`；在摘要开关打开时按“裁剪 → 遮蔽 → 判断 → 选择/生成 → append → 返回”编排；摘要关闭时仍丢弃 summary；召回分支仍是空实现。
- **history**：只追加；摘要通过既有 append 写入，id 由 append 分配；不新增摘要 API。
- **config**：新增保留轮数字段；非法值回退默认。
- **message**：字段不变，`replace` 语义改为完整 id 列表；序列化格式不变。

### 触发

- `should_summarize()` 的口径沿用原 `should_compress`：已计量真实 prompt_tokens + 未计量尾部按字符数除以 4 估算，严格大于 `context_window × compression_threshold` 才触发。
- 每次 `build()` 在 `context_summary_enabled` 为真时判断一次；不检查压缩区是否为空。
- 触发判断不依赖遮蔽视图；计量基线仍由 `record_prompt_tokens` 校准，clear/load 后归零。
- 方法改名后，调用方同步改名；没有新增的调用点。

### 遮蔽

- 只在 `context_summary_enabled` 为真时运行，输入是经过 pruner 的消息列表。
- 遍历输入：非 summary 消息按原顺序 append；遇到 summary 消息，把它 `replace` 中 id 对应的消息从已累积列表删除，再把该 summary 前插到列表头。
- 结果是不含被遮蔽消息的新列表；摘要总在头部；良好构造的 history 下至多一条未遮蔽摘要。
- 不修改 history，不修改输入 Message；摘要关闭时继续按原逻辑丢弃 summary，不运行遮蔽。
- 直接遮蔽语义：新摘要只直接遮蔽旧摘要 id 和本次压缩区消息；更早的遮蔽由旧摘要的 replace 承担，依赖遮蔽算法按顺序遍历历史中的旧摘要。因此 history 中的旧摘要不能被删除或跳过。
- 不为异常多摘要、replace 指向不存在 id 的历史加特判；缺失 id 按无操作处理。

### 区间选择

- 在遮蔽后的列表上操作：首条是 summary 时，它属于“摘要”段；否则没有摘要段。
- 可见消息 = 摘要之后的全部消息。
- 保留轮数 N = `context_summary_keep_rounds`。在可见消息中取最后 N 条 assistant；完整保留区起点 = 第 N 近 assistant 之前最近的一条 user；该 assistant 之前没有 user 时，从该 assistant 本身开始。
- 压缩区 = 摘要之后、完整保留区起点之前的消息；完整保留区 = 起点到可见消息末尾。
- 摘要区间 = 摘要（可无）+ 压缩区，即送入摘要函数的内容。
- 轮次口径沿用“每条 assistant 消息算一轮”；summary、user、tool 不计轮。
- 不足 N 轮时直接按倒数第 N 条 assistant 取边界，不写兜底分支；该情况不在本轮验证范围，行为由文档说明。

### 生成与写回

- 摘要区间首条是 summary 时走增量摘要，否则走普通摘要。
- `summarize(compression)` 的 `replace` = 压缩区消息 id，按视图顺序。
- `summarize_incremental(previous, compression)` 的 `replace` = 旧摘要 id + 压缩区消息 id，按视图顺序；不并入旧摘要 replace 里的历史 id。
- 两个函数返回 `role="summary"`、`content=""`、`timestamp` 默认的 Message；id 留空，由 `HistoryManager.append` 分配。
- 新摘要 append 到 history 末尾；摘要插入不 bump revision，也不重置 token 计量基线。
- 本次 `build()` 返回 `[新摘要] + 完整保留区`；完整保留区来自已经过 pruner 的视图，不重新从 history 取原文，避免本次返回绕过裁剪。下一次 build 重新走完整流程。
- 空 content 摘要按既有 `to_llm_dict` 映射为 user 角色发送；本轮不特殊处理。

### 配置契约

- `context_summary_keep_rounds: int = 10`。
- 校验：`< 1` 时静默回退为 10；`context_summary_enabled` 保持传入值。
- 不新增摘要模型、prompt、温度、费用或 usage 配置；不新增环境变量、CLI、UI 入口。

### 数据模型

- `Message` 字段不变：`id`、`content`、`role`、`tool_call_id`、`tool_calls`、`replace`、`timestamp`、`metadata`。
- `replace` 的语义改为“本摘要直接遮蔽的完整消息 id 列表”，不再是区间端点；顺序为摘要区间在视图中的顺序。
- `to_dict` / `from_dict` 不变，`replace` 按 list 序列化；保存/加载往返后视图可重建。
- 摘要仍是 `role="summary"`；发送时降级为 user 的既有行为不变。

### 取舍与风险

- 完整 id 列表换取精确遮蔽和可自描述，代价是 replace 比区间端点更长。
- 直接遮蔽换取较短的新 replace，代价是旧摘要必须留在 history 并被遍历；如果未来需要“只看最新摘要就能还原遮蔽集”，要改成传递闭包并迁移数据。
- 遮蔽在视图层做，history 不改写；代价是每次 build 都要遍历并重建视图。
- 空 content 摘要会以空 user 消息出现在发送列表；本轮接受，后续填入内容后消失。
- 触发只看 token，不检查压缩区是否为空；保留区不足 N 轮、压缩区恰好为空的情况可能产生空 replace 摘要，不在本轮范围。
- token 计量仍基于 history 原文和真实 prompt_tokens，不按遮蔽视图重算；可能偏保守，下一次 LLM 返回后校准。
- summary 模块本轮不调用 LLM，后续接入时函数签名和 replace 语义保持不变。

## 测试决策

### Seam A：`ContextBuilder.build()`

直接构造 HistoryManager + Config + 消息，不经过 Agent 循环、不联网、不执行工具。重点验证：

- 摘要关闭：summary 消息被丢弃，遮蔽不运行。
- 摘要开启但 `should_summarize()` 为假：返回遮蔽后的列表（旧摘要前插、被遮蔽消息消失），不 append 新摘要。
- 摘要开启且需要摘要：history 末尾 append 一条 summary；返回 `[新摘要, 完整保留区]`；新摘要 content 为空、role 为 summary、`to_llm_dict` 映射为 user。
- 无旧摘要走普通摘要，有旧摘要走增量摘要；`replace` 为直接遮蔽 id 列表。
- 完整保留区从 user 开始；压缩后列表变为 `[新摘要, 该 user 起]`。
- 与 pruner 的交互：本次返回的保留区仍是被裁剪后的视图，不绕过 pruner。
- system 在最前、`extra_messages` 追加在最后且不入 history；每次 build 返回新列表。
- 通过 `record_prompt_tokens` 控制触发真假；clear/load 后计量状态归零的既有行为不变。

### Seam B：`ContextSummarizer` 算法接口

用短消息直接构造，重点验证：

- 遮蔽：无摘要、有摘要、`replace` 命中的消息被删除、摘要前插、非摘要顺序保持、输入不被修改。
- 区间：无摘要时按 user 边界切分；有摘要时摘要段与压缩区/保留区的划分；无 user 时从 assistant 开始的回退；N 可配置。
- `summarize`：`replace` = 压缩区 id 列表、content 为空、role 为 summary。
- `summarize_incremental`：`replace` = 旧摘要 id + 压缩区 id、content 为空、不并入旧 replace 的历史 id。
- 不测试：不足 N 轮触发压缩、空压缩区、异常多摘要历史；这些情况只在文档说明。

### 好测试的标准

确定性、无网络、不调用真实模型、不执行工具、不写文件；用短字符串和显式 id 断言；只测公开契约，不测私有辅助函数；不为已删除的占位行为保留测试。

### 测试落点与更新

- 新增 summary 算法测试文件，覆盖 Seam B。
- 重写 builder 测试中的 summary 相关用例，覆盖 Seam A；`should_compress` 用例改名为 `should_summarize`。
- 更新受影响的既有用例：摘要开关打开时保留 summary 的用例、pruner 不改 non-tool 的用例（遮蔽现在会移除被 replace 的消息）、摘要 stub 返回 None 的用例。
- 更新 Agent 上下文测试中对 `should_compress` 的调用。
- 验收：`pytest test/context test/core` 通过；不访问网络、不调用真实模型、不产生落盘文件。

## 不在范围

- 摘要内容生成、摘要 LLM 的模型/prompt/温度/费用/usage 记账。
- 增量摘要的内容合并算法与摘要质量评估。
- 完整保留区不足 N 轮就触发压缩的处理；压缩区为空；异常多摘要历史。
- 按遮蔽视图重算 token 计量、预算强制执行、上下文溢出处理。
- 删除或改写 history 中的原文；按“记忆 i”读取全文的工具。
- 历史召回（recall）的实现；本轮仍只保留开关与空分支。
- 旧会话的兼容、迁移与回退路径。
- 环境变量、CLI、UI 的新开关或展示变化。
- 摘要消息的样式、提示词或模型可见文案优化。

## 补充说明

- 默认值：`context_summary_enabled=false`；`context_summary_keep_rounds=10`；`context_window=128000`；`compression_threshold=0.8`。
- 轮次口径沿用 `GLOSSARY.md` 的“轮（LLM 轮）”：每条 assistant 消息算一轮。
- 术语 `replace`、摘要区间、压缩区、完整保留区的定义以本轮 spec 为准；术语表由用户自行维护。
- 完整保留区未达上限：本轮不做防御、不写测试，只在本文档记录；触发时可能出现空压缩区或空 replace 摘要。
- 直接遮蔽依赖旧摘要留在 history 并被遮蔽算法遍历；不要手动删除 history 中的摘要消息。
- 架构取舍见 ADR 0003；README 在实现完成后更新，删除“摘要仍为空实现”的描述。
- 兼容性：本项目仍处开发阶段，`replace` 语义直接从区间端点演进为完整 id 列表，不保留旧格式分支。

## 收尾

本规格已批准；拆票见 `issues/` 目录。
