"""验收测试：SWE-bench Lite 容器专用工具路径校验（spec R1-R6）。

独立性说明：本文件先按 `docs/sdlc/feat/eval-container-path-guard/spec.md`
的 R1-R6 推导断言，再按 `eval/` 的公开接口接线；不修改实现，也不修改既有测试。

全部用例在宿主机运行，不需要 Docker：
- 路径判定直接调用容器侧守卫模块的纯判定函数（容器脚本里跑的同一份源码）；
- 守卫脚本的实际执行用 `sys.executable` 原样跑 DockerSandbox 捕获到的容器脚本；
- `/testbed` 在宿主机通常不存在，守卫的 realpath 判定不要求目标存在，
  因此写边界可以直接断言；真实容器内 I/O 只用显式 smoke 验证（不在本套件内）。
"""
from __future__ import annotations

import contextlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agents.tools.errors import ToolErrorCode
from agents.tools.response import ToolStatus
from eval import build_patch
from eval import container_path_guard as guard
from eval import container_runner as container_runner
from eval import run_predictions
from eval.container_tools import (
    ContainerBashTool,
    ContainerEditTool,
    ContainerGlobTool,
    ContainerGrepTool,
    ContainerReadTool,
    ContainerWriteTool,
    build_container_tools,
)
from eval.docker_sandbox import DockerSandbox, ExecResult

REPO_ROOT = Path(__file__).resolve().parents[2]
DENIED_RC = 13
DENIED_MARKER = "[sandbox: file access denied]"


# ------------------------------------------------------------------ 辅助 --

def _sandbox(path_guard=True):
    return DockerSandbox(image="dummy", instance_id="guard-acceptance", path_guard=path_guard)


def _capture(sandbox, monkeypatch):
    """拦截底层 docker 调用，返回容器 exec 的 argv/input 记录。"""
    calls = []

    def fake_run(args, input=None, timeout=None):
        calls.append({"args": list(args), "input": input})
        return ExecResult(0, "ok", "")

    monkeypatch.setattr(sandbox, "_run", fake_run)
    return calls


def _docker_argv(call):
    argv = call["args"]
    assert argv[0] == "exec", "文件工具操作必须通过 docker exec 在容器内执行: %r" % (argv,)
    return argv


def _normalized_argv(call, sandbox):
    """抹掉随机容器名，便于比较开关前后的命令。"""
    return ["<container>" if token == sandbox.name else token for token in call["args"]]


def _python_code(call):
    argv = _docker_argv(call)
    assert "-c" in argv
    return argv[argv.index("-c") + 1]


def _run_container_script(script, argv, stdin=""):
    """用宿主机 python 原样执行容器脚本（守卫逻辑与容器内一致）。"""
    return subprocess.run(
        [sys.executable, "-c", script, *argv],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=120,
    )


@contextlib.contextmanager
def _outside_writable_root():
    """工作区内临时目录：不在 /testbed、/tmp、/dev/null 之内（写应被拒）。"""
    try:
        path = Path(tempfile.mkdtemp(prefix=".guard-accept-", dir=str(REPO_ROOT)))
    except OSError as exc:  # 只读工作区时跳过，不误报实现问题
        pytest.skip("cannot create temp dir under repo root: %s" % exc)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _allow(path, kind="write", cwd="/"):
    return guard.check(path, kind, cwd) is None


def _deny(path, kind="write", cwd="/"):
    return guard.check(path, kind, cwd) is not None


# 一个可复用的已拒绝沙箱：五个文件方法统一返回 rc=13 + 标记。
class _DeniedSandbox:
    def __init__(self):
        self.calls = 0

    def _denied(self, *args, **kwargs):
        self.calls += 1
        return ExecResult(DENIED_RC, DENIED_MARKER + " denied", "")

    read_text = write_text = edit_text = grep = glob_files = _denied


# =================================================================== R1 ====
# 容器内 Read/Write/Edit/Grep/Glob 在真正读写前完成路径校验，不得只在宿主机判断。

@pytest.mark.parametrize(
    "method,args,body_marker",
    [
        ("read_text", ("src/a.py",), "if os.path.isdir(p)"),
        ("write_text", ("src/a.py", "x = 1"), "data = sys.stdin.read()"),
        ("edit_text", ("src/a.py", "old", "new"), "json.load(sys.stdin)"),
        ("glob_files", ("**/*.py", "src"), "glob.glob("),
    ],
    ids=["Read", "Write", "Edit", "Glob"],
)
def test_r1_guard_precedes_io_in_container_command(monkeypatch, method, args, body_marker):
    # 宿主机上不允许执行任何路径判定：校验必须随脚本进入容器。
    def _host_check_forbidden(*a, **k):
        raise AssertionError("路径校验不得只在宿主机进行")

    monkeypatch.setattr(guard, "check", _host_check_forbidden)

    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox, monkeypatch)
    getattr(sandbox, method)(*args)

    code = _python_code(calls[0])
    assert "def enforce(" in code, "容器脚本里没有守卫实现"
    assert DENIED_MARKER in code and "DENY_EXIT" in code
    assert code.index("def enforce(") < code.index(body_marker), "守卫必须在 I/O 之前"
    assert "enforce(" in code[: code.index(body_marker)], "I/O 之前必须调用守卫"


def test_r1_grep_guard_precedes_grep_command(monkeypatch):
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox, monkeypatch)

    sandbox.grep("needle", "src")

    command = _docker_argv(calls[0])[-1]
    assert DENIED_MARKER in command and "def enforce(" in command
    guard_pos = command.index("raise SystemExit(main(")
    grep_pos = min(
        pos for pos in (command.find("command -v rg"), command.find("grep -r"))
        if pos >= 0
    )
    assert guard_pos < grep_pos, "Grep 守卫必须先于搜索命令执行"


def test_r1_guard_script_is_executed_inside_container_argv(monkeypatch):
    """守卫代码必须位于容器 exec 的 argv 中，而不是宿主机侧调用。"""
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox, monkeypatch)
    sandbox.write_text("src/a.py", "x = 1")

    argv = _docker_argv(calls[0])
    assert argv[0] == "exec"
    assert "def enforce(" in argv[argv.index("-c") + 1]
    assert str(REPO_ROOT) not in argv[argv.index("-c") + 1], "容器脚本不应包含宿主机路径"


# =================================================================== R2 ====
# 默认策略与本地 trusted 语义一致：全盘可读；仅 /testbed、/dev/null、/tmp 可写。

@pytest.mark.parametrize("path", [
    "/testbed/new.py",
    "/testbed/deep/new.py",
    "/testbed/src/pkg/mod.py",
    "/tmp/new.py",
    "/tmp/deep/new.py",
    "/tmp/agent-scratch.txt",
    "/dev/null",
])
def test_r2_default_policy_allows_writes_in_writable_roots(path):
    assert _allow(path, "write")


@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "/root/.bashrc",
    "/home/lyd/notes.txt",
    "/var/tmp/scratch.txt",
    "/usr/local/bin/tool",
    "/dev/sda",
    "/dev/null/x",
    "/proc/self/cmdline",
])
def test_r2_default_policy_denies_writes_outside_writable_roots(path):
    assert _deny(path, "write")


@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "/root/.bashrc",
    "/home/lyd/notes.txt",
    "/var/tmp/scratch.txt",
    "/testbed/src/mod.py",
    "/tmp/x",
    "/dev/null",
    "/",
])
def test_r2_default_policy_allows_reads_anywhere(path):
    assert _allow(path, "read")


def test_r2_read_via_symlink_outside_is_still_allowed(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    link = tmp_path / "passwd-link"
    link.symlink_to("/etc/passwd")
    assert _allow(str(link), "read")


def test_r2_tmp_write_actually_succeeds_through_guard_script(monkeypatch, tmp_path):
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox, monkeypatch)
    target = tmp_path / "allowed.txt"

    sandbox.write_text(str(target), "allowed")
    proc = _run_container_script(
        _python_code(calls[0]), [str(target)], stdin="allowed"
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert target.read_text(encoding="utf-8") == "allowed"


def test_r2_dev_null_write_actually_succeeds_through_guard_script(monkeypatch):
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox, monkeypatch)

    sandbox.write_text("/dev/null", "discard me")
    proc = _run_container_script(_python_code(calls[0]), ["/dev/null"], stdin="x")

    assert proc.returncode == 0, proc.stdout + proc.stderr


# =================================================================== R3 ====
# 越界访问直接拒绝，返回 ACCESS_DENIED 与统一拒绝标记，不进入审批。

def test_r3_denial_contract_constants():
    assert DENIED_RC == guard.DENY_EXIT == 13
    assert DENIED_MARKER == guard.DENIAL_MARKER == "[sandbox: file access denied]"


def test_r3_write_denied_before_io_and_target_not_created(monkeypatch):
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox, monkeypatch)

    with _outside_writable_root() as root:
        target = root / "pwned.txt"
        sandbox.write_text(str(target), "pwned")
        proc = _run_container_script(
            _python_code(calls[0]), [str(target)], stdin="pwned"
        )

        assert proc.returncode == DENIED_RC
        assert DENIED_MARKER in proc.stdout
        assert not target.exists(), "守卫拒绝后不得落盘"


def test_r3_edit_denied_before_io_and_file_unchanged(monkeypatch):
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox, monkeypatch)

    with _outside_writable_root() as root:
        target = root / "keep.txt"
        target.write_text("old\n", encoding="utf-8")
        sandbox.edit_text(str(target), "old", "new")
        proc = _run_container_script(
            _python_code(calls[0]),
            [str(target)],
            stdin=json.dumps({"old": "old", "new": "new"}),
        )

        assert proc.returncode == DENIED_RC
        assert DENIED_MARKER in proc.stdout
        assert target.read_text(encoding="utf-8") == "old\n", "守卫拒绝后不得修改文件"


@pytest.mark.parametrize(
    "tool_cls,params",
    [
        (ContainerReadTool, {"path": "x"}),
        (ContainerWriteTool, {"path": "x", "content": "y"}),
        (ContainerEditTool, {"path": "x", "old_string": "a", "new_string": "b"}),
        (ContainerGrepTool, {"pattern": "a"}),
        (ContainerGlobTool, {"pattern": "*"}),
    ],
    ids=["Read", "Write", "Edit", "Grep", "Glob"],
)
def test_r3_denial_maps_to_access_denied_without_approval_roundtrip(tool_cls, params):
    sandbox = _DeniedSandbox()

    resp = tool_cls(sandbox).run(params)

    assert sandbox.calls == 1, "拒绝是直接返回，不应有审批/重试往返"
    assert resp.status is ToolStatus.ERROR
    assert resp.error_info["code"] == ToolErrorCode.ACCESS_DENIED
    assert DENIED_MARKER in resp.text


def test_r3_guard_main_returns_denial_marker_and_rc_for_each_path(capsys):
    rc = guard.main(["write", "/testbed", "/etc/passwd", "/tmp/ok.py"])

    assert rc == DENIED_RC
    assert DENIED_MARKER in capsys.readouterr().out


# =================================================================== R4 ====
# 相对/绝对/`..`/软链及尚不存在的写目标都按容器内 realpath 判定，软链逃逸视为越界。

@pytest.mark.parametrize("cwd,path,allowed", [
    ("/testbed", "src/mod.py", True),
    ("/testbed", "sub/../ok.py", True),
    ("/testbed", "./pkg/mod.py", True),
    ("/testbed", "../etc/passwd", False),
    ("/testbed", "../../etc/passwd", False),
    ("/testbed", "sub/../../etc/passwd", False),
    ("/tmp", "scratch/x.py", True),
    ("/tmp", "../etc/passwd", False),
])
def test_r4_relative_paths_resolve_against_container_cwd(cwd, path, allowed):
    assert _allow(path, "write", cwd) is allowed


@pytest.mark.parametrize("path,allowed", [
    ("/testbed/../etc/passwd", False),
    ("/tmp/../etc/passwd", False),
    ("/testbed/sub/../ok.py", True),
    ("/tmp/../tmp/ok.py", True),
    ("/tmp/../../etc/passwd", False),
    ("/etc/../etc/passwd", False),
])
def test_r4_dotdot_normalized_before_decision(path, allowed):
    assert _allow(path, "write") is allowed


@pytest.mark.parametrize("path,allowed", [
    ("/testbed/new/deep/file.py", True),
    ("/tmp/new/deep/file.py", True),
    ("/testbed-evil/new/deep/file.py", False),
    ("/tmp-evil/new/deep/file.py", False),
])
def test_r4_nonexistent_write_targets_use_parent_realpath(path, allowed):
    assert _allow(path, "write") is allowed


@pytest.mark.parametrize("path", [
    "/testbed-other/x.py",
    "/testbed.backup/x.py",
    "/testbed2/x.py",
    "/tmp-other/x.py",
    "/tmp2/x.py",
    "/testbed/../testbed2/x.py",
])
def test_r4_prefix_sibling_directories_are_not_inside(path):
    assert _deny(path, "write")


def test_r4_symlink_escape_is_denied(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    link = tmp_path / "escape"
    link.symlink_to("/etc/passwd")

    assert _deny(str(link), "write")
    # 读是全盘放行，同一软链读应允许。
    assert _allow(str(link), "read")


def test_r4_directory_symlink_escape_is_denied(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    link = tmp_path / "etc-dir"
    link.symlink_to("/etc")

    assert _deny(str(link / "passwd"), "write")


def test_r4_symlink_inside_writable_root_is_allowed(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    target = tmp_path / "real.txt"
    target.write_text("ok", encoding="utf-8")
    link = tmp_path / "inside-link"
    link.symlink_to(target)

    assert _allow(str(link), "write")


def test_r4_symlink_into_testbed_is_allowed(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    link = tmp_path / "testbed-link"
    link.symlink_to("/testbed/ok.py")

    assert _allow(str(link), "write")


def test_r4_dangling_symlink_outside_is_denied(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    link = tmp_path / "dangling"
    link.symlink_to("/home/lyd/guard-acceptance-should-not-exist")

    assert _deny(str(link), "write")


def test_r4_nonexistent_target_under_escaping_symlink_parent_is_denied(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    link = tmp_path / "escape-dir"
    link.symlink_to(REPO_ROOT)

    assert _deny(str(link / "brand-new.py"), "write")


def test_r4_testbed_relative_target_with_dotdot_stays_inside():
    # /testbed/sub/../x 归一后仍是 /testbed/x，应放行。
    assert _allow("sub/../x.py", "write", cwd="/testbed")


# =================================================================== R5 ====
# run_predictions 新增 --no-path-guard：默认开启，显式传入时关闭并恢复当前行为。

def _run_predictions_cli(monkeypatch, tmp_path, extra_argv, workers):
    captured = []
    instance = {"instance_id": "demo__1", "image": "dummy:latest"}

    monkeypatch.setattr(run_predictions, "load_instances", lambda *a, **k: [instance])
    monkeypatch.setattr(
        run_predictions, "select_instances", lambda insts, ids, limit: list(insts)
    )

    def fake_run_instance(
        inst, max_steps=50, pull_timeout=7200, keep_container=False,
        network="none", path_guard=True,
    ):
        captured.append(path_guard)
        return {
            "instance_id": inst["instance_id"],
            "status": "ok",
            "model_patch": "",
            "patch_bytes": 0,
            "steps": 0,
            "tokens": 0,
            "duration_seconds": 0.0,
            "error": "",
            "diagnostics": {"path_guard": path_guard},
        }

    monkeypatch.setattr(run_predictions, "run_instance", fake_run_instance)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [
        "run_predictions.py",
        "--out", str(out),
        "--run-id", "acceptance",
        "--workers", str(workers),
        "--network", "none",
        *extra_argv,
    ])

    run_predictions.main()
    return captured, out / "acceptance"


@pytest.mark.parametrize("workers", [1, 2], ids=["sequential", "threaded"])
@pytest.mark.parametrize(
    "extra_argv,expected",
    [([], True), (["--no-path-guard"], False)],
    ids=["default-on", "no-path-guard"],
)
def test_r5_cli_switch_propagates_to_run_instance_and_diagnostics(
    monkeypatch, tmp_path, capsys, extra_argv, expected, workers
):
    captured, out_dir = _run_predictions_cli(monkeypatch, tmp_path, extra_argv, workers)

    assert captured == [expected]
    records = [
        json.loads(line)
        for line in (out_dir / "diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["diagnostics"]["path_guard"] is expected
    assert "path_guard=%s" % expected in capsys.readouterr().out


@pytest.mark.parametrize("path_guard", [True, False], ids=["on", "off"])
def test_r5_run_instance_forwards_switch_and_always_records_diagnostics(monkeypatch, path_guard):
    seen = {}

    class FakeSandbox:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def ensure_image(self, pull_timeout=7200):
            return ExecResult(1, "", "image unavailable in test")

        def stop(self):
            pass

    monkeypatch.setattr(container_runner, "DockerSandbox", FakeSandbox)
    instance = {
        "instance_id": "owner__1",
        "image": "dummy:latest",
        "test_patch": "",
        "test_files": [],
    }

    result = container_runner.run_instance(instance, path_guard=path_guard)

    assert seen["path_guard"] is path_guard
    assert result["diagnostics"]["path_guard"] is path_guard


def test_r5_tools_inherit_switch_from_sandbox():
    for path_guard in (True, False):
        sandbox = _sandbox(path_guard=path_guard)
        tools = build_container_tools(sandbox)
        assert all(tool.sandbox is sandbox for tool in tools)
        assert all(tool.sandbox.path_guard is path_guard for tool in tools)


# =================================================================== R6 ====
# 开关只作用于五个文件工具；Bash、补丁提取、网络与工具接口不变。

@pytest.mark.parametrize("rc,expected_status", [
    (0, ToolStatus.SUCCESS),
    (1, ToolStatus.SUCCESS),  # grep 无匹配是成功，不得误判为 ACCESS_DENIED
])
def test_r6_grep_rc_1_is_not_treated_as_denial(rc, expected_status):
    class GrepSandbox:
        def grep(self, *args, **kwargs):
            return ExecResult(rc, "" if rc == 1 else "match:1:needle", "")

    resp = ContainerGrepTool(GrepSandbox()).run({"pattern": "needle", "path": "."})

    assert resp.status is expected_status
    assert resp.error_info is None
    if rc == 1:
        assert "No matches found." in resp.text


def test_r6_grep_denial_still_distinct_from_rc_1():
    class GrepSandbox:
        def grep(self, *args, **kwargs):
            return ExecResult(guard.DENY_EXIT, guard.DENIAL_MARKER, "")

    resp = ContainerGrepTool(GrepSandbox()).run({"pattern": "needle"})

    assert resp.status is ToolStatus.ERROR
    assert resp.error_info["code"] == ToolErrorCode.ACCESS_DENIED


def test_r6_bash_is_not_guarded(monkeypatch):
    on = _sandbox(path_guard=True)
    off = _sandbox(path_guard=False)
    on_calls = _capture(on, monkeypatch)
    off_calls = _capture(off, monkeypatch)

    on.bash("cat /etc/passwd && echo hi")
    off.bash("cat /etc/passwd && echo hi")

    on_argv = _normalized_argv(on_calls[0], on)
    off_argv = _normalized_argv(off_calls[0], off)
    assert on_argv == off_argv, "开关不得改变 Bash"
    assert "enforce(" not in on_argv[-1]
    assert guard.DENIAL_MARKER not in on_argv[-1]
    assert on_argv[0:2] == ["exec", "-w"]


def test_r6_bash_rc_13_is_not_mapped_to_access_denied():
    class BashSandbox:
        def bash(self, command, timeout=300):
            return ExecResult(13, "out", "err")

    resp = ContainerBashTool(BashSandbox()).run({"command": "exit 13"})

    assert resp.status is ToolStatus.ERROR
    assert resp.error_info["code"] == ToolErrorCode.EXECUTION_ERROR
    assert resp.error_info["code"] != ToolErrorCode.ACCESS_DENIED


def test_r6_network_and_start_arguments_unchanged_by_switch(monkeypatch):
    on = _sandbox(path_guard=True)
    off = _sandbox(path_guard=False)
    on_calls = _capture(on, monkeypatch)
    off_calls = _capture(off, monkeypatch)

    on.start()
    off.start()

    on_run = next(c for c in on_calls if c["args"][0] == "run")
    off_run = next(c for c in off_calls if c["args"][0] == "run")
    assert _normalized_argv(on_run, on) == _normalized_argv(off_run, off)
    run_args = on_run["args"]
    assert "--network" in run_args and run_args[run_args.index("--network") + 1] == "none"
    for key, value in on.env.items():
        assert "%s=%s" % (key, value) in run_args


def test_r6_patch_extraction_is_not_guarded(monkeypatch):
    on = _sandbox(path_guard=True)
    off = _sandbox(path_guard=False)
    on_calls = _capture(on, monkeypatch)
    off_calls = _capture(off, monkeypatch)

    on.model_patch()
    off.model_patch()

    on_argv, on_input = _normalized_argv(on_calls[0], on), on_calls[0]["input"]
    off_argv, off_input = _normalized_argv(off_calls[0], off), off_calls[0]["input"]
    assert on_argv == off_argv
    assert on_input == off_input == build_patch.module_source()
    assert "def enforce(" not in on_argv[on_argv.index("-c") + 1]
    assert guard.DENIAL_MARKER not in on_input
    assert "container_path_guard" not in " ".join(on_argv)


@pytest.mark.parametrize(
    "method,args",
    [
        ("read_text", ("src/a.py",)),
        ("write_text", ("src/a.py", "x = 1")),
        ("edit_text", ("src/a.py", "old", "new")),
        ("glob_files", ("**/*.py", "src")),
    ],
    ids=["Read", "Write", "Edit", "Glob"],
)
def test_r6_switch_off_injects_nothing(monkeypatch, method, args):
    from eval import docker_sandbox as ds

    expected = {
        "read_text": ds._READ_SCRIPT,
        "write_text": ds._WRITE_SCRIPT,
        "edit_text": ds._EDIT_SCRIPT,
        "glob_files": ds._GLOB_SCRIPT,
    }[method]

    sandbox = _sandbox(path_guard=False)
    calls = _capture(sandbox, monkeypatch)
    getattr(sandbox, method)(*args)

    code = _python_code(calls[0])
    assert code == expected, "关闭守卫时脚本必须与现状完全一致"
    assert "def enforce(" not in code
    assert guard.DENIAL_MARKER not in code


def test_r6_switch_off_grep_injects_nothing(monkeypatch):
    sandbox = _sandbox(path_guard=False)
    calls = _capture(sandbox, monkeypatch)

    sandbox.grep("needle", "src")

    command = _docker_argv(calls[0])[-1]
    assert "raise SystemExit(main(" not in command
    assert "def enforce(" not in command
    assert guard.DENIAL_MARKER not in command
    assert "command -v rg" in command


def test_r6_switch_off_restores_unrestricted_write(monkeypatch):
    """关闭守卫后，同一越界写脚本应恢复为直接写（证明开关确实生效）。"""
    sandbox = _sandbox(path_guard=False)
    calls = _capture(sandbox, monkeypatch)

    with _outside_writable_root() as root:
        target = root / "written-with-guard-off.txt"
        sandbox.write_text(str(target), "restored")
        proc = _run_container_script(
            _python_code(calls[0]), [str(target)], stdin="restored"
        )

        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert target.read_text(encoding="utf-8") == "restored"


def test_r6_five_file_tools_keep_original_signatures():
    for cls in (
        ContainerReadTool,
        ContainerWriteTool,
        ContainerEditTool,
        ContainerGrepTool,
        ContainerGlobTool,
    ):
        params = list(inspect.signature(cls.__init__).parameters)
        assert params == ["self", "sandbox"], "%s 的构造签名不应改变" % cls.__name__

    names = [tool.name for tool in build_container_tools(_sandbox())]
    assert names == ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]
