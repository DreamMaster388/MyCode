# Test Report: SWE-bench Lite 评测容器专用工具路径校验

运行：
- `python -m pytest test/eval -q` → 178 passed
- `python -m pytest test/eval/test_eval_path_guard_acceptance.py -q` → 93 passed
- `HOME=/tmp python -m pytest test -q` → 362 passed, 26 skipped
- `python -m py_compile eval/container_path_guard.py eval/docker_sandbox.py eval/container_tools.py eval/container_runner.py eval/run_predictions.py` → exit 0

## 验收
R1 通过 —— `test_eval_path_guard_acceptance.py::test_r1_guard_precedes_io_in_container_command`、`::test_r1_grep_guard_precedes_grep_command`
R2 通过 —— `::test_r2_default_policy_allows_writes_in_writable_roots`、`::test_r2_default_policy_denies_writes_outside_writable_roots`、`::test_r2_tmp_write_actually_succeeds_through_guard_script`
R3 通过 —— `::test_r3_write_denied_before_io_and_target_not_created`、`::test_r3_denial_maps_to_access_denied_without_approval_roundtrip`
R4 通过 —— `::test_r4_relative_paths_resolve_against_container_cwd`、`::test_r4_symlink_escape_is_denied`、`::test_r4_prefix_sibling_directories_are_not_inside`
R5 通过 —— `::test_r5_cli_switch_propagates_to_run_instance_and_diagnostics`、`::test_r5_run_instance_forwards_switch_and_always_records_diagnostics`
R6 通过 —— `::test_r6_switch_off_injects_nothing`、`::test_r6_bash_is_not_guarded`、`::test_r6_grep_rc_1_is_not_treated_as_denial`

## 失败
无。

## 未覆盖
完整 Agent + LLM 实跑未执行；真实容器 smoke 仅作手动补充，未纳入自动化套件。
26 个 `test/sandbox/*` 用例因本环境 `HOME` 限制 skip，与本次改动无关。

## 结论
通过。

下一步：/sdlc-review
