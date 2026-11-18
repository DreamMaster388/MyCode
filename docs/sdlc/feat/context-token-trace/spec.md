# Spec: 上下文输入 token 消耗溯源

## 问题

现在 run 级只有 `input/output/cached/total` 总量，无法回答“输入 token 花在 system、user、assistant、tool、summary 哪一类，哪个工具最贵”。eval 报告只有总量；摘要次数只存在于 trace，而 eval 关闭 trace；未来前端也没有稳定的按类别数据。裁剪和摘要会改变实际发送内容，按 history 原文统计会失真。

## 方案

每次 LLM 请求发送前，按实际发送视图给每个类别算长度份额；模型返回真实 `prompt_tokens` 后按份额分配，跨请求累加，写入本次 run 的会话元数据。工具区再按工具名给出输入 token 与调用总次数；摘要调用按触发次数单独计数。eval 的 `diagnostics.jsonl` 与终端汇总消费同一份数据；现有输入/输出/缓存/总量、步数、请求次数与费用口径不变。

## 用户故事

1. 作为 eval 维护者，我想要按类别看到输入 token 的总量与均值，以便判断上下文预算花在哪。
2. 作为 eval 维护者，我想要按工具名看到输入 token 和调用次数，以便识别昂贵的工具。
3. 作为智能体开发者，我想要摘要调用次数，以便判断摘要触发的频率。
4. 作为未来前端开发者，我想要 run 级结构化统计数据，以便直接渲染而不用重算。
5. 作为维护者，我想要工具消耗以实际发送视图为准且现有总量/步数口径不变，以便裁剪/摘要效果可见、历史报告仍可比较。

## 实现决策

### 计量口径

- 只拆输入 `prompt_tokens`；输出仍只保留 run 总量，不归属到类别或工具。
- 累计：同一消息在后续请求中每次重发都再次计入；不记录上下文快照。每次 `stream_run` 独立记录，不维护会话累计字段，会话累计由读方按 run 求和。

### 分配

- 每次主循环/收尾请求发送前，对实际发送视图的每个类别计算 `len(str)`；拿到真实 `prompt_tokens` 后按份额分配。类别合计必须精确等于该请求真实输入；整数余数按最大余数法处理，不单列模板/未归类余量，模板开销按比例摊入。
- `len` 取实际发送 payload 的字符串表示（`role/content/tool_calls/tool_call_id`），不含 history 内部字段；工具 schema 按实际序列化 JSON 计。
- 没有 `prompt_tokens` 的请求不做分配；该请求只保留现有总量口径，类别合计可能小于 run 的 `input_tokens`。

### 类别区

- 固定类别：`system`、`tool_schema`、`user`、`assistant`、`tool_result`、`summary_message`、`summary_call`、`conclude_instruction`。
- `assistant` 合并正文与工具调用参数，不再拆两行；工具区需要的参数份额在内部保留，不暴露成额外类别。
- `summary_message` 是进入后续请求的 `role="summary"` 消息；`summary_call` 是生成摘要的那次 LLM 请求，整次请求输入归入该类别，不拆回被压缩消息。
- `conclude_instruction` 是预算/步数触顶时追加的收尾临时指令，不是历史 user 消息。
- builder 必须提供与发送列表一一对应的类别标注，且标注基于 pruner 裁剪、summary 遮蔽后的实际发送视图；调用方不得从发送字典的 `role` 反推 `summary_message`/`conclude_instruction`（它们发送时都是 `user`）。

### 工具区

- 按工具名一行：`input_tokens` + `calls`。`input_tokens` = 该工具调用参数与 tool 结果在请求中的输入 token 之和；不拆参数/结果。
- `calls` 只数模型发出的 `tool_call` 总数，含未知工具与参数解析失败；不区分成功/失败，不含 LLM HTTP 重试。
- 工具区是第二视角，不与类别区相加；它从 `assistant` 与 `tool_result` 两个类别中抽取对应份额，不改变类别区数值。
- 同一 assistant 消息含多个 `tool_call` 时，按各参数长度分摊该消息中属于工具参数的份额；tool 结果按 `tool_call_id` 映射到工具名。
- 未知工具名按模型发出的名字建行；工具 schema 只进 `tool_schema` 类别，不摊到具体工具。

### 摘要次数

- 每次 run 一个整数：摘要模块被触发的次数；触发但未真正发出 LLM 请求也加 1、token 记 0，不区分普通/增量、成功/降级/异常；收尾路径触发的摘要同样计入。

### 数据模型

每次 run 的 `_session_metadata` 增加：

```json
{
  "context_usage": {
    "categories": {
      "system": 0, "tool_schema": 0, "user": 0, "assistant": 0,
      "tool_result": 0, "summary_message": 0, "summary_call": 0,
      "conclude_instruction": 0
    },
    "tools": {"Read": {"input_tokens": 0, "calls": 0}},
    "summary_calls": 0
  }
}
```

- `categories` 八个键始终存在，无消耗记 0；`tools` 只保留出现过的工具名；`context_usage` 只描述本 run，不新增会话级累计字段。
- 现有 `input_tokens/output_tokens/cached_tokens/total_tokens/total_steps/llm_requests` 与费用字段语义不变；摘要调用不增加步数/请求次数。

### eval 展示

- 两条 eval 路径（本地 harness、容器）都把 `context_usage` 放进每实例结果；容器 `diagnostics.jsonl` 顶层增加该字段，早退实例为全 0 形状。
- 终端汇总打印：类别总量与均值、各工具调用次数/输入 token、摘要调用次数总量与均值；均值分母为全部实例，早退实例按 0 计。

## 测试决策

- Seam 1 `ContextBuilder.build()`：断言实际发送视图的类别标注——裁剪后的 tool 内容、被摘要遮蔽的消息、`summary_message`、收尾临时指令都归对类别；不测具体 token 数值。
- Seam 2 `CodeAgent.stream_run()`：断言类别合计与真实输入一致、工具区按名汇总正确、摘要次数按触发口径计、收尾路径被单独归类；旧总量/步数/请求次数不变。
- Seam 3 eval 透传：本地 `run_agent` 与容器 `run_instance` 都带出 `context_usage`；`diagnostics.jsonl` 与终端汇总同源；早退实例零值形状一致。
- 不测真实模型、Docker、网络、tokenizer 精度；不测前端和持久化。

## 不在范围

- 前端页面、读取 API、会话/usage 持久化。
- 输出 token 归属、按类别拆缓存命中与费用。
- 单次 `tool_call` 明细、单条消息明细、上下文快照。
- 子代理的 token 与调用次数。
- 自动生成 eval Markdown 报告。

## 补充说明

- 占比是估算，不是 tokenizer 精确值；单条/单工具份额仍有误差。
- 取舍：用占比估算换零 tokenizer 依赖；用按工具名汇总换数据量与实现简单；用只写内存换不改会话/trace 格式。
- 只写内存 `_session_metadata`，不落盘、不写 trace、不发 `AGENT_FINISH`、不建独立 JSON；交互式 run 结束后数据丢失，未来前端若要历史数据需另补持久化或实时读取。
- 类别/工具 token 都是总输入口径，不区分缓存命中；现有 `cached_tokens` 与费用字段不变。
