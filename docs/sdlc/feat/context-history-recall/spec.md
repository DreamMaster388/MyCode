# Spec: 历史召回工具 Recall

## 问题
在长对话或经过摘要的上下文中，模型难以精确回溯早期对话的原始细节。目前的上下文治理体系仅实现了截断与摘要，缺乏主动获取原始消息的机制。

## 方案
引入内置工具 `Recall(id, offset, length)`，允许模型主动查询历史记录。工具通过 `HistoryManager` 实例直接读取原始记录，不经过沙箱和 `ModeGuard` 审批，仅在 `CodeAgent` 配置开启时注册。

## 用户故事
1. 作为模型，我想要召回指定下标的原始消息，以便参考精确的对话记录。
2. 作为模型，我想要召回长消息的特定片段（切片），以便在上下文受限时高效获取细节。
3. 作为用户，我想要控制是否启用该工具，以按需调整上下文交互能力。

## 实现决策
1. **工具接口**：
   - 工具名 `Recall`，继承自 `Tool` 基类。
   - 参数：`id` (int, 必填), `offset` (int, 可选, 默认不切片), `length` (int, 可选, 默认不切片)。
   - 调用语义：
     - 若 `offset`/`length` 缺失或其一缺失，返回完整消息内容。
     - 若全提供，返回 `content[offset:offset+length]`。
   - 输出格式：字符串，包含 `role`、`id` (下标) 和切片原文（或全文）。
2. **注册机制**：
   - 仅 `CodeAgent` 注册。
   - `CodeAgent.__init__` 判断 `config.context_recall_enabled` 为真且 `tool_registry` 存在时注册实例，传入 `HistoryManager` 引用。
3. **上下文构建器**：
   - 移除 `ContextBuilder.build()` 中 `if recall: pass` 分支。
4. **错误处理**：
   - `id` 指向不存在的消息下标时，返回明确错误提示。
   - `offset`/`length` 非法或越界时，遵循 Python 切片语义（静默截断/返回空），不报错。
5. **权限**：
   - `Recall` 工具不定义 `access_kind`/`path_params`，不接入 `ModeGuard`，不触发沙箱策略。

## 测试决策
1. **工具级验证**：`test/tools/test_recall_tool.py`。
   - 断言：成功召回全文、召回指定切片、任一缺失回退、id 越界报错、切片越界表现。
2. **注册逻辑**：`test/agent/test_code_agent.py`（或类似）。
   - 断言：`context_recall_enabled` 开启时工具存在，关闭时不存在。
3. **回归验证**：
   - 现有 `test_builder.py` 确保 summary 丢弃逻辑在删除 `if recall` 分支后未受影响。

## 不在范围
- 不提供跨会话的召回（召回仅限当前内存中的 HistoryManager）。
- 不提供语义搜索（Recall 仅支持精确下标索引）。
- 不对 Recall 工具本身的调用频次做限制。

## 补充说明
- 术语见 `GLOSSARY.md` 中的“记忆 i”（对应本工具的 `id` 参数）。
- 开关 `context_recall_enabled` 语义变更为"是否注册 Recall 工具"，不再涉及上下文构建器的内部跳转。
