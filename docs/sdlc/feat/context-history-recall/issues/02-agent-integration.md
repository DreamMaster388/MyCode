# 02: CodeAgent 注册与 ContextBuilder 清理

**要构建什么：** 将 `RecallTool` 集成到 `CodeAgent` 生命周期中，确保其按配置开关按需注册；同时清理 `ContextBuilder` 中已废弃的召回逻辑分支。

**阻塞于：** 01-recall-tool

**状态：** ready-for-agent

- [ ] 在 `CodeAgent.__init__` 中增加逻辑：根据 `config.context_recall_enabled` 决定是否将 `RecallTool` 实例注册到 `tool_registry`。
- [ ] 从 `agents/context/builder.py` 中删除已失效的召回逻辑分支及其占位 `pass`。
- [ ] 验证现有测试（如 `test_builder.py`）无回归。
- [ ] 确保 `CodeAgent` 获得 `RecallTool` 的工具 schema，模型可见。
