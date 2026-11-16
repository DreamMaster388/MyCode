# 02: 上下文截断 pruner 与 Config

**要构建什么：** 开启 `context_truncation_enabled` 后，builder 在截断分支对 `role="tool"` 消息做视图级裁剪：最近轮内的超长工具输出保留前 head 个字符和后 tail 个字符，中间用换行包裹的、指向 history 下标的 marker 替代；轮次外的工具输出整体替换为 marker；只有结果严格更短才替换；轮数、阈值、头尾长度由 Config 控制，非法组合自动回退默认值；history 原文不改写；开关关闭时行为不变。

**阻塞于：** 01 轮次口径改为 assistant 计数

**状态：** ready-for-agent

- [ ] Config 新增 `context_truncation_rounds=10`、`context_truncation_threshold=2000`、`context_truncation_head_chars=1500`、`context_truncation_tail_chars=500`；`context_truncation_enabled` 默认仍为 `false`。
- [ ] Config 创建时校验 `rounds >= 0`、`threshold > 0`、`head >= 0`、`tail >= 0`、`head + tail <= threshold`；任一条件不满足时不报错，把四个裁剪参数整组重置为默认值（10/2000/1500/500），`context_truncation_enabled` 保持传入值，继续构建。
- [ ] pruner 只处理 `role="tool"`；user/assistant/system/summary 内容不变；返回新列表，被裁剪消息是副本，history 原始 content 不被修改。
- [ ] 最近轮内（工具消息之后的 assistant 消息数 < rounds）且 `len(content) >= threshold` 时，候选为“前 head 字符 + 换行 + marker + 换行 + 后 tail 字符”；marker 使用输入列表下标 i、偏移 head、长度 `len(content) - head - tail`；候选长度比较包含换行，只有候选严格短于原文才替换。
- [ ] 轮次外（工具消息之后的 assistant 消息数 >= rounds）时，整体替换为 marker，偏移 0、长度 `len(original)`；只有 marker 严格短于原文才替换。
- [ ] marker 文案固定为：最近轮内 `已裁剪，详情参见记忆{i}中偏移{head}，长度{l}`；轮次外 `已裁剪，详情参见记忆{i}中偏移0，长度{l}`；最近轮内 marker 与头、尾之间各插入一个换行，轮次外直接使用 marker、不拼接。
- [ ] `len(content) < threshold` 的最近消息保持原样；短旧消息在 marker 更长时保持原样；`tail == 0` 时不会把整段内容重复拼到尾部，但 marker 后的换行保留。
- [ ] i 是 pruner 输入列表下标：不含 builder 注入的 system、含 summary；裁剪发生在丢弃 summary 之前，summary 随后被丢弃不会改变 marker 的 i。
- [ ] 被裁剪的工具消息保留 id、tool_call_id、role、metadata 等字段，转换给模型时仍带 tool_call_id。
- [ ] builder 在 `context_truncation_enabled` 为真时调用 pruner；`extra_messages` 不参与裁剪；开关为假时输出与当前行为一致。
- [ ] 新增/更新 builder 测试，覆盖最近轮内/轮次外、阈值边界、只更短才替换、非 tool 消息、summary 占位、system 不偏移、history 不变、开关关闭、非法配置回退默认值、marker 前后换行；轮次边界与 01 的口径一致。
- [ ] 项目说明文档中关于“截断模块为空实现”的过期描述同步更新。
- [ ] 上下文测试集全部通过；测试不联网、不写文件、不调用真实模型。
