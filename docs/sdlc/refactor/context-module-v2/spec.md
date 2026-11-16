# Spec: 上下文构建体系 v2（三个上下文治理模块）

作者：lyd。状态：已批准。

一句话：工具结果全文入历史；history 只存原始记录与摘要，builder 负责组装；截断、摘要、历史召回三个上下文治理模块本轮跳过或空实现；三开关全关时发送历史并丢弃 summary 消息。

## 问题

编码智能体在长任务里依赖工具输出做判断，但旧工具输出截断会把超长结果截成预览，完整内容落到落盘目录，模型只能看到“完整内容见路径”的提示。模型无法直接使用完整结果，排查也依赖额外文件。

上下文策略不可组合：历史存储、token 计量、摘要、工具输出截断混在 `HistoryManager` 里，想只保留历史、关掉摘要或截断做不到。旧 `ContextBuilder` 是 GSSC 流水线，依赖已从依赖清单移除的 `tiktoken`，实际不可用。

重构需要一个稳定的默认组装方式，再逐步加能力；现有代码没有“截断、摘要、历史召回”三个治理模块的清晰边界，也没有对应的配置开关。旧配置、测试和 eval 仍引用 `tool_output_*`，重构后容易留下死路径。

## 方案

工具结果不再截断，完整文本直接写入历史；删除 `ObservationTruncator`、落盘路径和相关配置。

上下文包拆成四层：history 只存原始记录与摘要；pruner 负责裁剪（本轮空实现）；summary 负责摘要（本轮空实现）；builder 负责从历史组装上下文。截断、摘要、历史召回是三个独立的上下文治理模块，分别由 `context_truncation_enabled`、`context_summary_enabled`、`context_recall_enabled` 控制。

本轮三个模块都跳过或空实现：builder 的三个 `if` 分支只保留占位；三开关全关时直接发送历史并丢弃 summary 消息；摘要开关打开时保留 summary 消息，等待后续摘要算法。

Agent 改用 builder 组装上下文；history 保留兼容读写接口；token 计量迁到 builder。删除旧 GSSC builder、`ObservationTruncator`、旧配置、旧测试和 eval 中的落盘传参，更新 README。

## 用户故事

1. 作为编码智能体的用户，我想要工具输出完整进入上下文，以便模型能直接看到完整结果而不是只有预览。
2. 作为智能体开发者，我想要用三个独立开关分别控制截断、摘要和历史召回，以便按需启用治理模块。
3. 作为智能体开发者，我想要未实现的治理模块直接跳过或空实现，以便本轮保持简单、可预测。
4. 作为智能体开发者，我想要三个开关全关时直接发送历史并丢弃 summary 消息，以便默认上下文干净且可预测。
5. 作为智能体开发者，我想要摘要开关开启时保留 summary 消息，以便后续摘要算法接入时不必再改开关语义。
6. 作为智能体开发者，我想要 builder 负责组装与 token 计量、history 只负责存储，以便职责清晰、可独立测试。
7. 作为未来实现治理模块的开发者，我想要 token 计量继续记录真实 prompt_tokens 和增量估算，以便预算判断有基线。
8. 作为智能体开发者，我想要工具结果全文写入 history 且不再落盘，以便删除一条重复的落盘路径。
9. 作为维护者，我想要删除旧 GSSC builder、`ObservationTruncator` 和相关配置，以便不再维护不可用或重复的路径。
10. 作为评测/测试维护者，我想要 eval 和测试不再依赖 `tool_output_dir`，以便清理后仓库可运行。
11. 作为使用会话持久化的用户，我想要 history 序列化格式保持兼容，以便旧会话还能加载。
12. 作为成本敏感的用户，我想要治理模块为空时不产生额外 LLM 调用，以便本轮不增加费用。

## 实现决策

### 模块职责

- **history**：单一 append-only 列表，只存原始记录与摘要。保留追加并分配 id、`get_history` 返回副本、`clear`、`to_dict` / `load_from_dict`、`estimate_rounds` / `find_round_boundaries`，以及供 builder 判断派生状态是否失效的 `revision`。删除上下文构建、token 计量、摘要生成和 `NotEnoughRoundsError`。
- **pruner**：`ContextPruner`，接口 `prune(messages) -> List[Message]`。本轮空实现，原样返回输入；不读写文件、不改写 history。
- **summary**：`ContextSummarizer`，接口 `maybe_summarize(messages) -> Optional[Message]`。本轮空实现，返回 `None`。
- **builder**：`ContextBuilder(history, config)`，接口 `build(system_prompt=None, extra_messages=None) -> List[Dict]`。负责开关分支、组装和 token 计量。
- **Agent**：持有 `HistoryManager` 与 `ContextBuilder`，保留 `history_manager` 普通兼容属性；保留 `_history` 代理。
- **code_agent**：每次 LLM 调用前用 builder 构建上下文；工具结果全文写入 history；`record_prompt_tokens` 调 builder；收尾用 `extra_messages` 追加临时指令。

### 三个开关与组装

- 三个开关：`context_truncation_enabled`、`context_summary_enabled`、`context_recall_enabled`，默认全部 `false`。
- builder 取 history 后依次检查三个开关：
  - `context_truncation_enabled` 为真：占位分支，本轮 `pass`。
  - `context_summary_enabled` 为真：占位分支，本轮 `pass`。
  - `context_recall_enabled` 为真：占位分支，本轮 `pass`。
- `context_summary_enabled` 为假时，直接丢弃 `role == "summary"` 的消息；为真时保留 summary 消息。该判断同时覆盖三开关全关和只开截断/召回的情况。
- 本轮不实现 summary.replace 遮蔽；`Message.replace` 字段保留，等摘要算法重写时再消费。
- 未实现的分支直接 `pass`。

### 历史数据模型

- `Message` 字段不变：`id`、`content`、`role`、`tool_call_id`、`tool_calls`、`replace`、`timestamp`、`metadata`。
- 摘要仍是 `role="summary"` 的消息，`replace` 记录被遮蔽原始消息的 id 区间；本轮 builder 不消费 `replace`，发送时 summary 映射为 user。
- 工具结果作为 `role="tool"` 消息，`content` 为完整文本，保留 `tool_call_id`。
- 序列化格式保持 `history` / `next_id` / `created_at` / `rounds` 不变，旧会话可直接加载。
- 加载时按已有 id 重算 `next_id`；不再为缺失 id 的旧格式补号。
- id 只增不减；`clear` 只清消息，不重置 id 计数器；`revision` 在 `clear` / `load_from_dict` 时递增，供 builder 重置计量状态。

### Builder 契约

- 输入：history 快照、可选的 system prompt、可选的 `extra_messages`、config。
- 输出：OpenAI 风格消息字典列表；system 在最前；assistant 的 `tool_calls` 和 tool 的 `tool_call_id` 原样保留。
- 组装：system（若有）+ 经过开关处理的消息列表 + `extra_messages`（若有）。
- `extra_messages` 只用于本次请求，不写入 history；收尾指令走这个入口。
- `build` 每次返回新列表；不改写 history。
- 三个开关只控制是否进入占位分支，不产生裁剪或摘要效果。

### Token 计量

- builder 维护“已计量真实 prompt_tokens + 对应历史右边界”；未计量尾部按字符数除以 4 估算。
- `record_prompt_tokens(prompt_tokens)` 由 code_agent 在每次 LLM 返回后调用；计量覆盖当前完整历史。
- `estimate_message_tokens(message)` 使用渲染后字符数除以 4。
- `should_compress()` 的口径：已计量 token + 未计量尾部估算 > `context_window * compression_threshold`，严格大于才触发。本轮没有调用方。
- Config 保留 `context_window`（默认 128000）和 `compression_threshold`（默认 0.8）。
- history 清空或加载后，builder 的计量状态归零。

### Agent 集成

- Agent 创建 `HistoryManager` 和 `ContextBuilder`；`history_manager` 保持普通兼容属性，不支持运行中替换后自动换绑 builder。
- code_agent 在每次 LLM 调用前调用 builder 的 `build`；收尾路径调用 `build(..., extra_messages=[...])`。
- 工具执行直接返回完整文本，作为 tool 消息写入 history；不再生成预览或落盘路径。
- 删除 `_summarize_if_needed`；本轮不再有摘要调用或摘要 usage 记账。
- `last_summary_usage`、`NotEnoughRoundsError` 及对应测试一并删除。
- Trace 的 `tool_result` 仍记录完整结果；若启用 session 保存，历史文件也会包含全文。

### 配置清理

- 保留：三个开关、`context_window`、`compression_threshold`。
- 删除：`max_history_length`、`min_retain_rounds`、`enable_smart_compression`、`summary_llm_provider`、`summary_llm_model`、`summary_llm_api_key`、`summary_llm_base_url`、`summary_max_tokens`、`summary_temperature`、`tool_output_max_lines`、`tool_output_max_bytes`、`tool_output_dir`、`tool_output_truncate_direction`。
- 后续实现摘要/裁剪时按需加回算法参数。
- 这是破坏性配置变更；仓库内 eval、测试、README 同步更新，外部调用方需要跟着改。

### 删除项

- 旧 GSSC 的 `ContextPacket`、`ContextConfig`、`count_tokens` 与 `tiktoken` 引用；`ContextBuilder` 类名保留、实现重写。
- `ObservationTruncator`、落盘写入、eval 中的工具输出目录常量与 `tool_output_dir` 传参。
- `HistoryManager` 中的上下文构建、token 计量、摘要生成、`NotEnoughRoundsError`。
- 旧测试中针对截断、摘要、`build_context` 的用例；`should_compress` 的用例迁移到 builder 测试。

### 取舍与风险

- 工具全文入 history 换取可回溯性，代价是长会话更容易撑爆上下文；本轮不做溢出检查。
- 三个治理模块留空换取算法设计空间，避免在策略未定时固化接口；代价是开关打开也没有裁剪或摘要效果。
- 摘要开关打开时保留 summary 消息，但本轮不遮蔽，旧摘要与原文可能同时出现；这是后续摘要算法要处理的问题。
- token 计量放 builder 让 history 职责更纯，但 history 清空/加载时必须同步重置 builder 状态。
- 删除旧配置让配置面干净，但对仓库外调用方是破坏性变更；仓库内调用点本轮全部更新。
- trace 与 session 可能因全文工具输出而膨胀；本轮不加额外限流，作为已知副作用记录。

## 测试决策

### Seam A：`CodeAgent.stream_run()`

用假 LLM 记录每次请求的 `messages`，用假工具返回超长文本；不联网、不调用真实模型、不启动 Docker。重点验证：

- 工具结果全文出现在下一次 LLM 请求中，没有截断标记、没有落盘文件、没有“完整内容见路径”提示。
- `record_prompt_tokens` 写入 builder，会话 token 统计口径不变。
- 收尾路径的 `extra_messages` 只出现在收尾请求，不写入 history。
- 三开关本轮不产生额外输出或摘要调用。

参考现有 `test/core/test_code_agent_usage_acceptance.py` 和 `test/core/test_code_agent_usage_selfcheck.py` 的假 LLM 模式。

### Seam B：`ContextBuilder.build()`

直接构造 `HistoryManager` + `Config` 调用 builder，不经过 Agent 循环。重点验证：

- 三开关全关：system + 历史，summary 消息被丢弃。
- 只开截断或召回：summary 消息仍被丢弃。
- 开摘要：summary 消息保留，验证其映射为 user；本轮不做 replace 遮蔽。
- `extra_messages` 追加在历史之后，且不进入 history。
- `build` 每次返回新列表，不产生额外输出。
- token 计量：`record_prompt_tokens`、`estimate_message_tokens`、`should_compress` 的严格大于口径；clear/load 后状态归零。
- 存储前置：append 的 id 递增、显式 id 推进计数器、`get_history` 返回副本、`to_dict` / `load_from_dict` 往返。

好测试的标准：确定性、无网络、只断言公开契约；不要为已删除的行为保留测试。

### 测试落点

- 重写 `test/context/test_history.py`：存储、id、轮次、序列化用例。
- `test/context/test_builder.py`：覆盖 Seam B。
- `test/core/test_code_agent_context.py`：覆盖 Seam A。
- `test/core/test_code_agent_usage_selfcheck.py`：保留收尾/用量用例，不再有摘要 usage 用例。
- 验收口径：`pytest test/context test/core` 通过；不产生落盘文件；不访问网络。

## 不在范围

- 截断、摘要、历史召回三个模块的算法本身。
- 摘要 LLM 的模型、prompt、温度、费用与 usage 记账。
- 上下文溢出处理、自动丢弃旧轮次、预算强制执行。
- 工具输出的磁盘审计副本、trace/session 体积限流。
- 旧 GSSC 流水线的恢复或兼容层。
- SimpleAgent、ReflectionAgent 等其他 Agent 的上下文构建改造；只保证它们依赖的 history 读写接口不变。
- 旧会话的主动迁移或清洗；只保证兼容加载。
- CLI/UI 新增开关或展示变化。
- 除字符数除以 4 之外的 token 估算优化。

## 补充说明

- 术语见根目录 `GLOSSARY.md`；架构决策见 `docs/adr/0001-context-v2.md`。
- 默认值：三个开关 `false`；`context_window=128000`；`compression_threshold=0.8`。
- 未实现的治理模块统一写“跳过/空实现”。
- 兼容性：旧会话可加载；旧配置字段删除属于破坏性变更，README 需要同步说明。
- 后续实现治理模块时，在 builder 对应 `if` 分支填入 pruner/summary/recall，并按需加回算法参数；本轮不预留在用的算法配置。
- 本轮不新增 `ContextManager` 门面；`HistoryManager` 继续作为“上下文管理器”的落点。

批准：lyd @ 2026-11-15
