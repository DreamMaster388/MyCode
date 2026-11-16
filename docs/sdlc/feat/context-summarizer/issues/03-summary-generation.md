# 03: 摘要生成端到端（普通 + 增量）

**要构建什么：** 摘要开关打开且 `should_summarize` 为真时，builder 在遮蔽后的视图上选出“摘要 + 压缩区”，按有无旧摘要分别调用普通或增量摘要函数，生成 `content` 为空、`replace` 为直接遮蔽 id 完整列表的摘要消息并 append 到 history，本次返回 `[新摘要, 完整保留区]`；不需要摘要时返回遮蔽后的列表。保留轮数由 Config 控制，保留区仍经过 pruner；完整保留区的起点回退到最近 N 轮中的第一条 user。本轮摘要内容留空，不调用 LLM。

**阻塞于：** 01 预重构：`should_compress` 改名 `should_summarize`、02 摘要遮蔽与 replace 完整列表

**状态：** ready-for-agent

- [ ] Config 新增 `context_summary_keep_rounds=10`；`< 1` 时静默回退 10；`context_summary_enabled` 保持传入值；不新增摘要模型、prompt、温度、费用或 usage 配置。
- [ ] builder 在摘要分支调用 `should_summarize`；口径不变，只在开关打开时判断；不需要摘要时不追加新摘要。
- [ ] `select_summary_region` 在遮蔽后的列表上返回摘要区间（可选旧摘要 + 压缩区）和完整保留区：无旧摘要时按最后 N 条 assistant 找边界，起点回退到该 assistant 之前最近一条 user；该 assistant 前没有 user 时从该 assistant 开始；有旧摘要时摘要段为旧摘要，压缩区为摘要之后到保留区起点之前。
- [ ] 轮次口径沿用 assistant 计数；N 来自配置；压缩区与完整保留区不重叠、不遗漏。
- [ ] `summarize(compression)` 返回 `role="summary"`、`content` 为空、`replace` 为压缩区消息 id（按视图顺序）的 Message。
- [ ] `summarize_incremental(previous, compression)` 返回 `role="summary"`、`content` 为空、`replace` 为旧摘要 id + 压缩区消息 id（按视图顺序）的 Message；不并入旧摘要 `replace` 中的历史 id。
- [ ] builder 在需要摘要时：选择 → 无旧摘要走普通摘要、有旧摘要走增量摘要 → append 到 history 末尾 → 返回 `[新摘要] + 完整保留区`；完整保留区来自已经过 pruner 的视图，不重新从 history 取原文。
- [ ] 不需要摘要时返回遮蔽后的列表，不 append；摘要关闭时丢弃 summary；召回分支仍为空实现，摘要分支不提前 return，system 与 `extra_messages` 的组装顺序不变。
- [ ] 新摘要的 id 由 `HistoryManager.append` 分配；history 原文与已有摘要不被改写；append 不 bump revision；token 计量基线不重置，下一次 `record_prompt_tokens` 正常校准。
- [ ] 空 content 摘要按既有 `to_llm_dict` 映射作为 user 消息发送；本轮不调用 LLM、不访问网络。
- [ ] Seam A 测试覆盖：触发真假、有/无旧摘要、append 后 history 末尾、返回 `[新摘要, 完整保留区]`、`replace` 为直接遮蔽列表、保留区与 pruner 的交互、system 与 `extra_messages`、每次 build 返回新列表。
- [ ] Seam B 测试覆盖：选区的 user 边界、摘要后一条结果、无 user 回退、N 可配置；两种摘要函数的 `replace`、空 content、role。
- [ ] 不测试“保留区不足 N 轮就触发压缩”“压缩区为空”“异常多摘要历史”；不为这些情况写防御分支。
- [ ] README 删除“摘要仍为空实现”的描述，补充触发条件、`context_summary_keep_rounds`、遮蔽行为、空 content 占位，以及“保留区未达上限”不在本轮范围；把 `replace` 的区间描述改为完整 id 列表。
- [ ] `pytest test/context test/core` 通过；测试不联网、不写文件、不调用真实模型；spec、ADR 与实现描述一致。
