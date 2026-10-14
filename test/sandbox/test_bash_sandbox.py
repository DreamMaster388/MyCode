"""BashTool 经内核沙箱执行的集成测试。"""
import os

import pytest

from agents.core.mode import AgentMode, ModeGuard
from agents.fs import SandboxMode, build_policy
from agents.sandbox import BwrapBackend, LandlockBackend
from agents.tools.builtin.bash_tool import BashTool
from agents.tools.response import ToolStatus

pytestmark = pytest.mark.skipif(
    not LandlockBackend().available(), reason="landlock unavailable"
)


def _bash(home_sandbox, **kwargs):
    policy, ws, outside = home_sandbox
    return BashTool(policy=policy, **kwargs), policy, ws, outside


def test_write_inside_is_allowed(home_sandbox):
    tool, policy, ws, outside = _bash(home_sandbox)
    resp = tool.run({"command": f"echo hi > {ws}/a.txt && echo OK"})
    assert resp.status is ToolStatus.SUCCESS
    assert os.path.exists(os.path.join(ws, "a.txt"))


def test_write_outside_is_denied_with_marker(home_sandbox):
    tool, policy, ws, outside = _bash(home_sandbox)
    resp = tool.run({"command": f"echo hi > {outside}"})
    assert resp.status is ToolStatus.ERROR
    assert "sandbox: file access denied" in resp.text
    assert not os.path.exists(outside)


def test_env_wrapper_bypass_is_denied(home_sandbox):
    tool, policy, ws, outside = _bash(home_sandbox)
    resp = tool.run({"command": f"env bash -c 'echo x > {outside}'"})
    assert resp.status is ToolStatus.ERROR
    assert not os.path.exists(outside)


def test_interpreter_write_is_denied(home_sandbox):
    tool, policy, ws, outside = _bash(home_sandbox)
    resp = tool.run({"command": f"python3 -c \"open('{outside}','w').write('x')\""})
    assert resp.status is ToolStatus.ERROR
    assert not os.path.exists(outside)


def test_plan_mode_is_read_only(home_sandbox):
    policy, ws, outside = home_sandbox
    guard = ModeGuard(AgentMode.BUILD, policy=policy)
    tool = BashTool(policy=policy, policy_provider=guard.sandbox_policy)
    guard.set_mode(AgentMode.PLAN)
    resp = tool.run({"command": f"echo hi > {ws}/plan.txt"})
    assert resp.status is ToolStatus.ERROR
    assert not os.path.exists(os.path.join(ws, "plan.txt"))


def test_fail_closed_when_no_backend(monkeypatch, home_sandbox):
    policy, ws, outside = home_sandbox
    monkeypatch.setattr(BwrapBackend, "available", lambda self: False)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: False)
    tool = BashTool(policy=policy)
    resp = tool.run({"command": "echo hi"})
    assert resp.status is ToolStatus.ERROR
    assert "fail-closed" in resp.text


def test_danger_full_access_runs_unconfined(home_sandbox):
    policy, ws, outside = home_sandbox
    danger = build_policy(SandboxMode.DANGER_FULL_ACCESS, ws)
    tool = BashTool(policy=danger)
    resp = tool.run({"command": f"echo hi > {outside}"})
    assert resp.status is ToolStatus.SUCCESS
    assert os.path.exists(outside)
