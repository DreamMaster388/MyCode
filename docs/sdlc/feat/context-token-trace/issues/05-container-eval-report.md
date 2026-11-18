# 05: 容器 eval 落盘与正式终端

**要构建什么：** 容器评测的每实例记录顶层带上 `context_usage`；run 结束的正式终端汇总打印类别总量与均值、各工具调用次数与输入 token、摘要调用次数总量与均值，数值与 `diagnostics.jsonl` 明细逐项一致；早退实例保持全 0 形状。

**阻塞于：** 04

**状态：** ready-for-agent

- [ ] 容器 `run_instance` 成功与早退记录都在顶层带 `context_usage`；早退为 categories 全 0、tools 空、summary_calls 0，并保留 error/status。
- [ ] `diagnostics.jsonl` 每实例含该字段；`predictions.jsonl` 结构不变。
- [ ] `run_predictions` 终端复用 04 的 helper，打印类别总量/均值、各工具调用次数/输入 token、摘要次数总量/均值；数值与 diagnostics 明细一致。
- [ ] 现有输入/输出/缓存/请求/时长/费用六项指标与 steps/llm_requests 口径不变；均值分母与现有 avg_steps 一致（全部实例）。
- [ ] 测试用假 sandbox/agent 覆盖成功、早退、汇总与明细一致；不需要 Docker/网络。
- [ ] 本地 harness 不在本票范围，已在 04 完成。
