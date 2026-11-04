"""`/allow` 批准目录命令的单元测试。"""
import pytest

from agents.cli.builtin import AllowCommand
from agents.cli.registry import CommandRegistry
from agents.core.mode import AgentMode, ModeGuard
from agents.fs import (
    SandboxMode,
    build_policy,
    readable_roots,
    writable_roots,
)
from agents.tools.builtin import BashTool


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    return ws, outside


def _guard(ws):
    return ModeGuard(AgentMode.BUILD, project_root=str(ws))


def _bash(ws, guard):
    return BashTool(
        project_root=str(ws),
        policy=build_policy(SandboxMode.WORKSPACE_WRITE, str(ws)),
        policy_provider=guard.sandbox_policy,
    )


def test_allow_read_adds_root_and_updates_policy(workspace, capsys):
    ws, outside = workspace
    guard = _guard(ws)

    AllowCommand().execute_command(f"/allow read {outside}", guard)

    out = capsys.readouterr().out
    assert "已批准可读目录" in out
    assert {root.target_key for root in guard.approved_read_roots} == {
        str(outside.resolve())
    }
    assert {root.target_key for root in readable_roots(guard.sandbox_policy())} >= {
        str(outside.resolve())
    }
    # bash 通过同一个 policy_provider 立即读到新根。
    assert {root.target_key for root in readable_roots(_bash(ws, guard)._current_policy())} >= {
        str(outside.resolve())
    }


def test_allow_write_relative_path_adds_root(workspace, capsys):
    ws, _ = workspace
    approved = ws / "data"
    approved.mkdir()
    guard = _guard(ws)

    AllowCommand().execute_command("/allow write data", guard)

    out = capsys.readouterr().out
    assert "已批准可写目录" in out
    assert {root.target_key for root in guard.approved_write_roots} == {
        str(approved.resolve())
    }
    assert {root.target_key for root in writable_roots(guard.sandbox_policy())} >= {
        str(approved.resolve())
    }
    assert {root.target_key for root in writable_roots(_bash(ws, guard)._current_policy())} >= {
        str(approved.resolve())
    }


def test_allow_lists_read_and_write_roots(workspace, capsys):
    ws, outside = workspace
    guard = _guard(ws)
    guard.add_read_root(str(outside))
    guard.add_write_root(str(outside))

    AllowCommand().execute_command("/allow", guard)

    out = capsys.readouterr().out
    assert "可读批准目录:" in out
    assert "可写批准目录:" in out
    assert str(outside) in out

    AllowCommand().execute_command("/allow list", guard)
    assert str(outside) in capsys.readouterr().out


def test_allow_remove_read_and_write(workspace, capsys):
    ws, outside = workspace
    guard = _guard(ws)
    guard.add_read_root(str(outside))
    guard.add_write_root(str(outside))
    cmd = AllowCommand()

    cmd.execute_command(f"/allow remove read {outside}", guard)
    assert guard.approved_read_roots == set()
    assert "已移除可读批准目录" in capsys.readouterr().out

    cmd.execute_command(f"/allow remove write {outside}", guard)
    assert guard.approved_write_roots == set()
    assert "已移除可写批准目录" in capsys.readouterr().out


def test_allow_add_is_deduped(workspace, capsys):
    ws, outside = workspace
    guard = _guard(ws)
    cmd = AllowCommand()

    cmd.execute_command(f"/allow read {outside}", guard)
    capsys.readouterr()
    cmd.execute_command(f"/allow read {outside}", guard)

    assert len(guard.approved_read_roots) == 1
    assert "已在可读批准列表中" in capsys.readouterr().out


def test_allow_rejects_missing_or_non_directory(workspace, capsys):
    ws, outside = workspace
    guard = _guard(ws)
    file_path = ws / "a.txt"
    file_path.write_text("x", encoding="utf-8")
    cmd = AllowCommand()

    cmd.execute_command("/allow read", guard)
    assert "用法:" in capsys.readouterr().out

    cmd.execute_command(f"/allow read {file_path}", guard)
    assert "目录不存在或不是目录" in capsys.readouterr().out

    cmd.execute_command(f"/allow read {ws / 'missing'}", guard)
    assert "目录不存在或不是目录" in capsys.readouterr().out

    cmd.execute_command("/allow nope", guard)
    assert "用法:" in capsys.readouterr().out

    cmd.execute_command("/allow remove write", guard)
    assert "用法:" in capsys.readouterr().out
    assert guard.approved_read_roots == set()
    assert guard.approved_write_roots == set()


def test_allow_remove_handles_bad_path(workspace, capsys):
    ws, _ = workspace
    guard = _guard(ws)
    parent_file = ws / "file"
    parent_file.write_text("x", encoding="utf-8")

    AllowCommand().execute_command(f"/allow remove read {parent_file / 'child'}", guard)

    assert "无法解析路径" in capsys.readouterr().out


def test_registry_preserves_path_case_and_accepts_upper_command(workspace, capsys):
    ws, _ = workspace
    target = ws / "DataWithCaps"
    target.mkdir()
    guard = _guard(ws)
    registry = CommandRegistry()
    registry.register(AllowCommand())

    assert registry.check_command(f"/ALLOW read {target}", guard) is True

    assert {root.target_key for root in guard.approved_read_roots} == {
        str(target.resolve())
    }
    assert "已批准可读目录" in capsys.readouterr().out
