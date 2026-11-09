# Review: 容器评测新增用量与费用指标
对照 intent / spec / plan 与当前 diff（`be60526..HEAD` + 未提交修复）。

## 发现
阻塞：无。
重要：首轮 1 条（`_conclude` 收尾与智能摘要 usage 未入账，触顶实例费用系统性偏低）已补计并复核闭环；
复核新增 1 条「摘要调用无 usage 被折叠成未调用」已修（`last_summary_usage` 改为 None/{} 三态）。
建议：
1. `_conclude` 通道返回空文本时该次调用 usage 仍被丢弃（真实花费未入账，极端残留）。
2. 整轮 usage 缺失时 token/费用记 0 仅靠 `cache_hit_available` 标记，建议汇总补「缓存不可用 N/M」。
3. `eval/run_predictions.py` 汇总费用打印 4 位、明细 6 位，削弱可核对性。
4. `estimate_cost` 未把 cached 截到 input，cached>input 时超出部分仍按 0.04 计价。
5. 缺「真实 CodeAgent→run_instance」贯通用例；`llm_requests` 只数主循环而费用含收尾/摘要，建议 spec 注明口径。
另有 0 条未列出建议。

## 测试
R1–R8 均有对应用例（R1 3 例、R2 7 例、R3 6 例、R4 4 例、R5 2 例、R6 1 例、R7 4 例、R8 6 例 + 早退 3 例）。
未削弱：既有测试零改动、无 skip/xfail、断言未放宽、mock 只替边界（Docker/LLM/时钟）。
复核实测：4 轮主循环 + 摘要 + 收尾 → input 350 / output 35 / total 385，R8 恒等式成立，连跑两次无重复计。

## 结论
通过：重要项全部闭环，剩余 5 条建议不阻塞合并。

下一步：人工提交、开 PR、合并。
