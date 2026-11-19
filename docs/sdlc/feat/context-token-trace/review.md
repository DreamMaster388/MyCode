# 评审：把每次 run 的输入 token 按类别/工具/摘要次数归集，并在两条 eval 路径汇总展示

对照：spec / 5 张票 / `08a23ab..HEAD` 的 diff（2 个提交，21 个文件）。三轴独立评审（代码质量、需求完成度、bug）结论已逐条复核，重复项合并；代码质量线报的一条“阻塞”复核后降为建议。

## 结论

通过：4 项阻塞已修复。工具区改为“先类别、后工具切片”，多调用按 `arguments` 长度分摊；结果映射、收尾触发摘要、Seam 1 标注三处测试已补。`python -m pytest test/context test/core test/eval -q` 424 passed。

## 必须修（阻塞，已修复）

本轮已全部修复，保留原始问题与验证方式：

- [x] [bug] 工具区没有从类别区已分配值切分，多调用时又按整段 call JSON 分摊 — `agents/context/usage.py:152-186`；已修复：`commit_request` 先落类别，再从 assistant 分配值取参数占比（`arguments` 合计 / 消息长度）按各 `arguments` 长度切给工具，`tool_result` 分配值整段归到 `tool_call_id` 对应的工具；验证：`test_multi_tool_call_split_follows_arguments_length`（`test/core/test_code_agent_tool_usage.py:164`）。
- [x] [需求] 工具结果按 `tool_call_id` 归名没有测试 — `test/core/test_code_agent_tool_usage.py:191`；已修复：新增 `test_tool_result_tokens_map_to_originating_tool`，同一 `tool_call_id` 的长结果使该工具 token 增大且不出现 `unknown`。
- [x] [需求] 收尾路径触发的摘要没有断言 `context_usage.summary_calls` — `test/core/test_code_agent_summary_context_usage.py:193`；已修复：新增 `test_summary_triggered_by_conclude_build_is_counted`（摘要只在 `_conclude` 的 build 触发），断言 `summary_calls == 1`、`summary_call == 40`、类别合计 190。
- [x] [需求] Seam 1 缺 `summary_message` / `conclude_instruction` 的标注断言 — `test/context/test_builder.py:158`；已修复：`test_build_labels_cover_summary_and_conclude_instruction` 断言 labels 含这两类、被遮蔽消息不在发送视图、labels 与 context 等长。

## 建议修（不阻塞）

- [ ] [质量] labels 缺失时按发送字典 `role` 反推类别 — `agents/context/usage.py:134`；影响：spec 第 38 行明确“调用方不得从发送字典的 `role` 反推 `summary_message`/`conclude_instruction`（它们发送时都是 `user`）”，这里摘要与收尾会被静默记成 `user`；两处调用都传了等长 labels，当前不可达；怎么改：删掉 `_category_from_message`（`:40-50`）与 `label in CATEGORY_KEYS` 兜底，labels 长度不符时抛错。
- [ ] [质量] `prompt_tokens` 的提取与判空写了三份 — `agents/agent/code_agent.py:193`、`:341`、`agents/context/usage.py:31`；影响：同一语义三处判定，`usage.py:36` 的 `input_tokens` 回退在生产适配器下不可达（`agents/core/llm_adapters.py:43-46` 同时写两个键），`code_agent.py:198` 的 `commit_request(None)` 又与 `abort_request()` 等价；怎么改：让 `commit_request` 接收 usage 并在内部统一取 `prompt_tokens`。
- [ ] [质量] `__init__` 里创建的 tracker 从未被读取 — `agents/agent/code_agent.py:60`；影响：`stream_run` 开头（`:93`）会整体重建，死赋值让人误以为是跨 run 累计器；怎么改：删掉构造里的赋值，只在 `stream_run` 开头创建。
- [ ] [质量] 零值形状在四处并列构造 — `eval/usage_summary.py:14`、`:50-54`、`agents/context/usage.py:104`、`:204`；影响：类别键或工具条目一变要同步四处；怎么改：`empty_context_usage()` 返回 `ContextUsageTracker().to_dict()`，totals 从该副本派生。
- [ ] [流程] 5 张票仍是 `ready-for-agent`、验收项全未勾选 — `docs/sdlc/feat/context-token-trace/issues/01-category-breakdown.md:7` 等 5 个文件；影响：与此前特性用 `[x]`/`done` 记录完成的惯例不一致；怎么改：勾选已完成项或改写状态。

## 需求完成度

| 需求 | 结论 | 证据 |
| --- | --- | --- |
| R1 01-1/01-2 builder 逐条标注，基于裁剪/摘要视图，不靠 role 反推 | 已实现 | `agents/context/builder.py:82-94`、`:96-107`；`test/context/test_builder.py:123`、`:137` |
| R2 01-3 按份额分配、最大余数、合计精确、无 usage 不分配 | 已实现 | `agents/context/usage.py:53-71`、`:152-186`；`test/core/test_code_agent_context_usage.py:103`；另用随机长度验证 `_allocate` 合计恒等于 total |
| R3 01-4/01-5 八键 schema 与既有总量/步数/请求次数不变 | 已实现 | `agents/context/usage.py:204-212`；`agents/agent/code_agent.py:285-293`；`test/core/test_code_agent_context_usage.py:103` |
| R4 02-1/02-4/02-5 工具区按名聚合参数+结果 token 与 calls，不改类别区 | 已实现 | `agents/context/usage.py:152-186`；新增 `test/core/test_code_agent_tool_usage.py:164` |
| R5 02-2/02-6 多 tool_call 分摊、结果按 id 映射、未知工具保留 | 已实现 | `agents/context/usage.py:134-186`；`test/core/test_code_agent_tool_usage.py:164`、`:191` |
| R6 03-1/03-2/03-3 summary/conclude 归位、摘要 usage 计入 input_tokens | 已实现 | `agents/context/builder.py:91-100`；`agents/context/usage.py:197-202`；`agents/agent/code_agent.py:137`；`test/core/test_code_agent_summary_context_usage.py:141` |
| R7 03-4/03-5 摘要次数按触发计（含无真实调用）、类别合计覆盖 | 已实现 | `agents/agent/code_agent.py:137`、`:272`、`:279`；`test/core/test_code_agent_summary_context_usage.py:141`、`:193` |
| R8 04 本地 eval 透传、零值形状、终端汇总同源、helper 复用 | 已实现 | `eval/agent_runner.py:99`、`eval/harness.py:84`/`:138`、`eval/usage_summary.py:46`/`:85`、`eval/run_eval.py:70`；`test/eval/test_usage_summary.py:27-166` |
| R9 05 容器顶层字段、diagnostics、predictions 不变、终端复用 | 已实现 | `eval/container_runner.py:206`/`:248`、`eval/run_predictions.py:168-169`/`:186`；`test/eval/test_container_usage_report.py:76-190` |
| R10 测试决策 Seam 1/2/3 | 已实现 | Seam 2/3 齐；Seam 1 见 `test/context/test_builder.py:158` |
| R11 票面状态与验收记录（流程） | 待人工确认 | 5 张票均 `ready-for-agent`、`[ ]` 未勾选（见建议末条） |

没被要求的多余改动：`agents/context/usage.py:40-50`、`:134` 的 role 反推兜底，见建议首条。

## 已核对，不成立

- `_ensure_usage` 的逐字段校验被标为“兼容/回退路径”：这是对同进程内存结果的归一化，不是为旧格式保留的分支，也不影响当前口径，不列为问题。
- `run_eval`/`run_predictions` 各自调用既有 `summarize` 与 `summarize_context_usage`：两者输入同为 results、字段互不覆盖，没有形成口径分叉，不列为问题。
- `extra_messages` 一律标 `conclude_instruction`：仓库内只有 `_conclude` 一个调用方，spec 也把 extra 定义为收尾临时指令，不列为问题（可讨论）。
- 收尾主通道返回空白 `content` 时该次 usage 被丢弃（bug 线报为建议）：复核 `08a23ab` 的旧实现，`_conclude` 同样只在 `text` 非空时返回该通道 usage，属既有口径，不是本分支引入；新类别数据沿用了同一口径。若要精确，另立票把两次真实请求都计入。
- `test/sandbox` 的 26 条 `PermissionError`：环境 landlock 权限导致，本分支未触碰该目录，与改动无关。

## 怎么验证

```bash
python -m pytest test/context test/core test/eval -q   # 424 passed
python -m pytest test --ignore=test/sandbox -q         # 491 passed
# 多调用按参数长度分摊、结果按 tool_call_id 归名：
python -m pytest test/core/test_code_agent_tool_usage.py -q
```

## 未覆盖

- 未跑真实模型、Docker 与网络；eval 终端输出只按假数据验证（spec 明确不测真实 harness）。
- 收尾请求 `tool_choice="none"` 下模型仍返回 `tool_calls` 时，是否计入 `tools.calls`：spec 未明确，当前不计，待人工拍板。
- 未在真实供应商响应上核对 `prompt_tokens` 是否总存在；按 `agents/core/llm_adapters.py:43-46` 代码确认会同时写 `prompt_tokens` 与 `input_tokens`。
- 前端与持久化不在范围，未审。
