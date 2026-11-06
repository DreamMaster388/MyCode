"""Landlock 启动器(landlock_exec.py)的直接行为测试。"""
import os
import shutil
import subprocess
import sys

import pytest

from agents.fs import SandboxMode, build_policy
from agents.sandbox import LandlockBackend, read_roots as sandbox_read_roots
from agents.sandbox.landlock_exec import (
    LAUNCHER_ERROR_EXIT,
    _ACCESS_FS,
    _handled_rights,
    _parse,
    _rule_access,
)

pytestmark = pytest.mark.skipif(
    not LandlockBackend().available(), reason="landlock unavailable"
)

_LAUNCHER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "agents", "sandbox", "landlock_exec.py")
)


def _read_roots(ws):
    """工作区策略对应的读根(系统 + 解释器 + 项目)。"""
    return sandbox_read_roots(build_policy(SandboxMode.WORKSPACE_WRITE, ws))


def _run(mode, command, *, read_roots=(), write_roots=()):
    argv = [sys.executable, _LAUNCHER, "--mode", mode]
    for root in read_roots:
        argv += ["--read-root", root]
    for root in write_roots:
        argv += ["--write-root", root]
    argv += ["--", "bash", "-c", command]
    return subprocess.run(argv, capture_output=True, text=True)


# ---------------- 参数解析 ----------------

def test_parse_defaults():
    assert _parse([]) == ("workspace-write", [], [], False, [])


def test_parse_separates_read_and_write_roots():
    mode, reads, writes, probe, command = _parse([
        "--mode", "read-only",
        "--read-root", "/r1",
        "--read-root", "/r2",
        "--write-root", "/w1",
        "--", "bash", "-c", "ls",
    ])
    assert mode == "read-only"
    assert reads == ["/r1", "/r2"]
    assert writes == ["/w1"]
    assert probe is False
    assert command == ["bash", "-c", "ls"]


def test_parse_probe_flag():
    assert _parse(["--probe"]) == ("workspace-write", [], [], True, [])


def test_parse_unknown_argument_raises():
    with pytest.raises(ValueError):
        _parse(["--bogus"])


# ---------------- 接管的权限位 ----------------

def test_handled_rights_include_read_and_write():
    bits = _handled_rights(3)
    for name in (
        "read_file", "read_dir", "write_file", "remove_dir", "remove_file",
        "make_reg", "refer", "truncate",
    ):
        assert bits & _ACCESS_FS[name], name
    assert not bits & _ACCESS_FS["execute"]


def test_handled_rights_gate_by_abi():
    assert not _handled_rights(1) & _ACCESS_FS["refer"]
    assert _handled_rights(2) & _ACCESS_FS["refer"]
    assert not _handled_rights(2) & _ACCESS_FS["truncate"]
    assert _handled_rights(3) & _ACCESS_FS["truncate"]


def test_rule_access_read_root_is_read_only(tmp_path):
    read_bits = _ACCESS_FS["read_file"] | _ACCESS_FS["read_dir"]
    write_bits = _ACCESS_FS["write_file"]

    access = _rule_access([str(tmp_path)], [], read_bits, write_bits)

    assert access[os.path.realpath(str(tmp_path))] == read_bits


def test_rule_access_file_read_root_grants_read_file_only(tmp_path):
    """文件读根不能授予 read_dir: 内核会对文件返回 EINVAL。"""
    data = tmp_path / "data.txt"
    data.write_text("x", encoding="utf-8")
    read_bits = _ACCESS_FS["read_file"] | _ACCESS_FS["read_dir"]
    write_bits = _ACCESS_FS["write_file"]

    access = _rule_access([str(data)], [], read_bits, write_bits)

    assert access[os.path.realpath(str(data))] == _ACCESS_FS["read_file"]


def test_rule_access_write_root_is_read_write(tmp_path):
    read_bits = _ACCESS_FS["read_file"] | _ACCESS_FS["read_dir"]
    write_bits = _ACCESS_FS["write_file"]

    access = _rule_access([], [str(tmp_path)], read_bits, write_bits)

    assert access[os.path.realpath(str(tmp_path))] == read_bits | write_bits


def test_rule_access_merges_duplicate_path(tmp_path):
    read_bits = _ACCESS_FS["read_file"] | _ACCESS_FS["read_dir"]
    write_bits = _ACCESS_FS["write_file"]

    access = _rule_access([str(tmp_path)], [str(tmp_path)], read_bits, write_bits)

    assert len(access) == 1
    assert access[os.path.realpath(str(tmp_path))] == read_bits | write_bits


# ---------------- 启动器行为 ----------------

def test_probe_succeeds():
    r = subprocess.run([sys.executable, _LAUNCHER, "--probe"], capture_output=True)
    assert r.returncode == 0


def test_write_inside_allowed(home_sandbox):
    _, ws, _ = home_sandbox
    r = _run(
        "workspace-write", f"echo hi > {ws}/a.txt && echo OK",
        read_roots=_read_roots(ws), write_roots=[ws],
    )
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
    assert os.path.exists(os.path.join(ws, "a.txt"))


def test_write_outside_denied(home_sandbox):
    _, ws, outside = home_sandbox
    r = _run(
        "workspace-write", f"echo hi > {outside}",
        read_roots=_read_roots(ws), write_roots=[ws],
    )
    assert r.returncode != 0
    assert not os.path.exists(outside)


def test_dev_null_allowed(home_sandbox):
    _, ws, _ = home_sandbox
    r = _run(
        "workspace-write", "echo hi > /dev/null && echo OK",
        read_roots=_read_roots(ws), write_roots=[ws],
    )
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_read_only_blocks_workspace_write(home_sandbox):
    _, ws, _ = home_sandbox
    r = _run(
        "read-only", f"echo hi > {ws}/ro.txt",
        read_roots=_read_roots(ws),
    )
    assert r.returncode != 0
    assert not os.path.exists(os.path.join(ws, "ro.txt"))


def test_env_wrapper_cannot_bypass(home_sandbox):
    _, ws, outside = home_sandbox
    r = _run(
        "workspace-write", f"env bash -c 'echo x > {outside}'",
        read_roots=_read_roots(ws), write_roots=[ws],
    )
    assert r.returncode != 0
    assert not os.path.exists(outside)


# ---------------- 读围栏 ----------------

def test_read_inside_read_roots_allowed(home_sandbox):
    _, ws, _ = home_sandbox
    path = os.path.join(ws, "data.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("hello")
    r = _run(
        "workspace-write", f"cat {path}",
        read_roots=_read_roots(ws), write_roots=[ws],
    )
    assert r.returncode == 0, r.stderr
    assert "hello" in r.stdout


def test_explicit_file_read_root_allowed(home_sandbox):
    """显式批准的文件读根可用; 文件只授予 read_file 不会触发 EINVAL。"""
    _, ws, outside = home_sandbox
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("approved")
    r = _run(
        "workspace-write", f"cat {outside}",
        read_roots=_read_roots(ws) + [outside], write_roots=[ws],
    )
    assert r.returncode == 0, r.stderr
    assert "approved" in r.stdout


def test_read_outside_read_roots_denied(home_sandbox):
    _, ws, outside = home_sandbox
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("secret")
    r = _run(
        "workspace-write", f"cat {outside}",
        read_roots=_read_roots(ws), write_roots=[ws],
    )
    assert r.returncode != 0
    assert "secret" not in r.stdout


def test_python_runs_under_read_fence(home_sandbox):
    """系统读根必须覆盖解释器前缀, 否则 stdlib 读不到。"""
    _, ws, _ = home_sandbox
    r = _run(
        "workspace-write", f"{sys.executable} -c 'print(123)'",
        read_roots=_read_roots(ws), write_roots=[ws],
    )
    assert r.returncode == 0, r.stderr
    assert "123" in r.stdout


def test_python_from_path_runs_under_read_fence(home_sandbox):
    if shutil.which("python") is None:
        pytest.skip("python not on PATH")
    _, ws, _ = home_sandbox
    r = _run(
        "workspace-write", "python -c pass",
        read_roots=_read_roots(ws), write_roots=[ws],
    )
    assert r.returncode == 0, r.stderr


def test_rg_version_runs_under_read_fence(home_sandbox):
    rg = shutil.which("rg")
    if rg is None:
        pytest.skip("rg not installed")
    _, ws, _ = home_sandbox
    r = _run(
        "workspace-write", f"{rg} --version",
        read_roots=_read_roots(ws), write_roots=[ws],
    )
    assert r.returncode == 0, r.stderr
    assert "ripgrep" in r.stdout


def test_common_commands_start_under_converged_read_roots(home_sandbox):
    """收敛后的系统读根仍能启动常用命令。"""
    _, ws, _ = home_sandbox
    data = os.path.join(ws, "data.txt")
    with open(data, "w", encoding="utf-8") as fh:
        fh.write("hello")
    subprocess.run(["git", "init", "-q", ws], check=True)

    checks = [
        (f"{sys.executable} -c pass", None),
        (f"{sys.executable} -m pytest --version", "pytest"),
        (f"git -C {ws} status --short", None),
        ("sh -c true", None),
        (f"ls {ws}", "data.txt"),
        (f"cat {data}", "hello"),
    ]
    rg = shutil.which("rg")
    if rg is not None:
        checks.append((f"{rg} --version", "ripgrep"))

    for command, expected in checks:
        result = _run(
            "workspace-write", command,
            read_roots=_read_roots(ws), write_roots=[ws],
        )
        assert result.returncode == 0, (command, result.stderr)
        if expected is not None:
            assert expected in result.stdout, (command, result.stdout)


def test_bad_arguments_fail_closed():
    r = subprocess.run(
        [sys.executable, _LAUNCHER, "--bogus"], capture_output=True, text=True
    )
    assert r.returncode == LAUNCHER_ERROR_EXIT
