"""通用 argv 包装的集成测试。

本步不把其它工具接入 sandbox, 只验证"任意 argv 都能被包装并受围栏约束":
用 cat/rg/sh 等命令分别对应 Read/Grep/Glob/Write 的能力。
"""
import os
import shutil
import subprocess

import pytest

from agents.sandbox import LandlockBackend, SandboxProvider

pytestmark = pytest.mark.skipif(
    not LandlockBackend().available(), reason="landlock unavailable"
)

_HAS_RG = shutil.which("rg") is not None


def _run(policy, argv, timeout=30):
    confined = SandboxProvider(policy).wrap(argv)
    env = dict(os.environ)
    env.update(confined.env_overrides)
    return subprocess.run(
        confined.argv, capture_output=True, text=True,
        timeout=timeout, env=env, close_fds=True,
    )


def test_wrap_cat_reads_workspace_file(home_sandbox):
    """Read 功能可通过包装 argv 实现: cat <file>。"""
    policy, ws, _ = home_sandbox
    path = os.path.join(ws, "data.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("hello")

    result = _run(policy, ["cat", path])

    assert result.returncode == 0, result.stderr
    assert "hello" in result.stdout


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_wrap_rg_searches_workspace(home_sandbox):
    """Grep 功能可通过包装 argv 实现: rg <pattern> <dir>。"""
    policy, ws, _ = home_sandbox
    with open(os.path.join(ws, "a.txt"), "w", encoding="utf-8") as fh:
        fh.write("needle here")

    result = _run(policy, [
        "rg", "--line-number", "--with-filename", "--regexp", "needle", "--", ws,
    ])

    assert result.returncode == 0, result.stderr
    assert "needle" in result.stdout


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_wrap_rg_lists_files(home_sandbox):
    """Glob 功能可通过包装 argv 实现: rg --files --glob <pattern>。"""
    policy, ws, _ = home_sandbox
    with open(os.path.join(ws, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("print(1)")

    result = _run(policy, ["rg", "--files", "--glob", "*.py", "--", ws])

    assert result.returncode == 0, result.stderr
    assert "a.py" in result.stdout


def test_wrap_sh_writes_workspace_file(home_sandbox):
    """Write/Edit 类能力可通过包装 argv 实现: sh -c '... > file'。"""
    policy, ws, _ = home_sandbox
    path = os.path.join(ws, "out.txt")

    result = _run(policy, ["sh", "-c", f"printf hi > {path}"])

    assert result.returncode == 0, result.stderr
    assert os.path.exists(path)


def test_wrap_read_outside_read_roots_denied(home_sandbox):
    """读根之外的 cat 被内核拒绝, 证明读围栏同样作用于任意 argv。"""
    policy, ws, outside = home_sandbox
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write("secret")

    result = _run(policy, ["cat", outside])

    assert result.returncode != 0
    assert "secret" not in result.stdout


def test_wrap_write_outside_write_roots_denied(home_sandbox):
    """写根之外的 sh 写操作被内核拒绝。"""
    policy, ws, outside = home_sandbox

    result = _run(policy, ["sh", "-c", f"printf hi > {outside}"])

    assert result.returncode != 0
    assert not os.path.exists(outside) or os.path.getsize(outside) == 0
