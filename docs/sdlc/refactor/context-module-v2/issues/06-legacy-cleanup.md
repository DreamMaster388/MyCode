# 06: 旧截断器与配置/eval/README 清理

**要构建什么：** 删除旧截断器和落盘引用；配置收敛为三个开关 + context_window + compression_threshold，删除旧截断/摘要/历史长度字段；eval 不再传 tool_output_dir；README 更新。全仓不再把旧行为描述为现行能力。

**阻塞于：** 01 工具结果全量进入上下文、03 三开关与占位分支、05 history 契约瘦身

**状态：** done

- [x] 旧截断器类、落盘写入/引用、Agent 的 truncator 属性不再存在。
- [x] Config 只保留三个开关 + context_window + compression_threshold；旧字段全部删除。
- [x] eval 构造不再传 tool_output_dir；eval 中无旧常量/参数残留。
- [x] README 不再把截断/落盘/旧配置描述为现行行为；补充三开关与全文工具输出说明。
- [x] 运行时代码与 eval 中不再引用旧截断器/旧配置字段；README 不再把旧行为描述为现行能力。
- [x] context 与 core 相关测试通过；不产生落盘文件。
