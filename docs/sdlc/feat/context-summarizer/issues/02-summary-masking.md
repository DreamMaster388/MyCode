# 02: 摘要遮蔽与 replace 完整列表

**要构建什么：** 摘要开关打开时，上下文视图先做摘要遮蔽：非 summary 消息按原顺序保留，summary 消息把 `replace` 中列出的消息从视图删掉，再把 summary 前插到列表头；最终视图是 `[摘要, 若干消息]` 或没有摘要时的 `[若干消息]`。`replace` 按完整消息 id 列表消费，不再按区间端点理解；摘要关闭时仍按现有行为丢弃 summary。本轮只接通遮蔽视图，不判断触发、不生成新摘要、不改写 history。

**阻塞于：** 无（可立即开始）

**状态：** done

- [x] `ContextSummarizer` 提供 `apply_summary_masking(messages)`，返回遮蔽后的新列表；不修改输入列表和输入 Message 对象。
- [x] 遮蔽算法：遍历输入；非 summary 消息按原顺序 append；遇到 summary，把它 `replace` 中 id 对应的已累积消息删除，再把该 summary 前插到列表头。
- [x] `replace` 按完整 id 列表消费：不把首尾元素解释为区间端点；不存在的 id 按无操作处理；重复 id 不产生额外影响。
- [x] 有摘要时结果以摘要开头；无摘要时保持原顺序；良好构造的 history 下至多一条未遮蔽摘要。
- [x] builder 在 `context_summary_enabled` 为真时，对经过 pruner 的消息列表调用遮蔽；摘要关闭时继续丢弃 summary，不运行遮蔽。
- [x] 本轮不接入 `should_summarize`、不追加新摘要、不改写 history；摘要开关打开且已有 summary 时，旧摘要与被遮蔽原文不再同时出现在 build 返回中。
- [x] 移除旧的 `maybe_summarize` 空实现及其“返回 None”测试。
- [x] 更新既有用例：“摘要开关打开保留 summary + 原文”改为断言遮蔽后的视图；pruner 不改 non-tool 的用例适配遮蔽后列表；其余 summary 开关相关断言同步。
- [x] 新增遮蔽单元测试：无摘要、有摘要、删除 + 前插、非摘要顺序、输入不可变、缺失 id、重复 id、摘要关闭。
- [x] builder 测试覆盖：摘要开启时遮蔽、摘要关闭时丢弃、pruner 先裁剪再遮蔽、system 与 `extra_messages` 不受遮蔽影响；本轮不写触发/生成用例。
- [x] history 原文不被修改；`build()` 每次返回新列表。
- [x] `pytest test/context test/core` 通过；测试不联网、不写文件、不调用真实模型。
