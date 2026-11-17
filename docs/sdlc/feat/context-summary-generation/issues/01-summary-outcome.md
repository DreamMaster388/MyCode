# 01: 预重构：摘要结果对象与 builder 消费

**要构建什么：** 摘要生成链路在不改变现有输出的前提下，能返回一个携带摘要消息、模式、usage、降级标记和错误摘要的结果对象；builder 按结果对象写回 history，并允许调用方一次性读取。这样后续接入 LLM、降级和记账时不需要再改返回类型；本轮 content 仍为空、不调用模型。

**阻塞于：** 无（可立即开始）

**状态：** done

- [x] 定义 `SummaryOutcome`，字段为摘要 `Message`、模式（`normal` / `incremental`）、usage（`None` / `{}` / 字典三态）、`degraded`、`error` 与可选的 `latency_ms`；结果对象只在内存流转，不进入 history、不持久化。
- [x] `summarize(compression)` 与 `summarize_incremental(previous, compression)` 改为返回 `SummaryOutcome`；本轮两个结果的 `message` 仍是空 content、`role="summary"`、`replace` 规则不变、id 为空、timestamp 默认；`usage=None`、`degraded=False`、`error=None`，作为 02/03 接入模型前的占位值。
- [x] builder 在触发摘要时 append `outcome.message`，返回 `[摘要] + 完整保留区`；行为、遮蔽、选区、触发、token 计量与改造前一致。
- [x] builder 提供一次性 `take_summary_outcome()`：返回最近一次 build 的 outcome 后清空；未触发摘要时返回 `None`；重复调用第二次返回 `None`。
- [x] 不调用 LLM、不访问网络、不新增配置/环境变量/CLI、不写 `metadata`。
- [x] 更新已有摘要算法与 builder 测试到新返回类型；遮蔽、选区、触发、append、返回列表、system 与 `extra_messages` 等既有用例全部通过，build 输出与改造前一致。
- [x] 增量路径暂保持空 content 占位；02/03 再接入真实内容，不在本票做行为变化。
