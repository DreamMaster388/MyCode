# 04: token 计量迁移到 builder

**要构建什么：** builder 接管真实 prompt_tokens 校准和未计量尾部估算，并提供 should_compress；CodeAgent 每次 LLM 返回后把 prompt_tokens 写入 builder；history 清空或加载后 builder 状态归零。朴素链路不调用 should_compress；history 的 token 计量删除。

**阻塞于：** 02 朴素 builder 贯通（建议在 03 之后执行，避免同文件冲突）

**状态：** ready-for-agent

- [ ] builder 记录真实 prompt_tokens 和对应历史右边界；未计量尾部按字符数除以 4 估算；should_compress 严格大于阈值才为真。
- [ ] CodeAgent 每次 LLM 返回后把 prompt_tokens 写入 builder；会话 token 统计口径不变。
- [ ] history 清空或加载后 builder 计量状态归零，不会用旧基线判断新历史。
- [ ] 朴素链路不调用 should_compress；方法保留给下一轮链 2。
- [ ] history 不再保留 token 计量 API；相关测试迁移到 builder 测试。
- [ ] 现有 usage/收尾测试改用 builder 替身并通过。
