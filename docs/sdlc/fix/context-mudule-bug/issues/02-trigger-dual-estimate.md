# 02: 摘要触发：按实际视图新增消息做双阈值估算

**要构建什么：** 触发判断不再读 history 原始尾部。每次 build 用最近一次主循环成功请求的真实 prompt_tokens 作锚点，只估算锚点之后在实际视图（裁剪、遮蔽后）中新增的消息和本次 extra；估算按实际发送 payload 的字符数 / 4 与 UTF-8 字节数 / 4 分别向上取整。字符估算超过 window × threshold，或字节估算超过 window，任一成立即触发；工具参数、tool_call_id 与 extra 都进入估算。

**阻塞于：** 01

**状态：** ready-for-agent

- [ ] 锚点记录真实 prompt_tokens 和该次实际发送视图的最大消息 id；主循环成功请求后刷新。
- [ ] 尾部只取当前实际视图中 id 大于锚点边界的消息，加本次 extra；裁剪后的 tool 内容按视图计，被遮蔽的旧消息不重复计入。
- [ ] 估算对象是实际发送 payload（role/content/tool_calls/tool_call_id）的 JSON；assistant 的工具调用参数和 tool 结果都参与。
- [ ] 字符条件 C = ceil(字符数 / 4) 与字节条件 B = ceil(UTF-8 字节数 / 4) 分别可单独触发；等于阈值不触发；严格大于。
- [ ] C 与 P 相加后超过 window × threshold，或 B 与 P 相加后超过 window，任一成立即触发；系统提示词和 tool schema 不重复估算。
- [ ] 覆盖工具参数、裁剪/遮蔽视图、extra、两条阈值分别触发与等于阈值不触发；context/core 测试通过，不联网、不调用真实模型、不写 trace。
