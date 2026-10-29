"""模式与沙箱策略的单元测试。

命令审计与入口工具审查已随重构移除, 这里只保留仍可验证的行为:
模式取值、默认策略构造、显式策略保持, 以及模式切换时的策略重建。
"""
import os

import pytest

from agents.core.mode import AgentMode, ModeGuard
from agents.fs import SandboxMode, build_policy


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
    assert AgentMode("build") is AgentMode.BUILD
    assert AgentMode("plan") is AgentMode.PLAN


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
