"""审批请求与批准决策的单元测试。

覆盖 Step 7: 策略内不询问、ONCE/ALWAYS/DENY 语义、plan 写不可审批、
默认 handler fail-closed, 以及 ConsoleApprovalHandler 的输入解析。
"""
import io

import pytest

from agents.core.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ConsoleApprovalHandler,
    DenyAllHandler,
    approval_root,
)
from agents.core.mode import AgentMode, ModeGuard
from agents.fs import (
    AccessKind,
    SandboxMode,
    build_policy,
    readable_roots,
    resolve_path,
    writable_roots,
)


@pytest.fixture
def sandbox(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    return ws, outside


def _policy(ws, mode=SandboxMode.WORKSPACE_WRITE):
    return build_policy(mode, str(ws))


def _request(ws, path, kind=AccessKind.READ, tool_name="Read"):
    return ApprovalRequest(
        kind=kind,
        target=resolve_path(str(path), str(ws)),
        tool_name=tool_name,
    )


class RecordingHandler:
    """记录请求并返回预设决策的 fake handler。"""

    def __init__(self, decision=ApprovalDecision.DENY):
        self.decision = decision
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return self.decision


# ---------------- ModeGuard.request_access ----------------

def test_internal_read_does_not_consult_handler(sandbox):
    ws, _ = sandbox
    (ws / "a.txt").write_text("x", encoding="utf-8")
    handler = RecordingHandler(ApprovalDecision.ONCE)
    guard = ModeGuard(AgentMode.BUILD, policy=_policy(ws), approval_handler=handler)

    verdict = guard.request_access(_request(ws, ws / "a.txt"))

    assert verdict is ApprovalDecision.ALLOW
    assert handler.requests == []


def test_once_grants_but_does_not_change_roots(sandbox):
    ws, outside = sandbox
    handler = RecordingHandler(ApprovalDecision.ONCE)
    guard = ModeGuard(AgentMode.BUILD, policy=_policy(ws), approval_handler=handler)

    verdict = guard.request_access(_request(ws, outside / "secret.txt"))

    assert verdict is ApprovalDecision.ONCE
    assert len(handler.requests) == 1
    assert handler.requests[0].mode == "build"
    assert guard.approved_read_roots == set()


def test_always_adds_read_root_and_stops_asking(sandbox):
    ws, outside = sandbox
    handler = RecordingHandler(ApprovalDecision.ALWAYS)
    guard = ModeGuard(AgentMode.BUILD, policy=_policy(ws), approval_handler=handler)

    verdict = guard.request_access(_request(ws, outside / "secret.txt"))

    assert verdict is ApprovalDecision.ALWAYS
    assert {root.target_key for root in guard.approved_read_roots} == {
        str(outside.resolve())
    }
    assert {root.target_key for root in readable_roots(guard.sandbox_policy())} >= {
        str(outside.resolve())
    }

    # 同目录的兄弟文件应直接命中新批准目录, 不再询问。
    again = guard.request_access(_request(ws, outside / "secret.txt"))
    assert again is ApprovalDecision.ALLOW
    assert len(handler.requests) == 1


def test_always_adds_write_root(sandbox):
    ws, outside = sandbox
    handler = RecordingHandler(ApprovalDecision.ALWAYS)
    guard = ModeGuard(AgentMode.BUILD, policy=_policy(ws), approval_handler=handler)

    verdict = guard.request_access(
        _request(ws, outside / "x.txt", AccessKind.WRITE, "Write")
    )

    assert verdict is ApprovalDecision.ALWAYS
    assert {root.target_key for root in guard.approved_write_roots} == {
        str(outside.resolve())
    }
    assert {root.target_key for root in writable_roots(guard.sandbox_policy())} >= {
        str(outside.resolve())
    }


def test_deny_does_not_change_roots(sandbox):
    ws, outside = sandbox
    handler = RecordingHandler(ApprovalDecision.DENY)
    guard = ModeGuard(AgentMode.BUILD, policy=_policy(ws), approval_handler=handler)

    verdict = guard.request_access(_request(ws, outside / "secret.txt"))

    assert verdict is ApprovalDecision.DENY
    assert guard.approved_read_roots == set()
    assert guard.approved_write_roots == set()


def test_plan_write_never_consults_handler(sandbox):
    ws, _ = sandbox
    handler = RecordingHandler(ApprovalDecision.ALWAYS)
    guard = ModeGuard(
        AgentMode.PLAN,
        project_root=str(ws),
        policy=_policy(ws),
        approval_handler=handler,
    )

    verdict = guard.request_access(
        _request(ws, ws / "a.txt", AccessKind.WRITE, "Write")
    )

    assert verdict is ApprovalDecision.DENY
    assert handler.requests == []


def test_default_handler_is_fail_closed(sandbox):
    ws, outside = sandbox
    guard = ModeGuard(AgentMode.BUILD, policy=_policy(ws))

    assert isinstance(guard.approval_handler, DenyAllHandler)
    assert guard.request_access(_request(ws, outside / "secret.txt")) is ApprovalDecision.DENY


def test_handler_exception_fails_closed(sandbox):
    ws, outside = sandbox

    class BrokenHandler:
        def decide(self, request):
            raise RuntimeError("boom")

    guard = ModeGuard(
        AgentMode.BUILD, policy=_policy(ws), approval_handler=BrokenHandler()
    )

    assert guard.request_access(_request(ws, outside / "secret.txt")) is ApprovalDecision.DENY


def test_handler_returning_string_is_normalized(sandbox):
    ws, outside = sandbox

    class StringHandler:
        def decide(self, request):
            return "always"

    guard = ModeGuard(
        AgentMode.BUILD, policy=_policy(ws), approval_handler=StringHandler()
    )

    assert guard.request_access(_request(ws, outside / "secret.txt")) is ApprovalDecision.ALWAYS


# ---------------- approval_root ----------------

def test_approval_root_uses_parent_for_file(sandbox):
    ws, outside = sandbox
    target = resolve_path(str(outside / "secret.txt"), str(ws))

    assert approval_root(target).target_key == str(outside.resolve())


def test_approval_root_uses_directory_itself(sandbox):
    ws, outside = sandbox
    target = resolve_path(str(outside), str(ws))

    assert approval_root(target).target_key == str(outside.resolve())


# ---------------- ConsoleApprovalHandler ----------------

@pytest.mark.parametrize("answer,expected", [
    ("o\n", ApprovalDecision.ONCE),
    ("a\n", ApprovalDecision.ALWAYS),
    ("d\n", ApprovalDecision.DENY),
    ("\n", ApprovalDecision.DENY),
    ("x\n", ApprovalDecision.DENY),
    ("", ApprovalDecision.DENY),
])
def test_console_approval_choices(sandbox, answer, expected):
    ws, outside = sandbox
    output = io.StringIO()
    handler = ConsoleApprovalHandler(
        input_stream=io.StringIO(answer), output_stream=output
    )

    verdict = handler.decide(_request(ws, outside / "secret.txt"))

    assert verdict is expected


def test_console_prompt_shows_paths(sandbox):
    ws, outside = sandbox
    output = io.StringIO()
    handler = ConsoleApprovalHandler(
        input_stream=io.StringIO("d\n"), output_stream=output
    )

    handler.decide(_request(ws, outside / "secret.txt"))
    prompt = output.getvalue()

    assert str(outside.resolve()) in prompt
    assert "secret.txt" in prompt
    assert "[o]" in prompt and "[a]" in prompt and "[d]" in prompt
