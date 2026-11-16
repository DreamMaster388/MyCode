# Spec: 上下文截断 pruner 与轮次口径

作者：lyd。状态：已批准。

一句话：在 `context_truncation_enabled` 分支启用 pruner，对工具消息按“最近若干轮”和字符阈值做视图级裁剪；轮次改为按 assistant 消息计数，参数全部进入 Config。

## 问题

编码智能体在长任务里会连续调用工具，单条工具输出可能很长。当前截断模块是空实现，工具结果全文直接进入每一次请求：

- 单条 Read/Bash/Grep 结果可能挤占大量窗口，长任务更容易触发上下文溢出或高 token 成本。
- 用户希望保留最近工具输出的头尾，以便继续判断；又不希望旧工具输出一直占着窗口。
- 旧“轮”按 user 消息计数，和 agent 循环不一致：一次用户请求里可能发生很多次模型返回，按用户请求算轮无法表达“最近 N 次模型返回”。
- 直接按固定字符裁剪可能越裁越长，或把短旧消息替换成更长的 marker，反而增加上下文。

## 方案

在 builder 的截断分支调用 pruner，对 `role="tool"` 的消息做视图级裁剪，不改写 history：

- 最近若干轮内的工具消息：长度小于阈值不动；否则保留前 head 个字符和后 tail 个字符，中间替换为指向“记忆 i”的 marker。
- 轮次范围外的工具消息：整体替换为指向“记忆 i”的 marker。
- 只有替换结果严格短于原文才替换，避免越裁越长。
- 轮次改为按 assistant 消息计数：每条写入历史的 assistant 消息算一轮，正常路径对应一次成功模型返回，失败兜底文本也占一轮。
- 四个参数进入 Config；非法组合不报错，自动回退到默认值；开关默认仍关闭。
- 裁剪只影响发给模型的视图，history 仍保留全文；“记忆 i”本轮只是指路，不新增读取工具。

## 用户故事

1. 作为编码智能体的用户，我想要长工具输出在上下文中被限制，以便长任务不因单条输出而迅速耗尽窗口。
2. 作为编码智能体的用户，我想要最近工具输出保留开头和结尾，以便我仍能看到命令结果的关键部分。
3. 作为编码智能体的用户，我想要旧工具输出变成简短指针，以便窗口留给最近的工作。
4. 作为智能体开发者，我想要轮数按成功模型返回计数，以便“最近 N 轮”和 agent 循环对齐。
5. 作为智能体开发者，我想要轮数、阈值、头尾长度都可配置，以便按模型窗口和任务类型调参。
6. 作为智能体开发者，我想要裁剪只在结果更短时发生，以便配置不会意外增加上下文。
7. 作为智能体开发者，我想要 pruner 不改写 history，以便完整工具输出仍可审计。
8. 作为智能体开发者，我想要 marker 指向 history 下标，以便从上下文定位原始记录。
9. 作为智能体开发者，我想要无效的头尾/阈值组合在 Config 创建时自动回退到默认值，以便配置错误不会中断会话。
10. 作为会话持久化用户，我想要 `rounds` 元数据按新定义输出，以便会话统计与新口径一致。
11. 作为未来摘要/召回模块的开发者，我想要所有治理模块共享同一“轮”定义，以便组合时边界一致。
12. 作为测试维护者，我想要确定性的 builder/history 测试，不联网、不启动工具，以便快速验证裁剪规则。
13. 作为维护者，我想要只裁剪 `role="tool"` 消息，不改动 system/assistant/summary，以便裁剪边界清晰。
14. 作为成本敏感的用户，我想要最近一轮的超长工具输出也受单条上限约束，以便单条结果不能无限膨胀。
15. 作为审计者，我想要“记忆 i”指向原始记录下标而不是消息 id，以便在 clear/load 后仍按当前列表定位。

## 实现决策

### 模块职责

- `ContextPruner`：`prune(messages) -> List[Message]`。只处理 `role == "tool"`；返回新列表，被裁剪消息是副本，不修改输入对象；不读写文件、不改写 history。无状态，参数来自 `Config`。
- `ContextBuilder`：在 `context_truncation_enabled` 为真时，对 history 快照调用 pruner；调用发生在丢弃 summary 之前。system 提示词不在历史中，不参与裁剪；`extra_messages` 是临时消息，不参与裁剪。
- `HistoryManager`：`estimate_rounds()` 改为数 assistant 消息；`find_round_boundaries()` 返回 assistant 消息下标；`to_dict()["rounds"]` 使用新计数。`load_from_dict` 不消费 `rounds`。
- `Config`：新增四个字段；非法组合回退默认值；`context_truncation_enabled` 默认 `false`。

### 轮次规则

- 轮数 = 输入列表中 `role == "assistant"` 的消息数，记作 N。
- 对下标 j 的工具消息，记 A 为 j 之前（严格小于 j）的 assistant 消息数。
- 若 `N - A < context_truncation_rounds`，该工具消息在最近若干轮内；否则在轮次范围外。
- 所有 assistant 消息都计数：工具调用中间回复、最终纯文本回答、失败兜底写入的错误文本。
- 没有 assistant 消息时，工具消息的 `N - A = 0`，按最近轮处理。
- `estimate_rounds`、`find_round_boundaries`、pruner 使用同一计数口径；`user` / `tool` / `system` / `summary` 不计轮。

### 裁剪规则

输入为 pruner 收到的消息列表，下标 i 从 0 开始。

**最近轮内的工具消息：**

- 若 `len(content) < context_truncation_threshold`，保持原 content。
- 否则候选为：`content[:head] + "\n" + marker + "\n" + content[-tail:]`（`tail == 0` 时尾部为空，但 marker 后的换行保留）。
- marker：`已裁剪，详情参见记忆{i}中偏移{head}，长度{l}`，其中 `l = len(content) - head - tail`。
- 只有 `len(候选) < len(content)` 时才替换，否则保持原文；候选长度包含新增的两个换行。

**轮次范围外的工具消息：**

- marker：`已裁剪，详情参见记忆{i}中偏移0，长度{len(content)}`。
- 只有 `len(marker) < len(content)` 时才替换，否则保持原文。

**下标 i：**

- i 是 pruner 输入列表的下标，不含 builder 注入的 system、含 summary；不是 `Message.id`。
- 裁剪在丢弃 summary 之前，因此 summary 占位会让 i 与最终发送列表下标不一致；marker 只对 history 负责。
- 工具消息被裁剪后保留 id、tool_call_id、role、metadata 等其余字段；`to_llm_dict` 行为不变。

### Config 契约

- `context_truncation_rounds: int = 10`
- `context_truncation_threshold: int = 2000`
- `context_truncation_head_chars: int = 1500`
- `context_truncation_tail_chars: int = 500`
- `context_truncation_enabled: bool = False`
- 校验：`rounds >= 0`、`threshold > 0`、`head >= 0`、`tail >= 0`、`head + tail <= threshold`；任一条件不满足时，不报错，把这四个裁剪参数整组重置为默认值（10 / 2000 / 1500 / 500），`context_truncation_enabled` 保持传入值。
- 默认值下 head + tail == threshold；`threshold` 是触发裁剪的下界，`len(content) == threshold` 也会进入候选计算，但候选通常因 marker 变长而保持原文。
- 不新增环境变量、CLI 或 UI 入口；字段只在 Config 中使用。

### 取舍与风险

- 最近若干轮包含当前轮，因此最新一条超长工具输出也会被裁剪；这是用单条上限换“最新结果完整可见”。
- 只有更短才替换，避免近阈值内容和短旧消息越裁越长；代价是部分旧短消息仍保留全文。
- marker 指向 history 下标但不提供读取工具；模型看到的是指针，不能在本轮直接取回全文。这是用简单实现换“记忆读取”模块的设计空间。
- 轮次口径改为 assistant 后，`rounds` 的语义变化；本项目尚无已保存旧会话，不需要兼容旧统计口径。后续摘要/召回应沿用同一口径，避免边界漂移。
- 非法配置静默回退默认值，可能掩盖配置错误；这是用可诊断性换会话不中断。
- token 计量保持现状：`record_prompt_tokens` 仍用真实 prompt_tokens 校准；`should_compress` 的未计量尾部仍按 history 原文估算，启用裁剪时可能偏保守。
- 裁剪只作用于发送视图，history 体积不会因 pruner 下降；trace/session 仍可能包含全文。

## 测试决策

### Seam A：`ContextBuilder.build()`

直接构造 `HistoryManager` + `Config(context_truncation_enabled=True)` 调用 builder，不经过 Agent 循环、不联网、不执行工具。重点验证：

- 最近轮内短工具消息保持原样；超长消息变成“头 + 换行 + marker + 换行 + 尾”，marker 的 i、偏移、长度正确。
- 轮次边界：工具消息之后 9 条 assistant 时仍在最近 10 轮内；10 条时进入整体替换。
- 旧工具消息整体变成 marker；短旧消息在 marker 更长时保持原样。
- 近阈值消息在候选更长时保持原样；足够长时才替换；候选长度比较包含换行。
- 非法配置（负 rounds、threshold 为 0、head+tail 超阈值等）不抛错，四个裁剪参数回退默认值，开关保持传入值。
- 只有 `role="tool"` 被处理；system/assistant/summary 不变。
- i 是裁剪输入列表下标：有 system 时不偏移；有 summary 时 summary 占位；summary 随后被丢弃。
- 裁剪在丢弃 summary 之前；`extra_messages` 不被裁剪。
- history 原始 content 不变；`build()` 每次返回新列表。
- `context_truncation_enabled=False` 时行为与现在一致。

### Seam B：`HistoryManager`

- `estimate_rounds()` 只数 assistant，覆盖空历史、纯 user、assistant、summary、tool、system 混合。
- `find_round_boundaries()` 返回 assistant 下标。
- `to_dict()["rounds"]` 使用新计数；`load_from_dict` 不依赖 `rounds`，当前格式保存/加载往返正常。
- clear/load 后 id 计数器与 revision 行为不变。

### 好测试的标准

确定性、无网络、只断言公开契约；用短字符串和显式 `len` 断言，避免依赖真实模型；不测试私有辅助函数；不为已删除的占位行为保留测试。

### 参考与更新

参考现有 `test/context/test_builder.py` 和 `test/context/test_history.py` 的构造方式。更新：

- 删除/替换 `test_pruner_stub_returns_input_unchanged`。
- 重写 `test_estimate_rounds` 的参数化期望。
- 重命名并改写 `test_find_round_boundaries_returns_user_indexes`。
- 更新 `test_to_dict_contains_history_next_id_rounds` 的 `rounds` 期望。
- 保持 `test/core` 现有 usage/收尾测试不受影响。

验收：`pytest test/context` 通过；测试不写文件、不访问网络、不调用真实模型。

## 不在范围

- 独立的“记忆”存储、按 i 读取全文的工具或 API。
- 摘要、历史召回的算法本身；summary 的 `replace` 遮蔽。
- 工具输出的磁盘持久化、trace/session 体积限流。
- 按裁剪后视图重算 token 计量，或让 `should_compress` 消费裁剪视图。
- 修改 Agent 循环、LLM 调用、工具执行或 `Message` schema。
- Config 的环境变量/CLI/UI 暴露；旧版本保存文件的兼容、迁移与回退路径。
- 对 assistant 的 `tool_calls`、user、system、summary 做内容裁剪。
- 上下文溢出处理、自动丢弃旧轮次、预算强制执行。
- 让模型理解或主动使用“记忆 i”的提示词工程。

## 补充说明

- 术语见根目录 [GLOSSARY.md](GLOSSARY.md)；架构取舍见 [0002-context-pruning-and-round-semantics.md](docs/adr/0002-context-pruning-and-round-semantics.md)。
- 默认值：开关 `false`；`rounds=10`；`threshold=2000`；`head=1500`；`tail=500`。
- 配置名沿用 `context_truncation_*` 前缀，与开关名一致。
- 兼容性：本项目尚无已保存旧会话，不保留旧格式兼容逻辑；`Message` 序列化按当前格式演进；`rounds` 按新口径定义。
- marker 是模型可见文本，格式固定；本轮不解析 marker，也不做记忆读取。
- 失败兜底文本计入轮数是刻意的简单口径；如后续要区分“成功返回”，需要给 assistant 增加来源标记并改计数，不在本轮。

## 收尾

本规格已批准；拆票见 `issues/` 目录。
