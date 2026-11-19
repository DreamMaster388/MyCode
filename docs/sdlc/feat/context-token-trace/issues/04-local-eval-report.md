# 04: 本地 eval 透传与汇总

**要构建什么：** 本地 SWE 风格 harness 的每实例结果带上 `context_usage`；run 结束的终端汇总在现有 resolved/平均步数/平均耗时之外，打印类别总量与均值、各工具调用次数与输入 token、摘要调用次数总量与均值；数值与每实例结果逐项一致。

**阻塞于：** 01、02、03

**状态：** ready-for-agent

- [ ] 本地 harness 的每实例结果包含 `context_usage`，形状与 run 元数据一致；无用量/早退实例为 categories 全 0、tools 空、summary_calls 0。
- [ ] 本地 eval 的 results 明细与终端汇总同源：终端对已写出的结果求和/取均值，不二次推断；均值分母为全部实例，早退按 0。
- [ ] 终端打印类别总量与均值、各工具调用次数/输入 token、摘要调用次数总量与均值；保留现有 resolved、平均步数、平均耗时。
- [ ] 抽出可供 05 复用的汇总 helper；helper 输入每实例结果，输出类别/工具/摘要的 totals 与 averages。
- [ ] 现有 results 字段、补丁/判定/沙箱/测试执行逻辑不变。
- [ ] 测试用假 agent/假结果覆盖字段透传、汇总与明细一致、早退零值；不联网、不跑真实 harness。
- [ ] 容器路径不在本票范围，由 05 接入。
