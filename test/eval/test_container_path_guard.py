"""容器内路径守卫与开关的单测：全部在宿主机跑，不需要 Docker。"""
import json
import os

import pytest

from agents.tools.errors import ToolErrorCode
from agents.tools.response import ToolStatus
from eval import container_path_guard as guard
from eval.container_tools import (
    ContainerEditTool,
    ContainerGlobTool,
    ContainerGrepTool,
    ContainerReadTool,
    ContainerWriteTool,
)
from eval.docker_sandbox import DockerSandbox, ExecResult


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


@pytest.fixture
def write_root(repo, monkeypatch):
    """把可写根收敛到临时仓库，避免 /tmp 默认放行干扰断言。"""
    monkeypatch.setattr(guard, "WRITE_ROOTS", (str(repo),))
    return repo


# ---------------------------------------------------------------- 判定逻辑 --

def test_write_inside_root_allowed(write_root):
    assert guard.check("src/a.py", "write", str(write_root)) is None
    assert guard.check(str(write_root / "src" / "a.py"), "write", str(write_root)) is None


def test_write_outside_root_denied(write_root, tmp_path):
    outside = tmp_path / "outside.txt"

    message = guard.check(str(outside), "write", str(write_root))

    assert message
    assert "outside writable roots" in message


def test_write_dotdot_escape_denied(write_root, tmp_path):
    message = guard.check("../outside.txt", "write", str(write_root))

    assert message
    assert str(tmp_path / "outside.txt") in message


def test_write_prefix_sibling_is_not_inside(write_root, tmp_path):
    message = guard.check(str(tmp_path / "repo-other" / "a.py"), "write", str(write_root))

    assert message


def test_write_tmp_and_dev_null_allowed():
    assert guard.check("/tmp/agent-scratch.txt", "write") is None
    if os.path.exists("/dev/null"):
        assert guard.check("/dev/null", "write") is None


@pytest.mark.skipif(os.name == "nt", reason="symlink test is POSIX-only")
def test_write_symlink_escape_denied(write_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = write_root / "link"
    link.symlink_to(outside, target_is_directory=True)

    message = guard.check("link/x.py", "write", str(write_root))

    assert message
    assert str(outside / "x.py") in message


@pytest.mark.skipif(os.name == "nt", reason="symlink test is POSIX-only")
def test_write_symlink_inside_allowed(write_root):
    inside = write_root / "inside"
    inside.mkdir()
    link = write_root / "link"
    link.symlink_to(inside, target_is_directory=True)

    assert guard.check("link/x.py", "write", str(write_root)) is None


def test_read_allows_any_path():
    assert guard.check("/etc/hostname", "read", "/testbed") is None
    assert guard.check("/testbed/src/a.py", "read", "/testbed") is None


def test_enforce_denies_with_marker_and_exit_code(write_root, tmp_path, capsys):
    with pytest.raises(SystemExit) as excinfo:
        guard.enforce(str(tmp_path / "outside.txt"), "write", str(write_root))

    assert excinfo.value.code == guard.DENY_EXIT
    assert guard.DENIAL_MARKER in capsys.readouterr().out


def test_main_cli_exit_codes(write_root, tmp_path):
    assert guard.main(["write", str(write_root), str(write_root / "ok.py")]) == 0
    assert guard.main(["write", str(write_root), str(tmp_path / "no.py")]) == guard.DENY_EXIT
    assert guard.main([]) == 2


# ---------------------------------------------------------------- 开关注入 --

def _sandbox(path_guard=True):
    return DockerSandbox(image="dummy", instance_id="demo", path_guard=path_guard)


def _capture(sandbox):
    calls = []

    def fake_run(args, input=None, timeout=None):
        calls.append({"args": list(args), "input": input})
        return ExecResult(0, "ok", "")

    sandbox._run = fake_run
    return calls


def _python_code(call):
    args = call["args"]
    return args[args.index("-c") + 1]


def test_read_guard_precedes_io():
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox)

    sandbox.read_text("src/a.py")

    code = _python_code(calls[0])
    assert 'enforce(sys.argv[1], "read"' in code
    assert code.index('enforce(sys.argv[1], "read"') < code.index("if os.path.isdir(p)")


def test_write_guard_precedes_io():
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox)

    sandbox.write_text("src/a.py", "x = 1")

    code = _python_code(calls[0])
    assert 'enforce(sys.argv[1], "write"' in code
    assert code.index('enforce(sys.argv[1], "write"') < code.index("os.makedirs")


def test_edit_paths_go_through_argv_and_guard():
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox)

    sandbox.edit_text("src/a.py", "old", "new")

    call = calls[0]
    code = _python_code(call)
    assert 'enforce(sys.argv[1], "write"' in code
    assert code.index('enforce(sys.argv[1], "write"') < code.index("json.load(sys.stdin)")
    assert json.loads(call["input"]) == {"old": "old", "new": "new"}


def test_glob_guard_covers_base_and_pattern():
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox)

    sandbox.glob_files("**/*.py", "src")

    code = _python_code(calls[0])
    assert 'enforce(sys.argv[2], "read"' in code
    assert 'enforce(os.path.join(sys.argv[2], sys.argv[1]), "read"' in code
    assert code.index("enforce(os.path.join") < code.index("glob.glob")


def test_grep_guard_is_prefixed_to_command():
    sandbox = _sandbox(path_guard=True)
    calls = _capture(sandbox)

    sandbox.grep("needle", "src")

    command = calls[0]["args"][-1]
    assert "raise SystemExit(main(" in command
    assert "&&" in command


def test_guard_switch_off_injects_nothing():
    sandbox = _sandbox(path_guard=False)
    calls = _capture(sandbox)

    sandbox.read_text("src/a.py")
    sandbox.write_text("src/a.py", "x")
    sandbox.glob_files("**/*.py")
    sandbox.grep("needle")

    read_code = _python_code(calls[0])
    write_code = _python_code(calls[1])
    glob_code = _python_code(calls[2])
    grep_command = calls[3]["args"][-1]
    assert "enforce(" not in read_code
    assert "enforce(" not in write_code
    assert "enforce(" not in glob_code
    assert "raise SystemExit(main(" not in grep_command


def test_edit_switch_off_still_uses_argv_path():
    sandbox = _sandbox(path_guard=False)
    calls = _capture(sandbox)

    sandbox.edit_text("src/a.py", "old", "new")

    call = calls[0]
    assert "enforce(" not in _python_code(call)
    assert "sys.argv[1]" in _python_code(call)
    assert json.loads(call["input"]) == {"old": "old", "new": "new"}


# ---------------------------------------------------------------- 响应映射 --

class _DeniedSandbox:
    """所有文件操作都返回守卫拒绝的假沙箱。"""

    def _denied(self, *args, **kwargs):
        return ExecResult(guard.DENY_EXIT, guard.DENIAL_MARKER + " denied", "")

    read_text = write_text = edit_text = grep = glob_files = _denied


@pytest.mark.parametrize("tool_cls, params", [
    (ContainerReadTool, {"path": "x"}),
    (ContainerWriteTool, {"path": "x", "content": "y"}),
    (ContainerEditTool, {"path": "x", "old_string": "a", "new_string": "b"}),
    (ContainerGrepTool, {"pattern": "a"}),
    (ContainerGlobTool, {"pattern": "*"}),
])
def test_guard_denial_maps_to_access_denied(tool_cls, params):
    resp = tool_cls(_DeniedSandbox()).run(params)

    assert resp.status is ToolStatus.ERROR
    assert resp.error_info["code"] == ToolErrorCode.ACCESS_DENIED
    assert guard.DENIAL_MARKER in resp.text
