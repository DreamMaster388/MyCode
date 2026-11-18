# 01: 实现 Recall 基础工具类与单元测试

**要构建什么：** 实现 `RecallTool` 工具，使其能够按 `HistoryManager` 的消息下标索引历史记录，并支持按 `offset` 和 `length` 对消息内容进行切片，通过单元测试验证所有边界行为。

**阻塞于：** 无（可立即开始）

**状态：** ready-for-agent

- [ ] 新增 `agents/tools/builtin/recall_tool.py`，实现 `RecallTool` 类。
- [ ] `RecallTool` 构造时接收 `HistoryManager` 实例引用。
- [ ] 实现 `run()` 方法：支持 `id` 必填，`offset` 和 `length` 可选。
- [ ] 实现切片逻辑（符合 `content[offset : offset+length]` 语义）。
- [ ] 实现异常处理（`id` 越界报错，`offset/length` 越界按 Python 语义）。
- [ ] 新增 `test/tools/test_recall_tool.py`，覆盖：
    - [ ] 成功召回完整消息（不传 offset/length）。
    - [ ] 成功召回切片消息（全量参数）。
    - [ ] 任一参数缺失回退完整消息。
    - [ ] `id` 越界时的错误返回。
    - [ ] `offset`/`length` 超出边界时的静默行为。
