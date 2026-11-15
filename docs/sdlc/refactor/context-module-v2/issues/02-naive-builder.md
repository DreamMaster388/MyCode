# 02: 朴素 builder 贯通

**要构建什么：** 新增 builder 的朴素链路，CodeAgent 每次请求由 builder 组装 system + 按 summary.replace 遮蔽后的历史 + extra_messages；不再在轮末自动摘要，摘要 usage 记账移除。旧 history 构建/摘要方法可以暂留，但不再由主链路调用。

**阻塞于：** 01 工具结果全量进入上下文

**状态：** ready-for-agent

- [ ] builder 当前唯一路径是朴素链路：system（若有）+ 遮蔽后的历史 + extra_messages。
- [ ] summary 消息发送时降级为 user；assistant 的 tool_calls 与 tool 的 tool_call_id 保留。
- [ ] 遮蔽支持闭区间、倒序区间、嵌套摘要、端点缺失兜底，结果与旧快照一致。
- [ ] CodeAgent 每次 LLM 调用使用 builder 输出；收尾指令通过 extra_messages 传入，不写入历史。
- [ ] CodeAgent 不再在轮末自动摘要，不再有摘要 usage 记账；旧摘要 usage 测试移除或改写。
- [ ] 新增 builder 朴素链路单元测试和 CodeAgent 集成测试；现有 usage、步数、收尾测试通过。
