"""沙箱策略(可读根 / 可写根)的单元测试。"""
import os

import pytest

from agents.fs import (
    SandboxMode,
    SandboxPolicy,
    build_policy,
    readable_roots,
    resolve_path,
    writable_roots,
)


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


def _policy(mode, workspace, extra_writable=(), extra_readable=()):
    """以工作区根构造策略, 便于显式挂载额外读/写根。"""
    root = resolve_path(str(workspace), str(workspace))
    return SandboxPolicy(
        mode=mode,
        workspace_root=root,
        extra_writable=tuple(extra_writable),
        extra_readable=tuple(extra_readable),
    )


# ---------------- 字段默认值 ----------------

def test_extra_readable_defaults_to_empty(workspace):
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(workspace))
    assert policy.extra_readable == ()


# ---------------- readable_roots ----------------

def test_readable_roots_starts_with_workspace(workspace):
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(workspace))
    roots = readable_roots(policy)
    assert roots[0].target_key == os.path.realpath(str(workspace))


def test_readable_roots_includes_extra_readable(workspace):
    approved = workspace / "approved-read"
    approved.mkdir()
    policy = _policy(
        SandboxMode.WORKSPACE_WRITE, workspace,
        extra_readable=[resolve_path(str(approved), str(workspace))],
    )
    keys = {root.target_key for root in readable_roots(policy)}
    assert os.path.realpath(str(approved)) in keys


def test_readable_roots_dedups_by_identity(workspace):
    alias = resolve_path(str(workspace) + "/.", str(workspace))
    policy = _policy(
        SandboxMode.WORKSPACE_WRITE, workspace,
        extra_readable=[alias],
    )
    roots = readable_roots(policy)
    keys = [root.target_key for root in roots]
    assert len(keys) == len(set(keys)) == 1


def test_readable_roots_independent_of_mode(workspace):
    for mode in (SandboxMode.READ_ONLY, SandboxMode.WORKSPACE_WRITE,
                 SandboxMode.DANGER_FULL_ACCESS):
        policy = _policy(mode, workspace)
        assert [root.target_key for root in readable_roots(policy)] == [
            os.path.realpath(str(workspace))
        ]


# ---------------- writable_roots(回归) ----------------

def test_writable_roots_only_for_workspace_write(workspace):
    policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace)
    keys = {root.target_key for root in writable_roots(policy)}
    assert keys == {os.path.realpath(str(workspace))}

    for mode in (SandboxMode.READ_ONLY, SandboxMode.DANGER_FULL_ACCESS):
        assert writable_roots(_policy(mode, workspace)) == []


def test_writable_roots_includes_extra_writable(workspace):
    extra = workspace / "extra-write"
    extra.mkdir()
    policy = _policy(
        SandboxMode.WORKSPACE_WRITE, workspace,
        extra_writable=[resolve_path(str(extra), str(workspace))],
    )
    keys = {root.target_key for root in writable_roots(policy)}
    assert os.path.realpath(str(extra)) in keys
