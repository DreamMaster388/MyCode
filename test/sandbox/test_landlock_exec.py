"""Landlock 启动器(landlock_exec.py)的直接行为测试。"""
import os
import subprocess
import sys

import pytest

from agents.sandbox import LandlockBackend
from agents.sandbox.landlock_exec import LAUNCHER_ERROR_EXIT

pytestmark = pytest.mark.skipif(
    not LandlockBackend().available(), reason="landlock unavailable"
)

_LAUNCHER = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "agents", "sandbox", "landlock_exec.py")
)


def _run(mode, roots, command):
    argv = [sys.executable, _LAUNCHER, "--mode", mode]
    for root in roots:
        argv += ["--write-root", root]
    argv += ["--", "bash", "-c", command]
    return subprocess.run(argv, capture_output=True, text=True)


def test_probe_succeeds():
    r = subprocess.run([sys.executable, _LAUNCHER, "--probe"], capture_output=True)
    assert r.returncode == 0


def test_write_inside_allowed(home_sandbox):
    _, ws, _ = home_sandbox
    r = _run("workspace-write", [ws], f"echo hi > {ws}/a.txt && echo OK")
    assert r.returncode == 0
    assert "OK" in r.stdout
    assert os.path.exists(os.path.join(ws, "a.txt"))


def test_write_outside_denied(home_sandbox):
    _, ws, outside = home_sandbox
    r = _run("workspace-write", [ws], f"echo hi > {outside}")
    assert r.returncode != 0
    assert not os.path.exists(outside)


def test_dev_null_allowed(home_sandbox):
    _, ws, _ = home_sandbox
    r = _run("workspace-write", [ws], "echo hi > /dev/null && echo OK")
    assert r.returncode == 0
    assert "OK" in r.stdout


def test_read_only_blocks_workspace_write(home_sandbox):
    _, ws, _ = home_sandbox
    r = _run("read-only", [], f"echo hi > {ws}/ro.txt")
    assert r.returncode != 0
    assert not os.path.exists(os.path.join(ws, "ro.txt"))


def test_env_wrapper_cannot_bypass(home_sandbox):
    _, ws, outside = home_sandbox
    r = _run("workspace-write", [ws], f"env bash -c 'echo x > {outside}'")
    assert r.returncode != 0
    assert not os.path.exists(outside)


def test_bad_arguments_fail_closed():
    r = subprocess.run(
        [sys.executable, _LAUNCHER, "--bogus"], capture_output=True, text=True
    )
    assert r.returncode == LAUNCHER_ERROR_EXIT
