"""SandboxProvider / write_roots 的后端选择与任意 argv 包装测试。"""
import os

import pytest

from agents.fs import SandboxMode, build_policy
from agents.sandbox import (
    BwrapBackend,
    LandlockBackend,
    SandboxProvider,
    SandboxUnavailableError,
    write_roots,
)


def test_write_roots_workspace_write_includes_ws_and_tmp(tmp_path):
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))
    roots = write_roots(policy)
    assert os.path.realpath(str(tmp_path)) in roots
    assert os.path.realpath("/tmp") in roots


def test_write_roots_read_only_is_empty(tmp_path):
    policy = build_policy(SandboxMode.READ_ONLY, str(tmp_path))
    assert write_roots(policy) == []


def test_write_roots_danger_is_empty(tmp_path):
    policy = build_policy(SandboxMode.DANGER_FULL_ACCESS, str(tmp_path))
    assert write_roots(policy) == []


# ---------------- wrap: 通用 argv ----------------

def test_danger_full_access_passthrough(tmp_path):
    policy = build_policy(SandboxMode.DANGER_FULL_ACCESS, str(tmp_path))
    confined = SandboxProvider(policy).wrap(["rg", "--version"])
    assert confined.backend == "none"
    assert confined.argv == ("rg", "--version")
    assert confined.denial_signatures == ()


def test_no_backend_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(BwrapBackend, "available", lambda self: False)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: False)
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))
    with pytest.raises(SandboxUnavailableError):
        SandboxProvider(policy).wrap(["rg", "--version"])


def test_prefers_landlock_when_both_available(monkeypatch, tmp_path):
    monkeypatch.setattr(BwrapBackend, "available", lambda self: True)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: True)
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))

    confined = SandboxProvider(policy).wrap(["rg", "--version"])

    assert confined.backend == "landlock"
    assert "--mode" in confined.argv
    assert "workspace-write" in confined.argv
    assert confined.argv[-2:] == ("rg", "--version")


def test_falls_back_to_bwrap_without_read_roots(monkeypatch, tmp_path):
    """landlock 不可用时降级 bwrap: 能包装 argv, 但没有读限制。"""
    monkeypatch.setattr(BwrapBackend, "available", lambda self: True)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: False)
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))

    confined = SandboxProvider(policy).wrap(["rg", "--version"])

    assert confined.backend == "bwrap"
    assert confined.argv[0] == "bwrap"
    assert confined.argv[-2:] == ("rg", "--version")
    assert "--read-root" not in confined.argv


def test_landlock_wrap_passes_read_and_write_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(BwrapBackend, "available", lambda self: False)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: True)
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))

    argv = SandboxProvider(policy).wrap(["rg", "--version"]).argv
    read_values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--read-root"]
    write_values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--write-root"]

    assert os.path.realpath(str(tmp_path)) in read_values
    assert os.path.realpath("/usr") in read_values
    assert os.path.realpath(str(tmp_path)) in write_values
    assert argv[-2:] == ("rg", "--version")


def test_landlock_read_only_passes_no_write_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(BwrapBackend, "available", lambda self: False)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: True)
    policy = build_policy(SandboxMode.READ_ONLY, str(tmp_path))

    argv = SandboxProvider(policy).wrap(["rg", "--version"]).argv
    read_values = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--read-root"]

    assert "--write-root" not in argv
    assert os.path.realpath(str(tmp_path)) in read_values


def test_wrap_uses_explicit_policy_argument(monkeypatch, tmp_path):
    monkeypatch.setattr(BwrapBackend, "available", lambda self: False)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: True)
    read_only = build_policy(SandboxMode.READ_ONLY, str(tmp_path))
    writable = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))

    argv = SandboxProvider(read_only).wrap(["rg", "--version"], writable).argv

    assert "--write-root" in argv


def test_wrap_rejects_empty_argv(tmp_path):
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))
    with pytest.raises(ValueError):
        SandboxProvider(policy).wrap([])


def test_forced_bwrap_only(monkeypatch, tmp_path):
    monkeypatch.setattr(BwrapBackend, "available", lambda self: False)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: True)
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))
    with pytest.raises(SandboxUnavailableError):
        SandboxProvider(policy, backend="bwrap").wrap(["rg", "--version"])
