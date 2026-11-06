# Review: SWE-bench Lite 评测容器专用工具路径校验

对照 intent / spec / plan 与当前 diff。

## 发现
阻塞：无。
重要：无。
建议：
1. `eval/docker_sandbox.py:258,265,274` + `eval/container_path_guard.py:17`：写根硬编码 `/testbed`，与可配置 `workdir` 不一致；当前调用方均用默认值，暂不影响行为。
2. `eval/container_path_guard.py:39`：NUL 等非法路径抛 `ValueError`，容器脚本 rc=1，未统一映射为 `ACCESS_DENIED`；仍 fail-closed。
3. `eval/docker_sandbox.py:284`：Glob 的模式校验只是字符串拼接，全盘可读下恒放行；将来收紧读策略时需按实际匹配路径校验。
4. `eval/docker_sandbox.py:82,276`：关闭开关时 Edit 的 path 已从 stdin 移到 argv，行为等价且 plan 已授权，但 spec 的“命令保持现状”未记录偏差；建议补一条关闭态 Edit 实跑用例。
5. `README.md:165`：建议补一句“不覆盖 Bash、不构成容器隔离”，避免误读为安全边界。
其余低价值观察 2 条：`module_source()` 每次调用重复读盘；`guard.main()` docstring 与 Grep 内嵌调用方式不符。不展开。

## 测试
R1–R6 均有对应用例；`test/eval` 178 passed、acceptance 93 passed、全量 362 passed/26 skipped，与 test-report 一致；既有测试未被削弱。

## 结论
通过。

下一步：人工提交、开 PR、合并。
