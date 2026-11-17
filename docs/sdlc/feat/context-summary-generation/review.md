# 评审：摘要内容生成（普通 + 增量、降级、usage/trace）

对照：spec / 4 张票 / `adb9d3b..f35e6c1` 的 diff（1 个提交，14 个文件）。

## 结论

有条件通过：普通/增量摘要、失败降级、usage/预算/trace 的主路径已经实现，`test/context test/core` 192 passed；但有 1 个会静默丢上下文的数据边界，以及 3 个规格小偏差/测试缺口。阻塞项都能落到一行代码或一条缺失的测试，改法明确，修完可合。

## 必须修（阻塞）

- [ ] [bug] 模型只返回统一开头、正文为空时仍按成功处理 — `agents/context/summary.py:153`；影响：`content` 只剩开头声明、`degraded=False`，旧消息被 `replace` 永久遮蔽，trace 也看不出降级；怎么改：剥离开头后正文为空时走降级并写 `error`，补一条用例。复现：假 LLM 返回 `LLMResponse(content=SUMMARY_HEADER, usage={})`，当前得到 `degraded=False`。
- [ ] [需求] 系统提示词缺“使用中文” — `agents/context/summary.py:33`；影响：违反 spec“系统提示词固定四条”的最后一条，英文对话只能靠 user prompt 末尾一句兜底；怎么改：末条补“使用中文”，并在系统提示词断言里加该片段。
- [ ] [需求] 摘要输入未排除 `system` 消息 — `agents/context/summary.py:94`；影响：`Agent.add_message` 或 `HistoryManager.load_from_dict` 放入 history 的 system 消息会以 `[id=N][system]` 进入摘要 prompt，违反 spec“不包含 system 消息、timestamp、metadata”；怎么改：渲染前跳过 `role == "system"`，补一条用例。正常 CodeAgent 路径不产生 system，这是公共 seam 的合同偏差。
- [ ] [需求] CodeAgent seam 缺“摘要返回空串”降级用例 — `test/core/test_code_agent_summary_usage.py:192`；影响：spec 要求 Seam 3 覆盖异常或空串降级，目前只覆盖异常，也没有断言 history 最后一条是降级摘要；怎么改：新增 `summary_content=""` 用例，断言运行完成、history 末尾为降级摘要、trace `degraded=True`、usage 按真实调用记账。

## 建议修（不阻塞）

- [ ] [质量] `summarize` 与 `summarize_incremental` 各自重复“空内容降级 / 非空成功”分支 — `agents/context/summary.py:228`；影响：新增 `SummaryOutcome` 字段或调整降级兜底要同步 4 个 return，漏一处会导致普通/增量行为漂移；怎么改：抽统一构造 `SummaryOutcome` 的 helper。
- [ ] [质量] `_call_llm` 用四元组返回 content/usage/error/latency — `agents/context/summary.py:119`；影响：usage 三态与内容/错误混在一起，改签名要同步三处，按位置解包易错；怎么改：返回小 dataclass 或按属性读取。
- [ ] [质量] `_conclude` 的预算分支和 max_steps 分支各写一次 `_consume_summary` — `agents/agent/code_agent.py:254`；影响：将来新增收尾路径时容易只搬 `_conclude` 而漏记账；怎么改：让 `_conclude` 返回 outcome 或抽统一收尾 helper。
- [ ] [质量] 两个测试文件各维护一份假 LLM — `test/context/test_builder.py:16`、`test/context/test_summary_llm.py:26`；影响：假客户端构造或 usage 语义变化要改两处；怎么改：抽到 `test/context/conftest.py` 共用。
- [ ] [可观测] trace HTML 面板的 Token/模型调用次数不含摘要调用 — `agents/observability/trace_logger.py:215`；影响：`_session_metadata` 已含摘要 usage，但 `_compute_stats` 只统计 `model_output`，面板总 Token 与模型调用次数低估，复盘时摘要成本不可见；怎么改：把 `summary` 事件的 usage 计入，或单列“摘要调用/摘要 Token”。
- [ ] [需求] 异常 `error` 未截断 — `agents/context/summary.py:137`；影响：SDK 异常的 message 可能带完整响应体，trace 中的 error 会很长，违反 spec“只保留简短错误信息”；怎么改：只保留异常类型和首行，或截断到固定长度。
- [ ] [文档] 票 01/02 的完成描述仍是阶段性占位 — `docs/sdlc/feat/context-summary-generation/issues/01-summary-outcome.md:10`、`:15`、`02-normal-summary.md:18`；影响：票面已勾选 done，却写着“content 仍为空 / 增量不接入 LLM”，读者会误判最终状态；怎么改：加一句“03 已替换占位”，或回改票面（是否保留历史票待人工确认）。

## 需求完成度

| 需求 | 结论 | 证据 |
| --- | --- | --- |
| R1 普通摘要端到端 | 已实现 | `agents/context/summary.py:223-251`、`agents/context/builder.py:65-76`；`test/context/test_summary_llm.py:62-108` |
| R2 增量摘要端到端 | 已实现 | `agents/context/summary.py:253-289`、`agents/context/builder.py:67-69`；`test/context/test_summary_llm.py:208-241` |
| R3 系统提示词四条 | 做了一半 | `agents/context/summary.py:26-34` 缺“使用中文” |
| R4 普通/增量用户提示词 | 已实现 | `agents/context/summary.py:35-67`；`test/context/test_summary_llm.py:91-100` |
| R5 输入渲染与选区原样 | 做了一半 | `agents/context/summary.py:91-117` 未排除 system |
| R6 成功开头与重复声明去重 | 已实现 | `agents/context/summary.py:146-153`；`test/context/test_summary_llm.py:157-164` |
| R7 降级内容与 metadata | 已实现 | `agents/context/summary.py:155-179`、`:228-237`、`:266-275` |
| R8 usage 三态与 error | 已实现 | `agents/context/summary.py:119-144`；`test/context/test_summary_llm.py:120-148` |
| R9 降级摘要继续作为旧摘要 | 已实现 | `agents/context/summary.py:166-179`、`agents/context/builder.py:67-69` |
| R10 LLM 构造注入 | 已实现 | `agents/context/summary.py:87-89`、`agents/context/builder.py:19-29`、`agents/core/agent.py:55-57` |
| R11 调用契约 | 已实现 | `agents/context/summary.py:128-134`；`test/context/test_summary_llm.py:107-108` |
| R12 SummaryOutcome 与既有算法不变 | 已实现 | `agents/context/summary.py:70-83`、`:181-221`；`test/context/test_summary.py:11-180` |
| R13 builder 编排与开关 | 已实现 | `agents/context/builder.py:52-84`、`:124-132`；`test/context/test_builder.py:115-195` |
| R14 `take_summary_outcome` 一次性 | 已实现 | `agents/context/builder.py:91-98`；`test/context/test_builder.py:504-516` |
| R15 每次 build（含 `_conclude`）消费并写 trace | 已实现 | `agents/agent/code_agent.py:122-140`、`:153-154`、`:251-262` |
| R16 usage 记账与缓存可用性 | 已实现 | `agents/agent/code_agent.py:101-120`、`:127`；`test/core/test_code_agent_summary_usage.py:146-215` |
| R17 预算包含摘要、步数不变 | 已实现 | `agents/agent/code_agent.py:251`、`:267-273`；`test/core/test_code_agent_summary_usage.py:217-236` |
| R18 Message/History/Config/持久化不变 | 已实现 | 本分支未改 `agents/core/message.py`、`agents/context/history.py`、`agents/core/config.py` |
| R19 README 与已知限制 | 已实现 | `README.md:153-155`、`docs/sdlc/feat/context-summary-generation/spec.md:193-203` |
| R20 Seam 1 测试 | 已实现 | `test/context/test_summary_llm.py`、`test/context/test_summary.py:184-218` |
| R21 Seam 2 测试 | 已实现 | `test/context/test_builder.py:467-752` |
| R22 Seam 3 测试 | 做了一半 | `test/core/test_code_agent_summary_usage.py:146-262` 缺空串降级与 history 断言 |
| R23 验收命令与假 LLM | 已实现 | `python -m pytest test/context test/core -q` 得 192 passed；未联网/未写 trace 文件 |
| R24 四张票状态与验收记录 | 待人工确认 | 票面均 done，但 01/02 仍保留阶段性占位描述 |

## 已核对，不成立

- 非 dict usage 穿透 `_call_llm`（C2）：生产 `HelloAgentsLLM` 的适配器统一用 `_parse_usage` 返回 dict，只有注入不符合 `LLMResponse.usage` 契约的自定义客户端才会触发；不作为阻塞。若想防御，可把 usage 提取纳入 `_call_llm` 的 try。
- `_compose_success` 额外去掉短前缀 `【历史对话摘要】`：spec 只要求完整声明去重；短前缀去重是超集，避免重复，行为无害。
- `_degraded_incremental` 对旧摘要做 `rstrip("\n")`：只归一化尾部空行，不改正文；本模块生成的摘要不以换行结尾，不作为问题。
- `_compose_success` 正文为空时返回仅开头的兜底：与第一条阻塞项同源，修复该 bug 时一并处理，不作为独立问题。
- `SummaryOutcome.mode` 用裸 `str`、`_render_tool_call` 保留对象分支：判断题，当前数据模型下不影响行为，未列为建议。
- 票面状态从“待确认”改为“已批准”、`SummaryOutcome` 增加 `latency_ms`：实现提交内的规格同步，与 04 票的 trace 耗时要求一致；不按“多余改动”处理。

## 怎么验证

```bash
python -m pytest test/context test/core -q
# 192 passed

python -m pytest test/ --ignore=test/sandbox -q
# 453 passed
```
