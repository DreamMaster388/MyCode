# 04: 摘要记账、预算与 trace

**要构建什么：** 摘要调用的 usage 进入本次 run 的输入/输出/缓存/总 tokens 和 `max_run_tokens` 预算，缓存可用性按真实调用判定；每次摘要调用写一条 `summary` trace 事件；主循环步数与请求次数不变；终端不额外输出。README 补齐记账/trace 并做最终一致性清理。

**阻塞于：** 02、03

**状态：** done

- [x] CodeAgent 在每次主循环 `build()` 后消费 builder 的 outcome，在 `_conclude` 内部 build 后同样消费；消费一次后清空，避免重复计费。
- [x] `outcome.usage` 交给现有 `_absorb`：`None` 不参与记账；`{}` 视为调用发生但无 usage，tokens 记 0 且缓存不可用；字典并入 input/output/cached/total tokens；`total_tokens` 仍等于输入 + 输出。
- [x] 摘要 usage 计入 `max_run_tokens` 预算判断；摘要调用不增加 `total_steps`，评测侧 `llm_requests` 不变。
- [x] 摘要调用按真实调用参与 `cache_hit_available`：缺缓存字段或 `{}` 标记不可用；`None` 不改变标记。
- [x] trace 事件名 `summary`，记录模式（普通/增量）、是否降级、错误摘要、usage、耗时（若可得）与生成的摘要内容；不记录完整 prompt；终端不打印摘要生成/降级/记账提示。
- [x] 降级且无真实调用时不计 usage；空返回但带 usage 时照常计入；异常不翻转缓存标记；收尾 `_conclude` 自身的 usage 记账行为不变。
- [x] 测试覆盖 CodeAgent seam：摘要 usage 进入 `_session_metadata`、总 tokens 恒等式、预算包含摘要、`total_steps`/请求次数不变、缓存可用性三态、降级/异常不误计、`_conclude` 路径、trace 事件字段、终端无额外输出。
- [x] 测试使用假 LLM 与记录型假 trace logger，不写 trace 文件、不联网、不调用真实模型。
- [x] 更新 README：摘要调用计入 run 统计与预算、`total_steps`/请求次数口径、`summary` trace 事件、降级/异常/无 usage 的记账行为；清理与 spec 不一致的旧描述（尤其“content 为空”“摘要未实现”）。
- [x] 最终验收：`pytest test/context test/core` 通过；README/GLOSSARY/spec 对摘要行为的描述一致；不产生落盘 trace 文件。
