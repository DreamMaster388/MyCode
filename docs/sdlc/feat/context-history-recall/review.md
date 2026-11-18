# 评审：新增 Recall 工具实现历史召回，并删除 builder 召回空分支

对照：spec / 票 / `bcb53e0`..HEAD 的 diff（2 个提交，11 个文件）。三轴独立评审（代码质量、需求完成度、bug）结论均已逐条复核，重复项合并。

## 结论

**通过**：需求 R1–R13 全部落地且有测试，删除 builder 召回分支干净，无阻塞项。建议合入前顺手修掉「非法 `offset`/`length` 静默返回全文」，其余可按需处理；「Recall 结果被 pruner 二次裁剪」需要你拍板是否算预期行为。

## 建议修（不阻塞）

- [ ] [bug] `offset`/`length` 传非整数时被当成"未提供"，静默返回整条消息 — `agents/tools/builtin/recall_tool.py:127-132`；影响：模型请求 5000 字符切片却拿回 10013 字符全文，挤占上下文，且与 spec「`offset`/`length` 非法或越界时，遵循 Python 切片语义（静默截断/返回空）」不符；怎么改：区分"参数缺失"（回退全文）与"类型非法"（返回 `INVALID_PARAM` 或空切片），补 `"abc"`/对象参数用例。
- [ ] [bug] 开启截断时，Recall 返回的长结果下一轮 build 会被 pruner 再次裁剪 — `agents/tools/builtin/recall_tool.py:116`（结果以 `role="tool"` 写入 history）→ `agents/context/pruner.py:16-49`；影响：实测 10013 字符结果下一轮视图只剩 2027 字符（头 1500 + 尾 500 + marker），`rounds=0` 时整段只剩 marker，长片段只能沿新 marker 反复分页，与用户故事 2「高效获取细节」有折扣；怎么改：让 pruner 跳过 Recall 的结果消息，或 Recall 主动分页并在 text 中给出下一段 `offset`，补一条 marker→Recall→下一轮 build 的集成测试。spec 未写此交互，属待拍板项。
- [ ] [bug] 3 次 id 报错后熔断器禁用 Recall，之后合法调用也失败 — `agents/tools/builtin/recall_tool.py:86-93` + `agents/tools/circuit_breaker.py:27-31`（阈值 3、300 秒）；影响：模型照 marker 猜错几次下标后，本 run 内再也召不回来；怎么改：读类查询工具豁免熔断，或为 Recall 单独调低失败计数。registry 既有策略，非本分支引入，可讨论。
- [ ] [bug] `id` 为 `inf` 时抛 `OverflowError`，绕过 `INVALID_PARAM` — `agents/tools/builtin/recall_tool.py:73-74`；影响：`run({"id": float("inf")})` 直接抛异常，经 registry 变成 `EXECUTION_ERROR` 并计入熔断；怎么改：捕获 `OverflowError` 或用 `math.isfinite` 校验，补非有限浮点用例。
- [ ] [bug] 经 `CodeAgent` 调用时 bool/小数 `id` 被强转为 1，工具层校验不可达 — `agents/core/agent.py:281`（`int(value)`）+ `agents/tools/builtin/recall_tool.py:123`；影响：实测 `_convert_parameter_types("Recall", {"id": True})` 得到 `{"id": 1}`、`{"id": 1.9}` 同样变 1，有 2 条以上历史时会召回错误下标而不是报错；怎么改：参数转换层拒绝 bool 与非整数 float，或 Recall 拿到原始值再校验，并补一条经 `_execute_tool_call` 的用例。
- [ ] [bug/疑似] 召回 assistant 工具调用消息时正文为空 — `agents/tools/builtin/recall_tool.py:100`；影响：CodeAgent 的 assistant 工具调用消息 content 通常为空，`Recall` 只返回 content，模型得到 `[记忆0] [assistant]\n` 却报成功，想回溯「当时执行的命令/参数」拿不到东西；怎么改：确认是否应一并返回 `tool_calls`/`tool_call_id`，或至少在 description 写明"只返回 content 字段"。
- [ ] [质量] 局部变量 `message_id` 混用「消息 id」与「列表下标」 — `agents/tools/builtin/recall_tool.py:73`；影响：`Message.id` 与会话 load 后的下标不一致（GLOSSARY：「避免使用：消息 id」），维护者顺手传 `message.id` 会静默取错消息；怎么改：改名为 `index`/`history_index`，对外的 `data["id"]` 保持规格定义。
- [ ] [质量] `_as_int` 的 docstring 与实际异常类型不符 — `agents/tools/builtin/recall_tool.py:118-125`；影响：docstring 写"非法值抛 `ValueError`"，bool 分支实际抛 `TypeError`；怎么改：统一抛 `ValueError`，或把 docstring 改成 `TypeError`/`ValueError`。
- [ ] [质量] 构造参数 `history` 无类型标注 — `agents/tools/builtin/recall_tool.py:25`；影响：同目录工具与 `ContextBuilder` 都给注入依赖标了类型，这里看不出契约；怎么改：`history: "HistoryManager"`（`TYPE_CHECKING` 导入）。
- [ ] [质量] header/data 在 if/else 两分支各拼一遍 — `agents/tools/builtin/recall_tool.py:99-114`；影响：改输出格式或加字段要同步两处，`data` 键集合还随分支变化；怎么改：分支内只算 content 与可选偏移，分支外统一组装。
- [ ] [质量] 测试文件未使用的 `import pytest` — `test/tools/test_recall_tool.py:2`；怎么改：删除该行。
- [ ] [流程] 票的验收标准仍未勾选 — `docs/sdlc/feat/context-history-recall/issues/01-recall-tool.md:11-19`、`issues/02-agent-integration.md:11-14`；影响：已完成项仍记为 `[ ]`，与此前特性票用 `[x]` 记录完成的惯例不一致；怎么改：勾选已完成项或改写状态。

## 需求完成度

| 需求 | 结论 | 证据 |
| --- | --- | --- |
| R1 新增 `RecallTool`，名 `Recall`，继承 `Tool` | 已实现 | `agents/tools/builtin/recall_tool.py:14,32` |
| R2 `id` 必填、`offset`/`length` 可选整数 | 已实现 | `recall_tool.py:43-69` |
| R3 只给 `id` 返回整条消息（含 role 与下标） | 已实现 | `recall_tool.py:99-102,116`；`test_recall_full_message_by_index` |
| R4 `offset`+`length` 都给返回切片 | 已实现 | `recall_tool.py:103-104`；`test_recall_returns_content_slice_when_offset_and_length_given` |
| R5 任一参数缺失回退整条消息 | 已实现 | `recall_tool.py:99`；两条 fallback 测试 |
| R6 持有 `HistoryManager` 引用并读到后续追加 | 已实现 | `recall_tool.py:85`；`test_recall_reflects_messages_appended_after_construction` |
| R7 仅 `CodeAgent` 按开关注册，关闭不注册 | 已实现 | `agents/agent/code_agent.py:60-64`；`test/core/test_code_agent_recall.py` 4 条 |
| R8 删除 builder 的召回空分支 | 已实现 | `agents/context/builder.py:55-74` |
| R9 `id` 越界返回明确错误 | 已实现 | `recall_tool.py:85-93`；4 条越界/缺参测试 |
| R10 `offset`/`length` 越界按 Python 切片容忍 | 已实现（非法类型除外，见建议 1） | `recall_tool.py:104`；两条越界测试 |
| R11 不接沙箱与 `ModeGuard` | 已实现 | 工具无路径参数，执行链无权限检查 |
| R12 文档与术语同步 | 已实现（超出票面） | `GLOSSARY.md`、`README.md` 相关行 |

## 已核对，不成立

- `main.py` 未开启 `context_recall_enabled`：截断、摘要两个开关同样未在入口开启，默认全关是既有口径，不是本分支缺陷；入口是否暴露另立票。
- 「属性继承 `access_kind="read"` 违反不接沙箱」：该元数据当前无任何执行链消费（全仓只声明不读取），Recall 也没有路径参数，行为上无鉴权。
- 文档改动超出票面：内容与实现一致、已随本分支提交，无功能风险，不单独计问题。

## 怎么验证

```bash
cd /home/lyd/code/MyCode
python -m pytest test/tools/test_recall_tool.py test/core/test_code_agent_recall.py test/context/ -q
python -m pytest test/ -q --ignore=test/sandbox
```

复现建议 1 与熔断：

```bash
python -c "
from agents.context.history import HistoryManager
from agents.core.message import Message
from agents.tools.builtin.recall_tool import RecallTool
from agents.tools.registry import ToolRegistry
h=HistoryManager(); h.append(Message('abcdef','user'))
t=RecallTool(h)
print(repr(t.run({'id':0,'offset':'abc','length':2}).text))
reg=ToolRegistry(); reg.register_tool(t)
[reg.execute_tool('Recall',{'id':99}) for _ in range(3)]
print(reg.execute_tool('Recall',{'id':0}).text)
"
```

## 未覆盖

- 真实模型会话中的 marker→Recall→pruner 往返：无凭证，未跑端到端。
- assistant 工具调用消息是否应随召回返回 `tool_calls`：需求未定义，待人工拍板。
- Recall 结果被 pruner 二次裁剪是否算预期：spec 未写，待人工拍板（建议 2）。
