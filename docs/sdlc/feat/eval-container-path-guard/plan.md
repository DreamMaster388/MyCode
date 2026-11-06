# Plan: SWE-bench Lite 评测容器专用工具路径校验（来自 spec.md）

## 改动文件
- eval/container_path_guard.py（新增）
- eval/docker_sandbox.py
- eval/container_tools.py
- eval/container_runner.py
- eval/run_predictions.py
- test/eval/test_container_path_guard.py（新增）
- README.md

## 顺序
1. 新增守卫模块：容器内 realpath 归一，按路径分量判根；可读根 `/`，可写根 `/testbed`、`/dev/null`、`/tmp`；写目标不存在时按父目录归一；放行退出 0，拒绝退出 13 并输出 `[sandbox: file access denied]` 标记。
2. DockerSandbox 增加 path_guard 参数：开启时把守卫前导拼进 Read/Write/Edit/Glob 的容器脚本和 Grep 命令，在同一容器进程里先校验后 I/O；Edit 的路径改由 argv 传入、stdin 仍传 JSON；关闭时脚本与命令原样、零注入。
3. 五个文件工具把 rc=13 映射为 ACCESS_DENIED，Write/Edit 此时尚未落盘；Bash 不接守卫。
4. run_instance 接收 path_guard 并传给 DockerSandbox；diagnostics 始终记录 path_guard 值。
5. run_predictions 增加 --no-path-guard，顺序与并发两处调用都传入；默认开启。
6. 补单测，覆盖写边界、`..`、软链逃逸、`/tmp` 与 `/dev/null` 放行、关闭开关不注入守卫。
7. README 评测章节补充开关说明。

## 风险
- 守卫只随打开开关的那一次 exec 进入容器，不增加额外容器往返；拒绝时脚本在 I/O 前退出。
- 全盘可读使读类工具不会拒绝越界，测试只应断言写的拒绝。
- 拒绝码 13 必须与既有 2/3 区分避免误映射；Grep 的 1（无匹配）仍按成功处理。

## 证明方式
R1/R3 → 单测断言守卫前导在 I/O 前执行，拒绝返回 ACCESS_DENIED 且文件未变。
R2 → 写 `/tmp`、`/dev/null`、`/testbed` 内成功；写 `/testbed` 外、`..`、软链目标失败。
R4 → 相对/绝对/`..`/软链统一按 realpath 判定。
R5/R6 → path_guard=False 脚本不含守卫；Bash 无守卫。

批准：lyd @ 2026-11-06。

下一步：/sdlc-build
