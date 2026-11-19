# 03: 摘要与收尾类别 + 摘要次数

**要构建什么：** 开启摘要或触发收尾时，run 的类别区能把 summary 消息、摘要调用、收尾临时指令分别归位，不再混入 user；会话元数据同时给出本 run 的摘要调用次数（按触发计，含未真正发出请求的触发）。类别区合计覆盖主循环、摘要调用与收尾请求的真实输入。

**阻塞于：** 01

**状态：** ready-for-agent

- [ ] `summary_message` 类别统计进入后续请求的 summary 消息；`summary_call` 整次摘要请求的输入 token 归入该类别，不拆回被压缩消息；`conclude_instruction` 统计收尾追加的临时指令，不并入历史 user。
- [ ] builder 为 summary 消息与收尾临时指令提供类别标注；不得靠发送 role 反推（两者发送时都是 user）。
- [ ] 摘要调用的真实 usage 继续计入现有 input_tokens；本票把其输入 token 归入 summary_call 类别，摘要调用不增加 total_steps/llm_requests。
- [ ] `summary_calls` 每次摘要触发 +1，含未注入 LLM/调用前失败；无真实调用时 token 记 0；不区分普通/增量、成功/降级/异常；收尾路径触发也计入。
- [ ] 摘要开启且触发的 run 中，有 usage 请求的类别合计等于真实输入；无 usage 请求仍不分配。
- [ ] 测试覆盖摘要消息、摘要调用（成功/无真实调用/空压缩区）、收尾指令、摘要次数；用假 LLM，不联网。
- [ ] 工具区、eval 展示不在本票范围。
