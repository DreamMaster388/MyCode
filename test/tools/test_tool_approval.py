"""专用工具接入审批的单元测试。

覆盖 Step 8: 六个路径型工具在可读/可写根之外请求审批; ONCE 只对本次调用
有效(写操作含临时文件与 .backups), ALWAYS 扩展批准目录并对 bash 生效;
无 guard 时保持严格拒绝; bash 自身不审批。
"""
import shutil

import pytest

from agents.core.approval import ApprovalDecision
from agents.core.mode import AgentMode, ModeGuard
from agents.fs import SandboxMode, build_policy, writable_roots
from agents.sandbox import ConfinedCommand, SandboxProvider
from agents.tools.builtin import (
    BashTool,
    EditTool,
    GlobTool,
    GrepTool,
    ReadTool,
    WriteTool,
)
from agents.tools.response import ToolStatus

_HAS_RG = shutil.which("rg") is not None
_APPROVAL_MARKER = "[sandbox: approval denied]"
_FILE_MARKER = "[sandbox: file access denied]"


@pytest.fixture
def sandbox(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    return ws, outside


class ScriptedHandler:
    """按预设顺序返回决策并记录请求的 fake handler。"""

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        if not self.decisions:
            return ApprovalDecision.DENY
        return self.decisions.pop(0)


def _policy(ws):
    return build_policy(SandboxMode.WORKSPACE_WRITE, str(ws))


def _guard(ws, handler, mode=AgentMode.BUILD):
    return ModeGuard(mode, project_root=str(ws), policy=_policy(ws),
                     approval_handler=handler)


def _tool(cls, ws, guard):
    return cls(project_root=str(ws), policy=_policy(ws),
               policy_provider=guard.sandbox_policy, access_guard=guard)


# ---------------- Read ----------------

def test_read_once_grants_current_call_only(sandbox):
    ws, outside = sandbox
    handler = ScriptedHandler(ApprovalDecision.ONCE, ApprovalDecision.DENY)
    guard = _guard(ws, handler)
    tool = _tool(ReadTool, ws, guard)
    path = str(outside / "secret.txt")

    first = tool.run({"path": path})
    assert first.status is ToolStatus.SUCCESS
    assert "secret" in first.text
    assert guard.approved_read_roots == set()

    second = tool.run({"path": path})
    assert second.status is ToolStatus.ERROR
    assert _APPROVAL_MARKER in second.text


def test_read_always_adds_root_and_stops_asking(sandbox):
    ws, outside = sandbox
    handler = ScriptedHandler(ApprovalDecision.ALWAYS)
    guard = _guard(ws, handler)
    tool = _tool(ReadTool, ws, guard)
    path = str(outside / "secret.txt")

    assert tool.run({"path": path}).status is ToolStatus.SUCCESS
    assert {root.target_key for root in guard.approved_read_roots} == {
        str(outside.resolve())
    }

    assert tool.run({"path": path}).status is ToolStatus.SUCCESS
    assert len(handler.requests) == 1


def test_read_deny_returns_approval_denied(sandbox):
    ws, outside = sandbox
    handler = ScriptedHandler(ApprovalDecision.DENY)
    guard = _guard(ws, handler)
    tool = _tool(ReadTool, ws, guard)

    resp = tool.run({"path": str(outside / "secret.txt")})

    assert resp.status is ToolStatus.ERROR
    assert resp.error_info["code"] == "ACCESS_DENIED"
    assert _APPROVAL_MARKER in resp.text
    assert guard.approved_read_roots == set()


# ---------------- Write / Edit ----------------

def test_write_once_allows_temp_and_backup(sandbox):
    ws, outside = sandbox
    target = outside / "existing.txt"
    target.write_text("old", encoding="utf-8")
    handler = ScriptedHandler(ApprovalDecision.ONCE, ApprovalDecision.DENY)
    guard = _guard(ws, handler)
    tool = _tool(WriteTool, ws, guard)

    first = tool.run({"path": str(target), "content": "new"})
    assert first.status is ToolStatus.SUCCESS, first.text
    assert target.read_text(encoding="utf-8") == "new"
    assert (outside / ".backups").is_dir()
    assert guard.approved_write_roots == set()

    second = tool.run({"path": str(target), "content": "again"})
    assert second.status is ToolStatus.ERROR
    assert _APPROVAL_MARKER in second.text


def test_write_always_syncs_policy_and_bash(sandbox):
    ws, outside = sandbox
    target = outside / "existing.txt"
    target.write_text("old", encoding="utf-8")
    handler = ScriptedHandler(ApprovalDecision.ALWAYS)
    guard = _guard(ws, handler)
    tool = _tool(WriteTool, ws, guard)

    assert tool.run({"path": str(target), "content": "new"}).status is ToolStatus.SUCCESS
    assert {root.target_key for root in guard.approved_write_roots} == {
        str(outside.resolve())
    }
    assert {root.target_key for root in writable_roots(guard.sandbox_policy())} >= {
        str(outside.resolve())
    }

    # bash 与工具共用 guard.sandbox_policy(); 下一次调用自动带上新根。
    bash = BashTool(project_root=str(ws), policy=_policy(ws),
                    policy_provider=guard.sandbox_policy)
    assert {root.target_key for root in writable_roots(bash._current_policy())} >= {
        str(outside.resolve())
    }

    # 同一目录再次写入不再询问。
    assert tool.run({"path": str(target), "content": "again"}).status is ToolStatus.SUCCESS
    assert len(handler.requests) == 1


def test_write_once_backup_does_not_leak_permit(sandbox):
    """ONCE 的临时许可只属于本次调用, 不能带到下一次。"""
    ws, outside = sandbox
    target = outside / "existing.txt"
    target.write_text("old", encoding="utf-8")
    handler = ScriptedHandler(ApprovalDecision.ONCE)
    guard = _guard(ws, handler)
    tool = _tool(WriteTool, ws, guard)

    assert tool.run({"path": str(target), "content": "new"}).status is ToolStatus.SUCCESS
    # 调用结束后一次性许可已丢弃, 不能带到下一次。
    assert tool._permit is None

    # 再次调用会重新授权; handler 无决策时默认拒绝。
    resp = tool.run({"path": str(target), "content": "again"})
    assert resp.status is ToolStatus.ERROR
    assert guard.approved_write_roots == set()


def test_edit_once_allows_backup(sandbox):
    ws, outside = sandbox
    target = outside / "existing.txt"
    target.write_text("old", encoding="utf-8")
    handler = ScriptedHandler(ApprovalDecision.ONCE)
    guard = _guard(ws, handler)
    tool = _tool(EditTool, ws, guard)

    resp = tool.run({"path": str(target), "old_string": "old", "new_string": "new"})

    assert resp.status is ToolStatus.SUCCESS, resp.text
    assert target.read_text(encoding="utf-8") == "new"
    assert (outside / ".backups").is_dir()


# ---------------- Grep / Glob ----------------

@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_grep_uses_approval(sandbox):
    ws, outside = sandbox
    handler = ScriptedHandler(ApprovalDecision.ALWAYS)
    guard = _guard(ws, handler)
    tool = _tool(GrepTool, ws, guard)

    resp = tool.run({"pattern": "secret", "path": str(outside)})

    assert resp.status is ToolStatus.SUCCESS, resp.text
    assert "secret" in resp.text
    assert len(handler.requests) == 1


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_glob_uses_approval(sandbox):
    ws, outside = sandbox
    handler = ScriptedHandler(ApprovalDecision.ALWAYS)
    guard = _guard(ws, handler)
    tool = _tool(GlobTool, ws, guard)

    resp = tool.run({"pattern": "*.txt", "path": str(outside)})

    assert resp.status is ToolStatus.SUCCESS, resp.text
    assert "secret.txt" in resp.text


def test_bash_wrap_receives_approved_root(sandbox, monkeypatch):
    """bash 通过 policy_provider 拿到含批准根的同一策略, 且不调用审批。"""
    ws, outside = sandbox
    target = outside / "existing.txt"
    target.write_text("old", encoding="utf-8")
    handler = ScriptedHandler(ApprovalDecision.ALWAYS)
    guard = _guard(ws, handler)
    assert _tool(WriteTool, ws, guard).run(
        {"path": str(target), "content": "new"}
    ).status is ToolStatus.SUCCESS

    captured = {}

    def fake_wrap(self, argv, policy=None):
        captured["policy"] = policy or self.policy
        return ConfinedCommand(argv=("true",), backend="fake")

    monkeypatch.setattr(SandboxProvider, "wrap", fake_wrap)
    bash = BashTool(project_root=str(ws), policy=_policy(ws),
                    policy_provider=guard.sandbox_policy)

    resp = bash.run({"command": "true"})

    assert resp.status is ToolStatus.SUCCESS, resp.text
    assert {root.target_key for root in writable_roots(captured["policy"])} >= {
        str(outside.resolve())
    }
    assert len(handler.requests) == 1  # 只有 ALWAYS 那次, bash 不审批


# ---------------- plan / trusted / 无 guard / bash ----------------

def test_plan_write_denied_without_asking(sandbox):
    ws, outside = sandbox
    handler = ScriptedHandler(ApprovalDecision.ALWAYS)
    guard = _guard(ws, handler, mode=AgentMode.PLAN)
    tool = _tool(WriteTool, ws, guard)

    resp = tool.run({"path": str(outside / "x.txt"), "content": "x"})

    assert resp.status is ToolStatus.ERROR
    assert handler.requests == []
    assert not (outside / "x.txt").exists()


def test_trusted_read_outside_does_not_ask(sandbox):
    ws, outside = sandbox
    handler = ScriptedHandler(ApprovalDecision.DENY)
    guard = _guard(ws, handler, mode=AgentMode.TRUSTED)
    tool = _tool(ReadTool, ws, guard)

    resp = tool.run({"path": str(outside / "secret.txt")})

    assert resp.status is ToolStatus.SUCCESS
    assert "secret" in resp.text
    assert handler.requests == []


def test_trusted_write_outside_denied_without_asking(sandbox):
    ws, outside = sandbox
    handler = ScriptedHandler(ApprovalDecision.ALWAYS)
    guard = _guard(ws, handler, mode=AgentMode.TRUSTED)
    tool = _tool(WriteTool, ws, guard)
    target = outside / "x.txt"

    resp = tool.run({"path": str(target), "content": "x"})

    assert resp.status is ToolStatus.ERROR
    assert _APPROVAL_MARKER in resp.text
    assert handler.requests == []
    assert not target.exists()


def test_without_guard_outside_read_is_strict_denied(sandbox):
    ws, outside = sandbox
    tool = ReadTool(project_root=str(ws), policy=_policy(ws))

    resp = tool.run({"path": str(outside / "secret.txt")})

    assert resp.status is ToolStatus.ERROR
    assert _FILE_MARKER in resp.text


def test_bash_does_not_request_approval(sandbox):
    ws, _ = sandbox
    handler = ScriptedHandler(ApprovalDecision.ALWAYS)
    guard = _guard(ws, handler)

    calls = []
    original = guard.request_access

    def record(request):
        calls.append(request)
        return original(request)

    guard.request_access = record
    bash = BashTool(project_root=str(ws), policy=_policy(ws),
                    policy_provider=guard.sandbox_policy)

    bash.run({"command": "echo hi"})

    assert calls == []
    assert handler.requests == []
