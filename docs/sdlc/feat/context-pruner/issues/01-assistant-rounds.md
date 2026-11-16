# 01: 轮次口径改为 assistant 计数

**要构建什么：** 会话的轮次统计和轮次边界改为按每条 assistant 消息计数：一次 user 请求内发生多次模型返回时会产生多轮；`rounds` 元数据、轮次计数和轮次边界都使用同一口径。

**阻塞于：** 无（可立即开始）

**状态：** done

- [x] `estimate_rounds()` 只统计 `role == "assistant"` 的消息，user/tool/system/summary 不计轮。
- [x] 一次 user 请求内出现 N 条 assistant 消息时，轮数为 N；工具调用中间回复、最终纯文本回答和失败兜底写入的 assistant 文本都各算一轮。
- [x] `find_round_boundaries()` 返回每条 assistant 消息在历史列表中的下标；空历史返回空列表。
- [x] `to_dict()` 的 `rounds` 字段使用新计数；`load_from_dict` 不依赖该字段，当前格式保存/加载往返仍正常。
- [x] `clear` / `load` 后 id 计数器与 revision 行为保持不变，既有 history 存储契约不被破坏。
- [x] 更新轮次相关测试：重写 `estimate_rounds` 的参数化期望，改写 `find_round_boundaries` 用例，更新 `to_dict` 的 `rounds` 断言；删除不再成立的既有轮次断言。
- [x] 上下文测试集全部通过；测试不联网、不写文件。
