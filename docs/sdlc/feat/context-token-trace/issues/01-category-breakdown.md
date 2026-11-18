# 01: 类别区与元数据骨架

**要构建什么：** 一次 CodeAgent run（摘要关闭、无收尾的正常主循环）结束后，会话元数据里能看到本次 run 的输入 token 按 system、工具 schema、user、assistant、tool 结果五类拆分；有 usage 的主循环请求类别合计等于其真实输入之和，现有总量/步数/请求次数不变。摘要、摘要调用、收尾指令三个键先以 0 存在，工具区为空，摘要次数为 0。

**阻塞于：** 无（可立即开始）

**状态：** ready-for-agent

- [ ] builder 为实际发送列表逐条产出类别标注，与列表一一对应；system、user、assistant、tool 结果可区分；标注基于 pruner 裁剪、summary 遮蔽后的发送视图，被裁 tool 内容按 marker 计。
- [ ] 类别标注不得依赖发送字典的 role 猜测 summary/extra；本票只验证主循环五类，summary/extra 的标注在 03 接入。
- [ ] 每次主循环请求发送前按类别 `len(str)` 算占比，拿到真实 `prompt_tokens` 后按占比分配；整数余数按最大余数法处理，类别合计精确等于该请求真实输入；无 usage 请求不分配。
- [ ] `_session_metadata.context_usage` 按约定 schema 出现：categories 八键齐全，本票 system/tool_schema/user/assistant/tool_result 可非零，summary_message/summary_call/conclude_instruction 为 0；tools 为空，summary_calls 为 0。
- [ ] 现有 input/output/cached/total、total_steps、llm_requests、费用口径不变；输出不拆。
- [ ] 测试覆盖 builder 标注与 CodeAgent 分配；用假 LLM，不联网、不调用真实模型；覆盖裁剪视图与无 usage 请求。
- [ ] 摘要开启、收尾、工具区、摘要次数不在本票验收范围，由 02/03 接入。
