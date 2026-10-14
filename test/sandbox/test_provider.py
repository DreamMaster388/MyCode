"""SandboxProvider / write_roots 的选择与包装逻辑测试。"""
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


def test_danger_full_access_passthrough(tmp_path):
    policy = build_policy(SandboxMode.DANGER_FULL_ACCESS, str(tmp_path))
    confined = SandboxProvider(policy).confine("echo hi")
    assert confined.backend == "none"
    assert confined.argv == ("bash", "-c", "echo hi")
    assert confined.denial_signatures == ()


def test_no_backend_is_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(BwrapBackend, "available", lambda self: False)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: False)
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))
    with pytest.raises(SandboxUnavailableError):
        SandboxProvider(policy).confine("echo hi")


def test_auto_falls_back_to_landlock(monkeypatch, tmp_path):
    monkeypatch.setattr(BwrapBackend, "available", lambda self: False)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: True)
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))
    confined = SandboxProvider(policy).confine("echo hi")
    assert confined.backend == "landlock"
    assert confined.argv[-3:] == ("bash", "-c", "echo hi")
    assert "--mode" in confined.argv
    assert "workspace-write" in confined.argv


def test_forced_bwrap_only(monkeypatch, tmp_path):
    monkeypatch.setattr(BwrapBackend, "available", lambda self: False)
    monkeypatch.setattr(LandlockBackend, "available", lambda self: True)
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, str(tmp_path))
    with pytest.raises(SandboxUnavailableError):
        SandboxProvider(policy, backend="bwrap").confine("echo hi")
