# Spec: 上下文构建体系 v2（朴素链路 + 链 2 骨架）

作者：lyd。状态：已批准。

一句话：工具结果全文入历史；上下文拆成 history / pruner / summary / builder 四层；本轮只实现朴素链路，链 2（截断 + 摘要 + 历史召回）留可运行骨架，三个开关默认全关，打开时告警并回退朴素。

## 问题

编码智能体在长任务里依赖工具输出做判断，但当前 `ObservationTruncator` 会把超长输出截成预览，完整内容落到 `tool-output/`，模型只能看到“完整内容见路径”的提示。模型无法直接使用完整结果，排查也依赖额外文件。

上下文策略不可组合：历史存储、token 计量、摘要、工具输出截断混在 `HistoryManager` 里，想只保留历史、关掉摘要或截断做不到。旧 `ContextBuilder` 是 GSSC 流水线，依赖已从依赖清单移除的 `tiktoken`，实际不可用。

重构需要一个稳定的默认链路，再逐步加能力；现有代码没有“朴素链路”和“链 2”的清晰边界，也没有对应的配置开关。旧配置、测试和 eval 仍引用 `tool_output_*`，重构后容易留下死路径。

## 方案

工具结果不再截断，完整文本直接写入历史；删除 `ObservationTruncator`、`tool-output/` 落盘路径和相关配置。

上下文包拆成四层：history 只存原始记录与摘要；pruner 负责裁剪；summary 负责摘要；builder 负责从原始记录组装上下文。builder 有两条链路：链路 1 是朴素拼接；链路 2 是截断 + 摘要 + 历史召回。三个开关分别控制链路 2 的三步，全关即链路 1。

本轮只实现链路 1；链路 2 保留可运行骨架：pruner 和 summary 提供空实现，打开任一开关时 builder 告警一次并回退朴素，不产生裁剪或摘要效果。

Agent 改用 builder 构建上下文；history 保留兼容读写接口；token 计量迁到 builder。删除旧 GSSC builder、`ObservationTruncator`、旧配置、旧测试和 eval 中的落盘传参，更新 README。

## 用户故事

1. 作为编码智能体的用户，我想要工具输出完整进入上下文，以便模型能直接看到完整结果而不是只有预览。
2. 作为编码智能体的用户，我想要默认走朴素拼接，以便本轮不会被未完成的裁剪/摘要算法影响。
3. 作为智能体开发者，我想要用三个独立开关分别控制截断、摘要和历史召回，以便按需组合链路 2。
4. 作为智能体开发者，我想要三个开关全关时自动走朴素链路，以便不需要额外的“朴素开关”。
5. 作为智能体开发者，我想要链路 2 未实现时打开开关会告警并回退，以便不会误以为裁剪/摘要已经生效。
6. 作为智能体开发者，我想要 summary 钩子在每次 build 内、组装前调用，以便未来摘要能在组装前压缩历史。
7. 作为智能体开发者，我想要朴素链路也应用已有 summary.replace 遮蔽，以便加载旧会话时不会重复发送摘要和原文。
8. 作为智能体开发者，我想要 builder 负责组装与 token 计量、history 只负责存储，以便职责清晰、可独立测试。
9. 作为智能体开发者，我想要工具结果全文写入 history 且不再落盘，以便删除一条重复的落盘路径。
10. 作为智能体开发者，我想要 pruner/summary 有明确的空接口和 builder 分支，以便下一轮直接填算法而不改 Agent 主循环。
11. 作为未来实现链路 2 的开发者，我想要 token 计量继续记录真实 prompt_tokens 和增量估算，以便预算判断有基线。
12. 作为维护者，我想要删除旧 GSSC builder、`ObservationTruncator` 和相关配置，以便不再维护不可用或重复的路径。
13. 作为评测/测试维护者，我想要 eval 和测试不再依赖 `tool_output_dir`，以便清理后仓库可运行。
14. 作为使用会话持久化的用户，我想要 history 序列化格式保持兼容，以便旧会话还能加载。
15. 作为成本敏感的用户，我想要摘要/裁剪未启用时不产生额外 LLM 调用，以便本轮不增加费用。

## 实现决策

### 模块职责

- **history**：单一 append-only 列表，只存原始记录与摘要。保留追加并分配 id、`get_history` 返回副本、`clear`、`to_dict` / `load_from_dict`、`estimate_rounds` / `find_round_boundaries`。删除上下文构建、token 计量、摘要生成和 `NotEnoughRoundsError`。
- **pruner**：`ContextPruner`，接口 `prune(messages) -> List[Message]`。本轮为空实现，原样返回输入；不读写文件、不改写 history。
- **summary**：`ContextSummarizer`，接口 `maybe_summarize(history) -> Optional[Message]`。本轮为空实现，返回 `None`；未来返回摘要消息时由 builder 追加到 history。
- **builder**：`ContextBuilder(history, config)`，接口 `build(system_prompt=None, extra_messages=None) -> List[Dict]`。负责 summary 钩子、开关判断、朴素组装和 token 计量。
- **Agent**：持有 `HistoryManager` 与 `ContextBuilder`，保留 `history_manager` 兼容属性和 `_history` 代理，删除 `truncator`。
- **code_agent**：每次 LLM 调用前用 builder 构建上下文；工具结果全文写入 history；`record_prompt_tokens` 改调 builder；收尾用 `extra_messages` 追加临时指令。

### 两条链路与开关

- 三个开关：`context_truncation_enabled`、`context_summary_enabled`、`context_recall_enabled`，默认全部 `false`。
- 三者全关：链路 1，system + 历史（按 summary.replace 遮蔽）+ `extra_messages`，不做溢出检查。
- 任一为 `true`：进入链路 2 准备阶段。本轮为骨架：若 `context_summary_enabled` 为真，先执行 summary 空钩子；本轮不执行 pruner；历史召回没有独立模块，只保留开关。随后 builder 首次进入链路 2 时告警一次，并回退链路 1 的结果。
- 告警不中断主循环；文案说明“链路 2 尚未实现，已回退朴素链路”。
- 未来链路 2 的顺序固定为：裁剪 → 摘要 → 历史召回，由 builder 统一编排；摘要仍在最终组装前执行。

### 历史数据模型

- `Message` 字段不变：`id`、`content`、`role`、`tool_call_id`、`tool_calls`、`replace`、`timestamp`、`metadata`。
- 摘要仍是 `role="summary"` 的消息，`replace` 记录被遮蔽原始消息的闭区间 id；发送给 LLM 时 summary 降级为 user。
- 工具结果作为 `role="tool"` 消息，`content` 为完整文本，保留 `tool_call_id`。
- 构建时由 builder 应用 summary.replace 遮蔽；支持闭区间、倒序区间、嵌套摘要、端点已缺失时的现有兜底规则。
- 序列化格式保持 `history` / `next_id` / `created_at` / `rounds` 不变，旧会话可直接加载，不做迁移。
- id 只增不减；`clear` 只清消息，不重置 id 计数器。history 清空或加载后，builder 的 token 计量状态必须重置。

### Builder 契约

- 输入：history 快照、可选的 system prompt、可选的 `extra_messages`、config。
- 输出：OpenAI 风格消息字典列表；system 在最前；assistant 的 `tool_calls` 和 tool 的 `tool_call_id` 原样保留。
- 朴素链路：system（若有）+ 遮蔽后的历史 + `extra_messages`（若有）。
- summary 钩子：仅当 `context_summary_enabled` 为真时，在组装前调用；若返回 `Message`，builder 追加到 history，后续快照会包含它。本轮返回 `None`。
- 执行顺序：三个开关全关时直接朴素组装；否则进入链路 2 准备阶段，按 裁剪（若启用）→ 摘要（若启用）→ 召回（若启用）编排，最后组装。本轮裁剪/召回不执行；摘要仅在开关开启时经过空钩子；随后告警并回退朴素。
- `extra_messages` 只用于本次请求，不写入 history；收尾指令走这个入口。
- `build` 每次返回新列表；除 summary 追加外不改写 history。
- 链路 2 开关打开时只告警并回退，不抛异常；告警每个 builder 实例只出现一次。

### Token 计量

- builder 维护“已计量真实 prompt_tokens + 对应历史右边界”；未计量尾部按 `len(str(message)) // 4` 估算。
- `record_prompt_tokens(prompt_tokens)` 由 code_agent 在每次 LLM 返回后调用；计量覆盖当前完整历史。
- `estimate_message_tokens(message)` 使用字符数除以 4。
- `should_compress()` 的口径：已计量 token + 未计量尾部估算 > `context_window * compression_threshold`，严格大于才触发。本轮朴素链路不调用它。
- Config 保留 `context_window`（默认 128000）和 `compression_threshold`（默认 0.8）。
- history 清空或加载后，builder 的计量状态归零，避免用旧基线判断新历史。

### Agent 集成

- Agent 创建 `HistoryManager` 和 `ContextBuilder`；`history_manager`、`add_message`、`_history` 读写保持兼容，其他 Agent 不受影响。
- code_agent 在每次 LLM 调用前调用 builder 的 `build`；收尾路径调用 `build(..., extra_messages=[...])`。
- 工具执行直接返回完整文本，作为 tool 消息写入 history；删除 `_run_and_truncate`，不再生成预览或落盘路径。
- 删除 `_summarize_if_needed`；summary 钩子改由 builder 在 build 内、组装前调用，本轮为空实现，因此本轮不再产生摘要 usage 记账。摘要 LLM 与 usage 记账在下一轮实现摘要时恢复。
- `last_summary_usage`、`NotEnoughRoundsError` 及对应测试一并删除。
- Trace 的 `tool_result` 仍记录完整结果；若启用 session 保存，历史文件也会包含全文。

### 配置清理

- 保留：三个开关、`context_window`、`compression_threshold`。
- 删除：`max_history_length`、`min_retain_rounds`、`enable_smart_compression`、`summary_llm_provider`、`summary_llm_model`、`summary_llm_api_key`、`summary_llm_base_url`、`summary_max_tokens`、`summary_temperature`、`tool_output_max_lines`、`tool_output_max_bytes`、`tool_output_dir`、`tool_output_truncate_direction`。
- `min_retain_rounds` 与 `summary_llm_*` 等摘要参数在下一轮实现链路 2 时按需加回。
- 这是破坏性配置变更；仓库内 eval、测试、README 同步更新，外部调用方需要跟着改。

### 删除项

- 旧 GSSC 的 `ContextPacket`、`ContextConfig`、`count_tokens` 与 `tiktoken` 引用；`ContextBuilder` 类名保留、实现重写。
- `ObservationTruncator`、`tool-output/` 写入、eval 中的 `TOOL_OUTPUT_DIR` 与 `tool_output_dir` 传参。
- `HistoryManager` 中的上下文构建、token 计量、摘要生成、`NotEnoughRoundsError`。
- 旧测试中针对截断、摘要、`build_context` 的用例；`should_compress` 的用例迁移到 builder 测试；history 存储用例保留或改写。

### 取舍与风险

- 工具全文入 history 换取可回溯性，代价是长会话更容易撑爆上下文；本轮朴素链路明确不做溢出检查。
- 链路 2 留空换取算法设计空间，避免在裁剪/摘要策略未定时固化接口；代价是打开开关只是告警回退，不是真实能力。
- token 计量放 builder 让 history 职责更纯，但 history 清空/加载时必须同步重置 builder 状态，否则基线会错位。
- 删除旧配置让配置面干净，但对仓库外调用方是破坏性变更；仓库内调用点本轮全部更新。
- trace 与 session 可能因全文工具输出而膨胀；本轮不加额外限流，作为已知副作用记录。

## 测试决策

### Seam A：`CodeAgent.stream_run()`

用假 LLM 记录每次请求的 `messages`，用假工具返回超长文本；不联网、不调用真实模型、不启动 Docker。重点验证：

- 工具结果全文出现在下一次 LLM 请求中，没有截断标记、没有 `tool-output/` 文件、没有“完整内容见路径”提示。
- `record_prompt_tokens` 写入 builder，会话 token 统计口径不变。
- summary 开关打开时，summary 钩子在 build 内、组装前被调用；关闭时不调用。
- 任一链路 2 开关打开时，告警一次并回退朴素，主循环不中断。
- 收尾路径的 `extra_messages` 只出现在收尾请求，不写入 history。
- 不再有 `_summarize_if_needed` 的额外调用或摘要 usage 记账。

参考现有 `test/core/test_code_agent_usage_acceptance.py` 和 `test/core/test_code_agent_usage_selfcheck.py` 的假 LLM 模式。

### Seam B：`ContextBuilder.build()`

直接构造 `HistoryManager` + `Config` 调用 builder，不经过 Agent 循环。重点验证：

- 全关时输出 system + 历史；system prompt 为空时不加 system 消息。
- summary.replace 遮蔽：闭区间、倒序区间、嵌套摘要、端点缺失兜底；summary 消息发送时降级为 user。
- `extra_messages` 追加在历史之后，且不进入 history。
- 三个开关的矩阵：逐个打开都告警一次并回退朴素，pruner 不被调用；全关不告警。
- summary 钩子顺序：在组装前调用；本轮返回 `None`；用 spy 返回 Message 时能被追加到 history（为下一轮预留）。
- token 计量：`record_prompt_tokens`、`estimate_message_tokens`、`should_compress` 的严格大于口径；clear/load 后状态归零。
- 存储前置：append 的 id 递增、显式 id 推进计数器、`get_history` 返回副本、`to_dict` / `load_from_dict` 往返、旧会话无 id 时补号。

好测试的标准：确定性、无网络、只断言公开契约；需要观察告警时用 `capsys` 或 `warnings.catch_warnings`；尽量不直接读私有字段，若 builder 计量状态没有公开读取口，补一个只读访问器。

### 测试落点

- 重写 `test/context/test_history.py`：只保留存储、id、轮次、序列化用例。
- 新增 `test/context/test_builder.py`：覆盖 Seam B。
- 新增或扩展 `test/core/test_code_agent_context.py`：覆盖 Seam A；可复用现有 usage 测试的假 LLM 与 `Config` 构造方式。
- 删除 `test/core/test_code_agent_usage_selfcheck.py` 中摘要 usage 相关用例；其余收尾/用量用例改用 builder 替身保留。
- 验收口径：`pytest test/context test/core` 通过；不产生 `tool-output/` 文件；不访问网络。

## 不在范围

- 链路 2 的裁剪算法、摘要算法、历史召回算法本身。
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
- 告警文案建议：`⚠️ 上下文链路 2 尚未实现，已回退朴素链路`；每个 builder 实例只出现一次。
- 兼容性：旧会话可加载；旧配置字段删除属于破坏性变更，README 需要同步说明。
- 下一轮实现链路 2 时，在 builder 的链路 2 分支填入 pruner/summary/recall，并加回 `min_retain_rounds`、`summary_llm_*` 等算法参数；本轮只保留 `should_compress` 需要的 `context_window` / `compression_threshold`，摘要/裁剪参数不预留。
- 本轮不新增 `ContextManager` 门面；`HistoryManager` 继续作为“上下文管理器”的落点。

批准：lyd @ 2026-11-15
