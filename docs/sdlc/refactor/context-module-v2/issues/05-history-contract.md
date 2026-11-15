# 05: history 契约瘦身

**要构建什么：** history 只保留原始记录与摘要的存储契约：追加/id、读取副本、清空、序列化/反序列化、轮次与边界查询；删除上下文构建、摘要生成和 NotEnoughRoundsError。旧测试重写为存储/序列化用例。

**阻塞于：** 02 朴素 builder 贯通、04 token 计量迁移到 builder

**状态：** ready-for-agent

- [ ] history 只暴露存储相关 API；build_context、build_snapshot、摘要生成、NotEnoughRoundsError 不再存在。
- [ ] append 仍按追加顺序分配 id；显式 id 推进计数器；get_history 返回副本；clear 只清消息，不重置 id 计数器。
- [ ] to_dict/load_from_dict 保持 history/next_id/created_at/rounds 格式；旧会话可加载；缺失 id 时补号。
- [ ] summary.replace 遮蔽由 builder 负责；history 不再参与上下文组装。
- [ ] 旧 history 测试重写为存储/序列化用例；context 测试全绿。
- [ ] 其他 Agent 依赖的 get_history/append/_history 兼容接口不变。
