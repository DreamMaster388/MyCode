# Intent: SWE-bench Lite 评测容器增加专用工具路径校验

作者：lyd。状态：已批准。

## 问题
本地专用文件工具已有路径校验，但容器版 Read/Write/Edit/Grep/Glob 没有，
评测中的行为与本地不一致，也无法做“有/无路径校验”的消融对比。

## 预期结果
- 容器内五个文件工具在执行前按与本地一致的规则校验路径：
  默认 trusted 语义，全盘可读，仅 `/testbed`、`/dev/null`、`/tmp` 可写；
  越界直接拒绝并返回统一拒绝标记。
- 校验在容器内完成，覆盖相对路径、绝对路径、`..` 与软链逃逸。
- `eval/run_predictions.py` 新增 `--no-path-guard`：默认开启校验，
  显式传入时才关闭并完全恢复当前行为，用于消融实验。

## 影响面
- 使用容器评测与做消融实验的人；
- `eval/container_tools.py`、`eval/docker_sandbox.py`、
  `eval/container_runner.py`、`eval/run_predictions.py`。

## 约束
- 校验必须在容器内、工具执行之前发生，不能只在宿主机判断。
- 不引入交互审批；越界 fail-closed。
- 开关只影响路径校验，不影响补丁提取、网络、工具接口等其他评测行为。
- 关闭校验时不得改变工具返回值或引入额外路径判断开销。

## 未定
- 是否把拒绝次数写入 diagnostics，供消融统计；本轮不做，后续可加。

下一步：/sdlc-spec

批准：lyd @ 2026-11-06
