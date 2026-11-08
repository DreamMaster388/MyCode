# Test Report: SWE-bench Lite 容器评测用量与费用统计
运行：`python -m pytest test/core/ test/eval/ -q`、`python -m pytest test/ -q --tb=no`（原始输出见对话）。

## 验收
R1 通过 —— test_metrics_acceptance.py::test_r1_instance_record_contains_six_metrics 等 3 例
R2 通过 —— test_llm_usage_acceptance.py::test_r2_* 共 7 例（两来源、优先级、三入口、DONE chunk）
R3 通过 —— test_llm_usage_acceptance.py::test_r3_* + test_code_agent_usage_acceptance.py::test_r3_*（含真实 0 命中与不可用区分）
R4 通过 —— test_metrics_acceptance.py::test_r4_* 共 4 例（公式、缓存>输入不为负、明细 6 位、汇总等于明细和）
R5 通过 —— test_metrics_acceptance.py::test_r5_llm_requests_shares_steps_source（哨兵值证明同源）
R6 通过 —— test_metrics_acceptance.py::test_r6_duration_excludes_pull_and_container_lifecycle（假时钟）
R7 通过 —— test_metrics_acceptance.py::test_r7_* 共 4 例（总量/均值/空 run/分母含失败实例/CLI 输出）
R8 通过 —— test_metrics_acceptance.py::test_r8_* 等 6 例（旧 usage 键、tokens、diagnostics 键、predictions 结构）

## 失败
- 首轮 1 例打回：`test_any_round_without_usage_marks_cache_unavailable`——部分轮无 usage 时仍标记缓存可用。已修 [code_agent.py](agents/agent/code_agent.py)，复跑通过。
- 评审打回 1 项（重要）：收尾 `_conclude` 与智能摘要调用未计入总量。已补计并同步 total_tokens；
  复核新增 1 项（重要边界）：摘要「调用无 usage」曾被折叠成未调用，已改为 None/{} 三态。
  自检 [test_code_agent_usage_selfcheck.py](test/core/test_code_agent_usage_selfcheck.py) 扩到 6 例，复跑 282 passed。

## 未覆盖
- 真实 Docker 拉镜像/容器启停耗时；真实模型 SDK/网络；`--workers>1` 终端行交错。
- test/sandbox 下 26 个 ERROR 为环境限制（fixture 在 `$HOME` 建临时目录被文件沙箱拒绝），与本次改动无关。

## 结论
通过（验收 49 例 + 自检 6 例全过；全量 423 passed、26 环境 error）。

下一步：/sdlc-review
