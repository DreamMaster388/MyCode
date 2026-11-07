# Plan: SWE-bench Lite 容器评测用量与费用统计（来自 spec.md）

## 改动文件
- agents/core/llm_adapters.py（usage 解析收一处，四处接入）
- agents/agent/code_agent.py（stream_run 累计并写会话元数据）
- eval/container_runner.py（记录落六项 + 计价）
- eval/run_predictions.py（汇总打印六项）
- test/core/test_llm_usage_cache.py（新增）
- test/eval/test_container_metrics.py（新增）

## 顺序
1. `llm_adapters` 新增模块级 `_parse_usage(raw)`：保留 `prompt_tokens/completion_tokens/total_tokens`，补 `input_tokens/output_tokens/cache_hit_tokens`；缓存先读 `prompt_cache_hit_tokens`，回退 `prompt_tokens_details.cached_tokens`；raw 为空返回 `{}`。替换 `invoke`、`stream_invoke`、`invoke_with_tools`、`stream_invoke_with_tools` 四处 usage 赋值，不改变流式「最后一次覆盖」的现状。
2. `code_agent`：`stream_run` 循环新增 input/output/cache 累加器与「缓存是否全程可用」标志（任一轮缺字段即置否）；在 `_session_metadata` 写入 `input_tokens/output_tokens/cached_tokens/cache_hit_available`；`total_steps/total_tokens` 算法不动。
3. `container_runner`：`run_instance` 的 `base` 预置 `input_tokens/output_tokens/cached_tokens/llm_requests/total_cost` 为 0，成功路径用 meta 覆盖，`llm_requests = total_steps`；`diagnostics` 加 `cache_hit_available`；计价 2/0.04/8 元每百万写死，未命中输入 `max(0, input - cache)`。
4. `run_predictions`：汇总段对 results 逐项求和后打印六项总量与均值（分母为实例数），保留原平均步数/平均 tokens 行；单实例进度行追加费用。
5. 自测：`python -m pytest test/core/test_llm_usage_cache.py test/eval/test_container_metrics.py`。

## 风险
- 末个 chunk 不带 usage → helper 返回 `{}`，按 0 计并置缓存不可用，不中断其它实例。
- 浮点费用求和尾差 → 明细存 6 位小数，汇总用原值求和后再格式化。
- 早退实例不经 meta → 六项留 0，字段形状与成功实例一致。

## 证明方式
R1/R5/R6 → test/eval/test_container_metrics.py::test_diagnostics_six_fields
R2/R3 → test/core/test_llm_usage_cache.py::test_cache_hit_fallback
R4 → test/core/test_llm_usage_cache.py::test_cost_formula
R7 → test/eval/test_container_metrics.py::test_summary_matches_details
R8 → test/eval/test_container_metrics.py::test_legacy_fields_unchanged

批准：lyd @ 2026-11-07

下一步：/sdlc-build
