# 04: token 计量迁移到 builder

**要构建什么：** builder 接管真实 prompt_tokens 校准和未计量尾部估算，并提供 should_compress；CodeAgent 每次 LLM 返回后把 prompt_tokens 写入 builder；history 清空或加载后 builder 状态归零。history 的 token 计量删除。

**阻塞于：** 02 builder 组装上下文

**状态：** done

- [x] builder 记录真实 prompt_tokens 和对应历史右边界；未计量尾部按渲染后字符数除以 4 估算；should_compress 严格大于阈值才为真。
- [x] CodeAgent 每次 LLM 返回后把 prompt_tokens 写入 builder；会话 token 统计口径不变。
- [x] history 清空或加载后 builder 计量状态归零，不会用旧基线判断新历史。
- [x] should_compress 本轮没有调用方，方法保留给后续治理模块。
- [x] history 不再保留 token 计量 API；相关测试迁移到 builder 测试。
- [x] 现有 usage/收尾测试改用 builder 并通过。
