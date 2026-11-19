# 评审：修复摘要保留区不足时的越界/空摘要问题，并将触发判断改为锚点加实际视图尾部的双阈值估算

对照：`docs/sdlc/fix/context-mudule-bug/spec.md` / 3 张票（`issues/01..03`）/ `feb5113..HEAD` 的 diff（1 个提交，6 个文件）。

## 结论

通过：`ContextBuilder` 与 `ContextSummarizer` 的选区切分、动态让出（可见 assistant 轮数不超过配置值时每次只保留 `A - 1` 轮）和双阈值估算（字符数与 UTF-8 字节数分别除以 4 向上取整）主逻辑正确，未发现运行时 bug；`README.md:151-153` 的摘要章节与已知限制，以及保留区裁剪视图、遮蔽消息过滤、锚点加尾部双阈值、`clear/load` 重算 4 处单测均已补齐，`python -m pytest test/context test/core -q` 228 passed。

## 必须修（阻塞）

- [x] [需求] `README.md` 未同步摘要新选区、锚点双阈值估算及已知限制 — `README.md:151-153`；影响：文档仍声称“压缩区为空仍会调用模型并 append；保留区自身超阈值时可能每步重复摘要”，违反 `issues/03-anchor-lifecycle.md`“README 摘要章更新：A<2 跳过、逐轮让出、严格 assistant 切分、锚点 + 双阈值估算、无 tokenizer”；怎么改：按 `spec.md` 与 Ticket 03 更新 `README.md:151-153` 的 Token 计量、摘要选区及已知限制（含无锚点不计 `system`/tool schema、锚点内旧消息缩小会短暂高估）。
- [x] [质量] `test_summary_uses_pruned_keep_view` 在新选区下将裁剪后的 `tool` 消息划入压缩区并删去了对裁剪文本的断言 — `test/context/test_builder.py:798-832`；影响：`context_summary_keep_rounds=1` 只保留 `a5`，`id=4` 的长 `tool` 消息被摘要替换后不再出现在返回上下文中，即使删掉 `self.pruner.prune(messages)` 该测试也照样通过；怎么改：将该用例的 `context_summary_keep_rounds` 改为 `2` 使 `id=4` 的 `tool` 消息留在保留区（不参与本轮摘要、原样进入上下文的消息段），并恢复对返回上下文中裁剪占位文本的断言。
- [x] [质量] `test_masked_messages_are_not_reestimated` 把锚点（最近一次主循环成功请求的真实 `prompt_tokens` 与最大消息 id）设在 `summary` 之后，短路了遮蔽过滤 — `test/context/test_builder.py:1007-1022`；影响：`_anchor_view` 将锚点边界设为 `1`，`id=0` 的长消息因 `id <= 1` 被锚点边界跳过而非被 `summary` 遮蔽，即使 `replace=[]` 测试也通过，未验证 `spec.md`“被遮蔽的旧消息不重复计入”；怎么改：去掉追加 `summary` 后的 `_anchor_view`（或先锚定 `u0` 再追加长消息与 `summary`），分别断言 `replace=[]` 时返回 `True`、`replace=[1]` 时返回 `False`。
- [x] [质量] 触发估算测试只测了 `P = 0` 或尾部为空（`C = 0, B = 0`），未验证锚点 `P` 与尾部估算（锚点之后新增视图消息与临时消息的 token 估算）相加 — `test/context/test_builder.py:944-974`；影响：原有的 `test_should_summarize_counts_unmeasured_tail` 被删后，若代码把 `prompt_tokens + char_tokens` 误写为 `max(prompt_tokens, char_tokens)` 测试仍全部通过，未覆盖 `spec.md`“触发条件是 `P + C > context_window × compression_threshold` 或 `P + B > context_window`”；怎么改：新增 `0 < P < 50` 且 `0 < C < 50`（以及 `0 < P < 100` 且 `0 < B < 100`）的组合测试，验证 `P + C == 50`、`P + B == 100` 时不触发、加 1 后触发。
- [x] [质量] `clear/load` 后重算测试未区分“清空锚点并按实际视图估算”与“未清空锚点或按原始 history 估算” — `test/context/test_builder.py:1058-1102`；影响：`clear()` 后追加的 `"x" * 170`（`id=1 > 0`）自身已超阈值（`C = 51 > 50`），无论旧锚点 `P = 45` 是否清空都会返回 `True`；`load_from_dict` 加载的会话不含 `summary` 遮蔽或裁剪消息，无法验证加载后按实际视图而非原始 history 估算；怎么改：`clear()` 后先用 `"x" * 169`（`C = 50`）断言 `False` 再追加消息断言 `True`，并在 `load_from_dict` 用例中加载含长消息与遮蔽它的 `summary` 的会话断言返回 `False`。

## 建议修（不阻塞）

- [ ] [质量] `ContextBuilder._current_view` 与 `build` 重复编写了拉取历史、裁剪和摘要遮蔽逻辑（可讨论） — `agents/context/builder.py:60-84`、`agents/context/builder.py:215-224`；影响：视图预处理分散在两处，后续修改裁剪或遮蔽顺序时容易使估算视图与发送视图脱节；怎么改：在 `build` 中调用 `messages = self._current_view()` 生成初始视图，再在 `summarize` 开启时执行摘要触发与生成。
- [ ] [质量] `select_summary_region` 包含契约外的 `if keep_rounds < 1: return [], visible` 分支 — `agents/context/summary.py:219-220`；影响：`spec.md` 规定“`select_summary_region` 只处理 A ≥ 2，不修改输入；A < 2 由 builder 拦截”，在 `A >= 2` 且 `context_summary_keep_rounds >= 1` 下该分支不可达；怎么改：删除 `if keep_rounds < 1: return [], visible`。
- [ ] [质量] `_visible_assistant_count` 与 `select_summary_region` 重复解析头部 `summary` 后的可见消息（可讨论） — `agents/context/builder.py:100-104`、`agents/context/summary.py:209-215`；影响：遮蔽列表头部放 `summary` 的结构约定跨两个类重复；怎么改：由 `ContextSummarizer` 提供提取可见消息或计数的方法供 `ContextBuilder` 调用。
- [ ] [质量] `_estimated_tail` 的 `extra_messages` 缺少类型注解且与 `messages` 分两个循环重复序列化累加（可讨论） — `agents/context/builder.py:169-186`；影响：与同文件 `extra_messages: Optional[List[Dict[str, Any]]]` 签名风格不一致，且序列化与字节累加逻辑重复；怎么改：补齐类型注解，将需要计数的 payload 合并迭代或提取单条累加辅助逻辑。
- [ ] [质量] `test_summary.py` 缺少 `2 < A <= N` 的 `select_summary_region` 单测 — `test/context/test_summary.py:137-149`；影响：Seam B 仅在 `A = 2`（此时 `A - 1 == 1`）下测试了 `A <= N`，单测本身无法区分 `keep = A - 1` 与固定保留 1 轮；怎么改：在 `test/context/test_summary.py` 中增加 `A = 3, context_summary_keep_rounds = 10` 的选区断言。
- [ ] [质量] 无锚点测试未显式传入 `system_prompt` 验证其不参与估算 — `test/context/test_builder.py:994-998`；影响：`issues/03-anchor-lifecycle.md` 要求“无锚点时 P 按 0 计，system/tool schema 不计入；行为有确定性测试”，当前仅调了无参 `should_summarize()`；怎么改：补充在 `"x" * 169` 边界下调用 `build(system_prompt="S" * 1000)` 仍不触发摘要的断言。
- [ ] [质量] 两条测“收尾触发摘要”的 `CodeAgent` 用例在无锚点下第 1 步主循环就触发了摘要，测试直接把预期从 1 次改为 2 次 — `test/core/test_code_agent_summary_context_usage.py:193-210`、`test/core/test_code_agent_summary_usage.py:239-263`；影响：混入了主循环摘要，偏离了用例原本只验证 `_conclude` 触发摘要记账的单一场景；怎么改：在 `_seed_history(agent)` 后调用 `agent.context_builder.record_prompt_tokens(10)` 建立初始锚点，使第 1 步主循环不触发、仅收尾阶段触发 1 次摘要。
- [ ] [质量] `_token_builder` 与 `_summary_builder` 维护了相同的默认 `Config` 字典（可讨论） — `test/context/test_builder.py:56-68`、`test/context/test_builder.py:873-881`；影响：同一测试文件内重复定义相同夹具配置；怎么改：合并默认配置字典或复用 `_summary_builder`。

## 需求完成度

| 需求 | 结论 | 证据 |
| --- | --- | --- |
| R1 触发后 `A < 2` 不调用摘要 LLM、不 append、无 outcome，返回遮蔽后完整视图（含旧摘要） | 已实现 | `agents/context/builder.py:68-71`、`test/context/test_builder.py:615-644` |
| R2 `A = 2` 保留 1 轮，压缩区含触发该 assistant 的 user 请求，保留区从该 assistant 开始 | 已实现 | `agents/context/summary.py:216-224`、`test/context/test_builder.py:647-659`、`test/context/test_summary.py:137-149` |
| R3 `A ≤ N` 每次触发让出最旧 1 轮，连续超阈值逐轮让出，无空压缩区摘要，最终停在 1 轮并跳过 | 已实现 | `agents/context/builder.py:68-81`、`test/context/test_builder.py:662-682` |
| R4 `A > N` 保留 N 轮、压缩其余可见消息，保留区不回退到 user | 已实现 | `agents/context/summary.py:216-224`、`test/context/test_summary.py:120-134`、`test/context/test_summary.py:170-181` |
| R5 有旧摘要走增量摘要（含旧摘要 + 压缩区），无旧摘要走普通摘要 | 已实现 | `agents/context/builder.py:72-78`、`test/context/test_builder.py:685-729`、`test/context/test_builder.py:770-795` |
| R6 `select_summary_region` 只处理 `A ≥ 2` 且不修改输入，`A < 2` 由 builder 拦截 | 已实现 | `agents/context/summary.py:199-224`、`test/context/test_summary.py:199-211` |
| R7 锚点记录真实 `prompt_tokens` 与实际发送视图最大消息 id，主循环成功请求后刷新 | 已实现 | `agents/context/builder.py:35-38`、`agents/context/builder.py:155-167`、`agents/agent/code_agent.py:195` |
| R8 尾部只取实际视图中 `id > 锚点边界` 的消息加 `extra_messages`，按裁剪/遮蔽后视图计 | 已实现 | `agents/context/builder.py:169-186`、`test/context/test_builder.py:798-832`、`test/context/test_builder.py:992-1039` |
| R9 估算对象为实际发送 payload（`role/content/tool_calls/tool_call_id`）的 JSON | 已实现 | `agents/context/builder.py:150-153`、`agents/context/builder.py:179-185`、`test/context/test_builder.py:976-989` |
| R10 `C = ceil(字符数 / 4)` 与 `B = ceil(UTF-8 字节数 / 4)` 分别可单独触发，等于阈值不触发 | 已实现 | `agents/context/builder.py:202-213`、`test/context/test_builder.py:912-941` |
| R11 `P + C > window × threshold` 或 `P + B > window` 任一触发，有锚点不重复计 system/tool schema | 已实现 | `agents/context/builder.py:200-213`、`test/context/test_builder.py:944-973` |
| R12 `clear/load` 后清空锚点，按整个实际视图 + `extra_messages` 估算，`P` 按 0 计 | 已实现 | `agents/context/builder.py:139-148`、`test/context/test_builder.py:1049-1102` |
| R13 收尾请求与摘要调用不刷新锚点，主循环请求刷新 | 已实现 | `agents/agent/code_agent.py:195`、`test/core/test_code_agent_summary_usage.py:239-263` |
| R14 无锚点时 `P` 按 0 计、system/tool schema 不计入，有确定性测试并写入已知限制 | 已实现 | `agents/context/builder.py:204-208`、`test/context/test_builder.py:1042-1046`、`README.md:153` |
| R15 裁剪/摘要使锚点范围内旧消息变小时短暂高估写入已知限制，不做补偿 | 已实现 | `spec.md:87`、`README.md:153` |
| R16 `README.md` 摘要章同步新选区、锚点 + 双阈值估算与无 tokenizer | 已实现 | `README.md:151-153` |
| R17 现有 `CodeAgent` 接线测试调整并通过，`context/core` 测试全绿且不联网/不写 trace | 已实现 | `test/core/test_code_agent_summary_context_usage.py:193-210`、`test/core/test_code_agent_summary_usage.py:217-263` |

## 怎么验证

```bash
python -m pytest test/context test/core -q
```

## 未覆盖

无
