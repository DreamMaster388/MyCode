# Intent: SWE-bench Lite 单容器编排增加用量与费用统计
作者：lyd。状态：草稿。

## 问题
容器评测只产出 `total_tokens`（输入+输出合计）、步数和 agent 运行时长，
看不出输入、输出、缓存命中各占多少、一次任务花多少钱、发了多少次模型请求；
换模型或调提示词时只能看到总量上涨，无法判断是缓存没命中还是输出变长。

## 预期结果
每个容器任务（每实例一条 diagnostics）新增并落盘六项，run 结束在终端打印
六项汇总（总量与均值，数值与明细一致）：
- 总输入 token、总输出 token、缓存命中 token（响应无该字段时记 0 并标记不可用）；
- 大模型请求次数：取 `stream_run` 每轮循环的 step，与 `total_steps` 同源；
- 端到端总时长：沿用现状，从 agent 启动到补丁提取完成，不含拉镜像与容器启停；
- 总 token 费用：未命中输入 × 2 + 缓存命中 × 0.04 + 输出 × 8（元/百万 token，写死）。

## 影响面
- 使用 `eval/run_predictions.py` 做容器评测、需要看用量与费用的人；
- `eval/container_runner.py`（采集与计价）、`eval/run_predictions.py`（汇总）；
- 共享 LLM 层补字段：`agents/core/llm_adapters.py` 提取缓存命中，
  `agents/agent/code_agent.py` 把输入/输出/缓存累计进会话元数据；
  交互式会话与本地 harness 的元数据也会多出这些字段，语义不变。

## 约束
- 单价按 2 / 0.04 / 8 元每百万 token 写死，不做配置化；
- 输入 token 含缓存命中部分，未命中输入 = 输入 − 缓存命中，再计价；
- 缓存命中从响应 usage 读取（DeepSeek `prompt_cache_hit_tokens`，
  兼容 `prompt_tokens_details.cached_tokens`），缺失记 0 并标记；
- 请求次数按 `stream_run` 的 step 计，不统计 HTTP 重试；
- 本轮只改容器编排链路的代码与输出：本地 harness 指标、README 说明不做；
- 不改补丁提取、判定、沙箱与路径校验行为。

批准：lyd @ 2026-11-07

下一步：/sdlc-spec
