# 01: 预重构：should_compress 改名 should_summarize

**要构建什么：** 把 builder 的触发判断方法从 `should_compress` 改名为 `should_summarize`，判定口径、严格大于、clear/load 后归零等行为完全不变；调用方和测试同步改名，行为零变化，为后续摘要接入准备清晰的命名。

**阻塞于：** 无（可立即开始）

**状态：** done

- [x] builder 的公开方法 `should_compress` 改名为 `should_summarize`，docstring 同步改为“是否需要摘要”；方法体与判定口径不变。
- [x] 判定仍为“已计量真实 prompt_tokens + 未计量尾部按字符数除以 4 估算，严格大于 `context_window × compression_threshold`”；clear/load 后计量状态归零的既有行为不变。
- [x] 当前代码与测试中不再引用 `should_compress`；历史 SDLC 文档和历史计划不做改写。
- [x] builder 测试中的触发用例改名为 `should_summarize`，阈值严格大于、未计量尾部、clear/load 归零的断言保持原样。
- [x] Agent 上下文测试中的触发调用同步改名，断言语义不变。
- [x] 摘要分支仍为空实现；不引入遮蔽、选区、生成或 append，build 输出与改名前一致。
- [x] `pytest test/context test/core` 通过；测试不联网、不写文件、不调用真实模型。
