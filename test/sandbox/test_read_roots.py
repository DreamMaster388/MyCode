"""沙箱可读根(read_roots)的单元测试。"""
import os
import sys

from agents.fs import SandboxMode, SandboxPolicy, build_policy, resolve_path
from agents.sandbox.base import SYSTEM_READ_ROOTS, read_roots


def _policy(mode, workspace, extra_readable=()):
    return SandboxPolicy(
        mode=mode,
        workspace_root=resolve_path(str(workspace), str(workspace)),
        extra_readable=tuple(extra_readable),
    )


# ---------------- SYSTEM_READ_ROOTS ----------------

def test_system_read_roots_are_nonempty_absolute_paths():
    assert SYSTEM_READ_ROOTS
    assert all(os.path.isabs(root) for root in SYSTEM_READ_ROOTS)


def test_system_read_roots_include_interpreter_prefix():
    prefixes = {
        os.path.realpath(p) for p in (sys.prefix, getattr(sys, "base_prefix", "")) if p
    }
    assert {os.path.realpath(root) for root in SYSTEM_READ_ROOTS} & prefixes


# ---------------- read_roots ----------------

def test_read_roots_includes_interpreter_prefix(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    roots = read_roots(build_policy(SandboxMode.WORKSPACE_WRITE, str(ws)))

    assert os.path.realpath(sys.prefix) in roots


def test_read_roots_includes_system_and_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    roots = read_roots(build_policy(SandboxMode.WORKSPACE_WRITE, str(ws)))

    assert os.path.realpath(str(ws)) in roots
    existing_system = {
        os.path.realpath(root)
        for root in SYSTEM_READ_ROOTS
        if os.path.isdir(os.path.realpath(root))
    }
    assert existing_system <= set(roots)
    assert all(isinstance(root, str) for root in roots)
    assert len(roots) == len(set(roots))


def test_read_roots_includes_extra_readable(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    approved = tmp_path / "approved"
    approved.mkdir()
    policy = _policy(
        SandboxMode.WORKSPACE_WRITE, ws,
        [resolve_path(str(approved), str(ws))],
    )

    assert os.path.realpath(str(approved)) in read_roots(policy)


def test_read_roots_skips_missing_dirs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    missing = tmp_path / "missing"
    policy = _policy(
        SandboxMode.WORKSPACE_WRITE, ws,
        [resolve_path(str(missing), str(ws))],
    )

    assert os.path.realpath(str(missing)) not in read_roots(policy)


def test_read_roots_dedups_workspace_alias(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    alias = resolve_path(str(ws) + "/.", str(ws))
    policy = _policy(SandboxMode.WORKSPACE_WRITE, ws, [alias])

    roots = read_roots(policy)

    assert roots.count(os.path.realpath(str(ws))) == 1


def test_read_roots_same_for_read_only_and_workspace_write(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    assert read_roots(build_policy(SandboxMode.READ_ONLY, str(ws))) == read_roots(
        build_policy(SandboxMode.WORKSPACE_WRITE, str(ws))
    )


def test_read_roots_danger_full_access_returns_empty(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    assert read_roots(build_policy(SandboxMode.DANGER_FULL_ACCESS, str(ws))) == []


def test_read_roots_read_all_returns_root(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(ws), read_all=True)

    assert read_roots(policy) == ["/"]
