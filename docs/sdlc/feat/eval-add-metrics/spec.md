# Spec: SWE-bench Lite 单容器编排增加用量与费用统计（来自 intent.md）

作者：lyd。状态：已批准。

一句话：每个容器实例的诊断多出输入/输出/缓存/请求次数/时长/费用六项，run 结束打印同源汇总。

## 需求

R1 每实例 `diagnostics.jsonl` 记录新增六项：输入 token、输出 token、缓存命中 token、大模型请求次数、端到端时长、总 token 费用。
R2 缓存命中从响应 usage 采集：优先 `prompt_cache_hit_tokens`，回退 `prompt_tokens_details.cached_tokens`。
R3 响应无缓存字段时记 0，并写入「缓存可用性」标记，使 0 与「不可用」可区分。
R4 计价：未命中输入 × 2 + 缓存命中 × 0.04 + 输出 × 8（元/百万 token，代码写死）；输入含缓存命中部分，未命中 = 输入 − 缓存。
R5 请求次数与 `total_steps` 同源（`stream_run` 每轮 step），不计 HTTP 重试。
R6 端到端时长沿用现状：agent 启动到补丁提取完成，不含拉镜像与容器启停。
R7 run 结束在终端打印六项汇总（总量 + 均值），数值与 `diagnostics.jsonl` 明细逐项一致。
R8 现有字段语义不变：`total_tokens` 仍为输入+输出，`steps`、`duration_seconds`、`predictions.jsonl` 结构不动。

## 风险与分歧

C1 缓存不可用时费用会偏高 → 已定：按全额输入计价并在标记中写明，不估算（lyd）。
C2 请求次数复用 step 口径，工具重试/截断会少计 → 已定：与 `total_steps` 同源优先，独立计次不在本期（lyd）。
C3 会话元数据新增字段会被交互式会话与本地 harness 看到 → 已定：只增不改、语义不变，其指标与 README 本期不跟进（intent）。
C4 早退/异常实例无用量 → 已定：六项记 0 并保留 `error`，均值分母与现有 `avg_steps` 一致（全部实例）。

## 设计

- 共享 LLM 层：把 usage 解析收成一处，在现有各条返回路径上补齐输入/输出/缓存字段并保留旧键，避免调用方分叉判断。
- `code_agent` 在 `stream_run` 每轮消费 usage 时累计输入/输出/缓存进会话元数据；`total_steps`、`total_tokens` 的计算路径不动。
- `container_runner` 从会话元数据取数写入实例诊断，计价在此完成；画像失败早退路径同样落六项 0 值，保证记录形状一致。
- `run_predictions` 汇总直接对已写出的 results 求和取均值，不二次推断，避免明细与汇总口径分叉；同时保留原有平均步数/tokens 行。
- 不新增 CLI 开关、不改补丁提取/判定/沙箱/路径校验；样本实验组用既有 `--no-path-guard` 即可。

## 不在范围

- 本地 harness（`agent_runner`）指标、README 与文档说明。
- 单价配置化、多模型价格表、费用币种换算。
- 缓存命中节省额、成本趋势与对账。

批准：lyd @ 2026-11-07

下一步：/sdlc-plan
