"""专用工具读鉴权的单元测试。

覆盖 Step 6: Read/Grep/Glob 只能读取可读根(项目目录 + 用户批准目录),
根外访问在审批接入前一律返回 ACCESS_DENIED; 写工具行为保持不回归。
"""
import os
import shutil

import pytest

from agents.fs import (
    SandboxMode,
    SandboxPolicy,
    build_policy,
    resolve_path,
)
from agents.tools.builtin import GlobTool, GrepTool, ReadTool, WriteTool
from agents.tools.response import ToolStatus

_HAS_RG = shutil.which("rg") is not None
_DENIAL_MARKER = "[sandbox: file access denied]"


@pytest.fixture
def sandbox(tmp_path):
    """返回 (workspace, outside_dir); outside_dir 不在可读根内。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    return ws, outside


def _policy(ws):
    return build_policy(SandboxMode.WORKSPACE_WRITE, str(ws))


def _policy_with_read(ws, approved):
    root = resolve_path(str(ws), str(ws))
    return SandboxPolicy(
        mode=SandboxMode.WORKSPACE_WRITE,
        workspace_root=root,
        extra_readable=(resolve_path(str(approved), str(ws)),),
    )


# ---------------- Read ----------------

def test_read_inside_workspace_allowed(sandbox):
    ws, _ = sandbox
    (ws / "a.txt").write_text("hello", encoding="utf-8")

    resp = ReadTool(project_root=str(ws), policy=_policy(ws)).run({"path": "a.txt"})

    assert resp.status is ToolStatus.SUCCESS
    assert "hello" in resp.text


def test_read_approved_root_allowed(sandbox):
    ws, outside = sandbox
    policy = _policy_with_read(ws, outside)

    resp = ReadTool(project_root=str(ws), policy=policy).run(
        {"path": str(outside / "secret.txt")}
    )

    assert resp.status is ToolStatus.SUCCESS
    assert "secret" in resp.text


def test_read_approved_directory_lists_entries(sandbox):
    ws, outside = sandbox
    policy = _policy_with_read(ws, outside)

    resp = ReadTool(project_root=str(ws), policy=policy).run({"path": str(outside)})

    assert resp.status is ToolStatus.SUCCESS
    assert "secret.txt" in resp.text


def test_read_outside_denied_with_marker(sandbox):
    ws, outside = sandbox

    resp = ReadTool(project_root=str(ws), policy=_policy(ws)).run(
        {"path": str(outside / "secret.txt")}
    )

    assert resp.status is ToolStatus.ERROR
    assert resp.error_info["code"] == "ACCESS_DENIED"
    assert _DENIAL_MARKER in resp.text


@pytest.mark.skipif(os.name == "nt", reason="symlink test is POSIX-only")
def test_read_symlink_escape_denied(sandbox):
    ws, outside = sandbox
    link = ws / "link.txt"
    link.symlink_to(outside / "secret.txt")

    resp = ReadTool(project_root=str(ws), policy=_policy(ws)).run({"path": "link.txt"})

    assert resp.status is ToolStatus.ERROR
    assert _DENIAL_MARKER in resp.text


def test_read_policy_provider_is_used_per_call(sandbox):
    ws, outside = sandbox
    holder = {"policy": _policy(ws)}

    def provider():
        return holder["policy"]

    tool = ReadTool(
        project_root=str(ws), policy=holder["policy"], policy_provider=provider
    )
    path = str(outside / "secret.txt")

    assert tool.run({"path": path}).status is ToolStatus.ERROR

    holder["policy"] = _policy_with_read(ws, outside)
    assert tool.run({"path": path}).status is ToolStatus.SUCCESS


# ---------------- Grep / Glob ----------------

@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_grep_default_path_is_workspace(sandbox):
    ws, _ = sandbox
    (ws / "a.txt").write_text("needle", encoding="utf-8")

    resp = GrepTool(project_root=str(ws), policy=_policy(ws)).run(
        {"pattern": "needle"}
    )

    assert resp.status is ToolStatus.SUCCESS, resp.text
    assert "needle" in resp.text


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_glob_default_path_is_workspace(sandbox):
    ws, _ = sandbox
    (ws / "a.py").write_text("print(1)", encoding="utf-8")

    resp = GlobTool(project_root=str(ws), policy=_policy(ws)).run(
        {"pattern": "*.py"}
    )

    assert resp.status is ToolStatus.SUCCESS, resp.text
    assert "a.py" in resp.text


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_grep_outside_denied_before_rg(sandbox):
    ws, outside = sandbox

    resp = GrepTool(project_root=str(ws), policy=_policy(ws)).run(
        {"pattern": "secret", "path": str(outside)}
    )

    assert resp.status is ToolStatus.ERROR
    assert _DENIAL_MARKER in resp.text


@pytest.mark.skipif(not _HAS_RG, reason="rg not installed")
def test_glob_outside_denied_before_rg(sandbox):
    ws, outside = sandbox

    resp = GlobTool(project_root=str(ws), policy=_policy(ws)).run(
        {"pattern": "*", "path": str(outside)}
    )

    assert resp.status is ToolStatus.ERROR
    assert _DENIAL_MARKER in resp.text


# ---------------- 写工具回归 ----------------

def test_write_inside_and_outside_unchanged(sandbox):
    ws, outside = sandbox
    tool = WriteTool(project_root=str(ws), policy=_policy(ws))

    ok = tool.run({"path": "out.txt", "content": "hi"})
    assert ok.status is ToolStatus.SUCCESS
    assert (ws / "out.txt").read_text(encoding="utf-8") == "hi"

    denied = tool.run({"path": str(outside / "x.txt"), "content": "hi"})
    assert denied.status is ToolStatus.ERROR
    assert denied.error_info["code"] == "ACCESS_DENIED"
    assert not (outside / "x.txt").exists()


def test_write_existing_file_still_backs_up(sandbox):
    ws, _ = sandbox
    target = ws / "out.txt"
    target.write_text("old", encoding="utf-8")

    resp = WriteTool(project_root=str(ws), policy=_policy(ws)).run(
        {"path": "out.txt", "content": "new"}
    )

    assert resp.status is ToolStatus.SUCCESS
    assert target.read_text(encoding="utf-8") == "new"
    assert (ws / ".backups").is_dir()
