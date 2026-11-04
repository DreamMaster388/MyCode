"""沙箱策略(可读根 / 可写根)的单元测试。"""
import os

import pytest

from agents.fs import (
    AccessDecision,
    AccessKind,
    SandboxApprovalRequired,
    SandboxDenied,
    SandboxMode,
    SandboxPolicy,
    authorize_read,
    authorize_write,
    build_policy,
    evaluate_read,
    evaluate_write,
    readable_roots,
    resolve_path,
    writable_roots,
)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _policy(mode, workspace, extra_writable=(), extra_readable=(),
            read_all=False):
    """以工作区根构造策略, 便于显式挂载额外读/写根。"""
    root = resolve_path(str(workspace), str(workspace))
    return SandboxPolicy(
        mode=mode,
        workspace_root=root,
        extra_writable=tuple(extra_writable),
        extra_readable=tuple(extra_readable),
        read_all=read_all,
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


# ---------------- evaluate_read / authorize_read ----------------

def test_evaluate_read_allows_workspace(workspace):
    inside = workspace / "a.txt"
    inside.write_text("x", encoding="utf-8")
    policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace)

    assert evaluate_read(policy, resolve_path(str(inside), str(workspace))) is AccessDecision.ALLOW


def test_evaluate_read_allows_extra_readable(workspace):
    approved = workspace.parent / "approved-read"
    approved.mkdir()
    (approved / "a.txt").write_text("x", encoding="utf-8")
    policy = _policy(
        SandboxMode.WORKSPACE_WRITE, workspace,
        extra_readable=[resolve_path(str(approved), str(workspace))],
    )

    target = resolve_path(str(approved / "a.txt"), str(workspace))
    assert evaluate_read(policy, target) is AccessDecision.ALLOW


def test_evaluate_read_asks_outside(workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace)

    target = resolve_path(str(outside), str(workspace))
    assert evaluate_read(policy, target) is AccessDecision.ASK


def test_evaluate_read_danger_allows_outside(workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    policy = _policy(SandboxMode.DANGER_FULL_ACCESS, workspace)

    target = resolve_path(str(outside), str(workspace))
    assert evaluate_read(policy, target) is AccessDecision.ALLOW


def test_authorize_read_allows_inside(workspace):
    inside = workspace / "a.txt"
    inside.write_text("x", encoding="utf-8")
    policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace)

    assert authorize_read(policy, resolve_path(str(inside), str(workspace))) is None


def test_authorize_read_raises_approval_required_outside(workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace)
    target = resolve_path(str(outside), str(workspace))

    with pytest.raises(SandboxApprovalRequired) as excinfo:
        authorize_read(policy, target)

    assert excinfo.value.kind is AccessKind.READ
    assert excinfo.value.target == target
    assert excinfo.value.code == "FS_APPROVAL_REQUIRED"


# ---------------- evaluate_write / authorize_write ----------------

def test_evaluate_write_decisions(workspace):
    inside = workspace / "a.txt"
    outside = workspace.parent / "outside.txt"
    inside.write_text("x", encoding="utf-8")
    outside.write_text("x", encoding="utf-8")

    write_policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace)
    assert evaluate_write(
        write_policy, resolve_path(str(inside), str(workspace))
    ) is AccessDecision.ALLOW
    assert evaluate_write(
        write_policy, resolve_path(str(outside), str(workspace))
    ) is AccessDecision.ASK

    assert evaluate_write(
        _policy(SandboxMode.READ_ONLY, workspace),
        resolve_path(str(inside), str(workspace)),
    ) is AccessDecision.DENY

    assert evaluate_write(
        _policy(SandboxMode.DANGER_FULL_ACCESS, workspace),
        resolve_path(str(outside), str(workspace)),
    ) is AccessDecision.ALLOW


def test_authorize_write_outside_still_denied(workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace)

    with pytest.raises(SandboxDenied):
        authorize_write(policy, resolve_path(str(outside), str(workspace)))


# ---------------- read_all(trusted) ----------------

def test_read_all_defaults_to_false(workspace):
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(workspace))
    assert policy.read_all is False


def test_build_policy_sets_read_all(workspace):
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(workspace), read_all=True)
    assert policy.read_all is True


def test_readable_roots_read_all_returns_root(workspace):
    policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace, read_all=True)

    roots = readable_roots(policy)

    assert [root.target_key for root in roots] == ["/"]


def test_evaluate_read_read_all_allows_outside(workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace, read_all=True)

    target = resolve_path(str(outside), str(workspace))
    assert evaluate_read(policy, target) is AccessDecision.ALLOW


def test_authorize_read_read_all_allows_outside(workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    policy = _policy(SandboxMode.WORKSPACE_WRITE, workspace, read_all=True)

    assert authorize_read(policy, resolve_path(str(outside), str(workspace))) is None


def test_read_all_does_not_widen_writes(workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    target = resolve_path(str(outside), str(workspace))

    # workspace-write + read_all: 读全盘, 写根外仍需审批(不直接 ALLOW)。
    assert evaluate_write(
        _policy(SandboxMode.WORKSPACE_WRITE, workspace, read_all=True), target
    ) is AccessDecision.ASK
    # read-only + read_all: 读全盘, 写仍明确拒绝。
    assert evaluate_write(
        _policy(SandboxMode.READ_ONLY, workspace, read_all=True), target
    ) is AccessDecision.DENY
