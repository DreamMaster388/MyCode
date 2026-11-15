# 03: 三开关与链 2 骨架

**要构建什么：** 配置新增三个开关，默认全关；pruner 和 summary 提供空实现；builder 在 build 内、组装前调用 summary 钩子；任一开关打开时告警一次并回退朴素。打开开关本轮不产生裁剪或摘要效果。

**阻塞于：** 02 朴素 builder 贯通

**状态：** ready-for-agent

- [ ] 配置新增三个开关，默认 false；全关时 builder 输出与 02 的朴素结果一致。
- [ ] 任一开关为 true 时，builder 首次进入链 2 告警一次并回退朴素；主循环不中断；同一 builder 不重复告警。
- [ ] summary 开关为 true 时，summary 空钩子在 build 内、组装前被调用并返回 None；关闭时不调用。
- [ ] truncation 开关为 true 时 pruner 不被调用；recall 开关不产生副作用。
- [ ] pruner/summary 空实现原样返回/返回 None，不读写文件、不改写 history。
- [ ] 三个开关的逐个矩阵测试通过；全关不告警。
