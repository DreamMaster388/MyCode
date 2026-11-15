# 02: builder 组装上下文

**要构建什么：** 新增 builder 负责组装 system、历史和临时消息；CodeAgent 每次请求改由 builder 组装；不再在轮末自动摘要，摘要 usage 记账移除。三开关全关时丢弃 summary 消息，摘要开关开启时保留。

**阻塞于：** 01 工具结果全量进入上下文

**状态：** done

- [x] builder 组装 system（若有）+ 历史 + extra_messages。
- [x] 三个开关全关时丢弃 summary 消息；摘要开关开启时保留 summary 消息。
- [x] summary 消息发送时映射为 user；assistant 的 tool_calls 与 tool 的 tool_call_id 保留。
- [x] CodeAgent 每次 LLM 调用使用 builder 输出；收尾指令通过 extra_messages 传入，不写入历史。
- [x] CodeAgent 不再在轮末自动摘要，不再有摘要 usage 记账；旧摘要 usage 测试移除或改写。
- [x] 新增 builder 单元测试和 CodeAgent 集成测试；现有 usage、步数、收尾测试通过。
