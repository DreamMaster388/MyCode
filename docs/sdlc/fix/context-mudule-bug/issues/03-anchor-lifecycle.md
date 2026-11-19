# 03: 摘要锚点生命周期与回归收口

**要构建什么：** 锚点跟随 history 生命周期：clear/load 后失效，下一次 build 按整个实际视图 + extra 重新估算，P 按 0 计；收尾请求和摘要调用不刷新锚点，主循环请求刷新。无锚点时 system/tool schema 不计入的行为固定下来；README 的摘要章与已知限制同步到新口径；context/core 回归通过。

**阻塞于：** 02

**状态：** ready-for-agent

- [ ] clear/load 后锚点清空，下一次 build 不再按原始 history 全量重算，只按整个实际视图 + extra 估算。
- [ ] 收尾请求与摘要调用不刷新锚点；主循环成功请求刷新。
- [ ] 无锚点时 P 按 0 计，system/tool schema 不计入；行为有确定性测试并写入已知限制。
- [ ] 裁剪/摘要让锚点范围内旧消息变小时，到下一次真实 usage 前会短暂高估；该行为写入已知限制，不做补偿。
- [ ] README 摘要章更新：A<2 跳过、逐轮让出、严格 assistant 切分、锚点 + 双阈值估算、无 tokenizer。
- [ ] 现有 CodeAgent 接线测试按新锚点口径调整并通过；context/core 测试全绿；测试不联网、不调用真实模型、不写 trace。
