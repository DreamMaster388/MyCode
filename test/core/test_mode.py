"""模式与沙箱策略的单元测试。

命令审计与入口工具审查已随重构移除, 这里只保留仍可验证的行为:
模式取值、默认策略构造、显式策略保持, 以及模式切换时的策略重建。
"""
import os

import pytest

from agents.core.mode import AgentMode, ModeGuard
from agents.fs import (
    AccessDecision,
    SandboxMode,
    SandboxPolicy,
    build_policy,
    evaluate_write,
    readable_roots,
    resolve_path,
    writable_roots,
)


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


@pytest.fixture
def write_policy(workspace):
    return build_policy(SandboxMode.WORKSPACE_WRITE, str(workspace))


# ---------------- AgentMode ----------------

def test_agent_mode_values():
    assert AgentMode.BUILD == "build"
    assert AgentMode.PLAN == "plan"
    assert AgentMode.TRUSTED == "trusted"
    assert AgentMode("build") is AgentMode.BUILD
    assert AgentMode("plan") is AgentMode.PLAN
    assert AgentMode("trusted") is AgentMode.TRUSTED


# ---------------- 构造与默认策略 ----------------

def test_default_mode_is_build():
    assert ModeGuard().mode is AgentMode.BUILD


def test_default_build_policy_is_workspace_write():
    assert ModeGuard().policy.mode is SandboxMode.WORKSPACE_WRITE


def test_plan_mode_builds_read_only_policy(workspace):
    guard = ModeGuard(AgentMode.PLAN, project_root=str(workspace))
    assert guard.policy.mode is SandboxMode.READ_ONLY
    assert guard.policy.workspace_root.display_path == os.path.normpath(str(workspace))


def test_explicit_policy_is_used(write_policy):
    guard = ModeGuard(AgentMode.PLAN, policy=write_policy)
    assert guard.policy is write_policy


# ---------------- set_mode ----------------

def test_set_mode_rebuilds_default_policy(workspace):
    guard = ModeGuard(AgentMode.BUILD, project_root=str(workspace))
    assert guard.policy.mode is SandboxMode.WORKSPACE_WRITE

    guard.set_mode(AgentMode.PLAN)
    assert guard.mode is AgentMode.PLAN
    assert guard.policy.mode is SandboxMode.READ_ONLY

    guard.set_mode(AgentMode.BUILD)
    assert guard.policy.mode is SandboxMode.WORKSPACE_WRITE


def test_set_mode_preserves_explicit_policy(write_policy):
    guard = ModeGuard(AgentMode.BUILD, policy=write_policy)
    guard.set_mode(AgentMode.PLAN)
    assert guard.mode is AgentMode.PLAN
    assert guard.policy is write_policy


# ---------------- 用户批准目录 ----------------

def test_default_approved_roots_are_empty(workspace):
    guard = ModeGuard(AgentMode.BUILD, project_root=str(workspace))
    assert guard.approved_read_roots == set()
    assert guard.approved_write_roots == set()


def test_add_read_root_dedups_by_identity(workspace):
    guard = ModeGuard(AgentMode.BUILD, project_root=str(workspace))
    target = workspace / "read-dir"
    target.mkdir()

    first = guard.add_read_root(str(target))
    second = guard.add_read_root(str(target) + "/.")

    assert first.target_key == os.path.realpath(str(target))
    assert second.target_key == first.target_key
    assert len(guard.approved_read_roots) == 1


def test_remove_read_root_by_equivalent_path(workspace):
    guard = ModeGuard(AgentMode.BUILD, project_root=str(workspace))
    target = workspace / "read-dir"
    target.mkdir()
    guard.add_read_root(str(target))

    assert guard.remove_read_root(str(target) + "/.") is True
    assert guard.approved_read_roots == set()
    assert guard.remove_read_root(str(target)) is False


def test_add_and_remove_write_root(workspace):
    guard = ModeGuard(AgentMode.BUILD, project_root=str(workspace))
    target = workspace / "write-dir"
    target.mkdir()

    added = guard.add_write_root(str(target))
    guard.add_write_root(str(target))
    assert added.target_key == os.path.realpath(str(target))
    assert len(guard.approved_write_roots) == 1

    assert guard.remove_write_root(str(target)) is True
    assert guard.approved_write_roots == set()
    assert guard.remove_write_root(str(target)) is False


# ---------------- sandbox_policy: 可读/可写根 ----------------

def test_sandbox_policy_build_merges_approved_roots(workspace):
    guard = ModeGuard(AgentMode.BUILD, project_root=str(workspace))
    read_dir = workspace / "read-dir"
    write_dir = workspace / "write-dir"
    read_dir.mkdir()
    write_dir.mkdir()
    guard.add_read_root(str(read_dir))
    guard.add_write_root(str(write_dir))

    policy = guard.sandbox_policy()

    assert policy.mode is SandboxMode.WORKSPACE_WRITE
    assert policy.workspace_root.display_path == os.path.normpath(str(workspace))
    read_keys = {root.target_key for root in readable_roots(policy)}
    write_keys = {root.target_key for root in writable_roots(policy)}
    assert os.path.realpath(str(read_dir)) in read_keys
    assert os.path.realpath(str(write_dir)) in write_keys
    assert os.path.realpath(str(workspace)) in write_keys


def test_sandbox_policy_plan_has_no_writable_roots(workspace):
    guard = ModeGuard(AgentMode.PLAN, project_root=str(workspace))
    approved = workspace / "approved"
    approved.mkdir()
    guard.add_read_root(str(approved))
    guard.add_write_root(str(approved))

    policy = guard.sandbox_policy()

    assert policy.mode is SandboxMode.READ_ONLY
    assert policy.extra_writable == ()
    assert writable_roots(policy) == []
    read_keys = {root.target_key for root in readable_roots(policy)}
    assert os.path.realpath(str(approved)) in read_keys


def test_sandbox_policy_keeps_base_extra_roots(workspace):
    base_read = workspace / "base-read"
    base_write = workspace / "base-write"
    base_read.mkdir()
    base_write.mkdir()
    base = SandboxPolicy(
        mode=SandboxMode.WORKSPACE_WRITE,
        workspace_root=resolve_path(str(workspace), str(workspace)),
        extra_writable=(resolve_path(str(base_write), str(workspace)),),
        extra_readable=(resolve_path(str(base_read), str(workspace)),),
    )
    guard = ModeGuard(AgentMode.BUILD, policy=base)
    approved_read = workspace / "approved-read"
    approved_read.mkdir()
    guard.add_read_root(str(approved_read))

    policy = guard.sandbox_policy()

    read_keys = {root.target_key for root in readable_roots(policy)}
    assert {
        os.path.realpath(str(base_read)),
        os.path.realpath(str(approved_read)),
        os.path.realpath(str(workspace)),
    } <= read_keys
    write_keys = {root.target_key for root in writable_roots(policy)}
    assert os.path.realpath(str(base_write)) in write_keys


def test_approved_roots_survive_mode_switch(workspace):
    guard = ModeGuard(AgentMode.BUILD, project_root=str(workspace))
    shared = workspace / "shared"
    shared.mkdir()
    guard.add_read_root(str(shared))
    guard.add_write_root(str(shared))

    guard.set_mode(AgentMode.PLAN)
    plan_policy = guard.sandbox_policy()
    assert os.path.realpath(str(shared)) in {
        root.target_key for root in readable_roots(plan_policy)
    }
    assert writable_roots(plan_policy) == []

    guard.set_mode(AgentMode.BUILD)
    build_policy_now = guard.sandbox_policy()
    assert os.path.realpath(str(shared)) in {
        root.target_key for root in writable_roots(build_policy_now)
    }


def test_sandbox_policy_preserves_danger_full_access_mode(workspace):
    danger = build_policy(SandboxMode.DANGER_FULL_ACCESS, str(workspace))
    guard = ModeGuard(AgentMode.BUILD, policy=danger)

    policy = guard.sandbox_policy()

    assert policy.mode is SandboxMode.DANGER_FULL_ACCESS
    assert writable_roots(policy) == []


# ---------------- trusted ----------------

def test_trusted_default_policy_is_workspace_write_read_all(workspace):
    guard = ModeGuard(AgentMode.TRUSTED, project_root=str(workspace))

    assert guard.policy.mode is SandboxMode.WORKSPACE_WRITE
    assert guard.policy.read_all is True

    policy = guard.sandbox_policy()
    assert policy.mode is SandboxMode.WORKSPACE_WRITE
    assert policy.read_all is True
    assert [root.target_key for root in readable_roots(policy)] == ["/"]
    assert os.path.realpath(str(workspace)) in {
        root.target_key for root in writable_roots(policy)
    }


def test_trusted_does_not_widen_explicit_danger_policy(workspace):
    outside = workspace.parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    danger = build_policy(SandboxMode.DANGER_FULL_ACCESS, str(workspace))
    guard = ModeGuard(AgentMode.TRUSTED, policy=danger)

    policy = guard.sandbox_policy()

    assert policy.mode is SandboxMode.WORKSPACE_WRITE
    assert policy.read_all is True
    # 写越界仍需审批, 不能被 danger 策略直接 ALLOW。
    assert evaluate_write(
        policy, resolve_path(str(outside), str(workspace))
    ) is AccessDecision.ASK


def test_trusted_keeps_approved_write_roots(workspace):
    guard = ModeGuard(AgentMode.TRUSTED, project_root=str(workspace))
    approved_read = workspace / "approved-read"
    approved_write = workspace / "approved-write"
    approved_read.mkdir()
    approved_write.mkdir()
    guard.add_read_root(str(approved_read))
    guard.add_write_root(str(approved_write))

    policy = guard.sandbox_policy()

    assert os.path.realpath(str(approved_write)) in {
        root.target_key for root in writable_roots(policy)
    }
    assert [root.target_key for root in readable_roots(policy)] == ["/"]


def test_set_mode_to_trusted_rebuilds_and_back(workspace):
    guard = ModeGuard(AgentMode.BUILD, project_root=str(workspace))
    assert guard.sandbox_policy().read_all is False

    guard.set_mode(AgentMode.TRUSTED)
    assert guard.mode is AgentMode.TRUSTED
    assert guard.sandbox_policy().read_all is True

    guard.set_mode(AgentMode.BUILD)
    assert guard.sandbox_policy().read_all is False


def test_mode_string_is_normalized(workspace):
    guard = ModeGuard("trusted", project_root=str(workspace))
    assert guard.mode is AgentMode.TRUSTED
    assert guard.policy.read_all is True

    guard.set_mode("build")
    assert guard.mode is AgentMode.BUILD
