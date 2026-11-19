"""Ticket 05 验收：容器 eval 落盘与正式终端。

用假 sandbox/agent 验证每实例记录顶层 context_usage、早退零值形状与终端汇总；
不需要 Docker、网络或真实模型。
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from eval import container_runner, run_predictions
from eval.usage_summary import empty_context_usage


def _usage(categories=None, tools=None, summary_calls=0):
    usage = empty_context_usage()
    usage["categories"].update(categories or {})
    usage["tools"] = tools or {}
    usage["summary_calls"] = summary_calls
    return usage


class _FakeSandbox:
    def __init__(self, image_ok=True, **kwargs):
        self.image_ok = image_ok

    def ensure_image(self, pull_timeout=0):
        return SimpleNamespace(ok=self.image_ok, out="", err="no image")

    def start(self):
        return SimpleNamespace(ok=True, out="", err="")

    def model_patch(self, exclude_paths=None):
        return SimpleNamespace(ok=True, out="diff --git a/x b/x\n", err="")

    def stop(self):
        return None


class _FakeAgent:
    def __init__(self, usage):
        self._session_metadata = {
            "total_steps": 2,
            "total_tokens": 120,
            "input_tokens": 100,
            "output_tokens": 20,
            "cached_tokens": 0,
            "cache_hit_available": True,
            "duration_seconds": 1.0,
            "context_usage": usage,
        }
        self.diagnostics = {
            "tool_calls": {"Read": 1},
            "bash_calls": 0,
            "test_runs": 0,
            "unknown_tools": 0,
            "tool_errors": 0,
            "edited_files": set(),
        }

    def run(self, problem_statement):
        return "done"


def _instance():
    return {
        "instance_id": "demo__1",
        "image": "demo:latest",
        "problem_statement": "fix it",
        "test_patch": "",
        "test_files": [],
    }


def test_container_record_contains_context_usage(monkeypatch):
    usage = _usage({"user": 10, "assistant": 20}, {"Read": {"input_tokens": 5, "calls": 1}}, 2)
    monkeypatch.setattr(container_runner, "DockerSandbox", lambda **kwargs: _FakeSandbox())
    monkeypatch.setattr(container_runner, "build_agent", lambda sandbox, max_steps=50: _FakeAgent(usage))

    rec = container_runner.run_instance(_instance())

    assert rec["status"] == "ok"
    assert rec["context_usage"] == usage


def test_container_early_exit_keeps_zero_context_usage(monkeypatch):
    monkeypatch.setattr(
        container_runner, "DockerSandbox", lambda **kwargs: _FakeSandbox(image_ok=False)
    )

    rec = container_runner.run_instance(_instance())

    assert rec["status"] == "error"
    assert rec["context_usage"] == empty_context_usage()


def test_run_predictions_prints_and_writes_context_usage(monkeypatch, tmp_path, capsys):
    records = [
        {
            "instance_id": "a-1",
            "status": "ok",
            "model_patch": "diff --git a/x b/x",
            "patch_bytes": 17,
            "steps": 1,
            "tokens": 30,
            "input_tokens": 30,
            "output_tokens": 0,
            "cached_tokens": 0,
            "llm_requests": 1,
            "duration_seconds": 1.0,
            "total_cost": 0.0,
            "context_usage": _usage(
                {"user": 10, "assistant": 20},
                {"Read": {"input_tokens": 5, "calls": 1}},
                2,
            ),
        },
        {
            "instance_id": "b-2",
            "status": "ok",
            "model_patch": "diff --git a/y b/y",
            "patch_bytes": 17,
            "steps": 1,
            "tokens": 70,
            "input_tokens": 70,
            "output_tokens": 0,
            "cached_tokens": 0,
            "llm_requests": 1,
            "duration_seconds": 1.0,
            "total_cost": 0.0,
            "context_usage": _usage(
                {"user": 30, "assistant": 40},
                {"Read": {"input_tokens": 10, "calls": 2}},
                1,
            ),
        },
    ]
    instances = [{"instance_id": rec["instance_id"]} for rec in records]
    by_id = {rec["instance_id"]: rec for rec in records}
    monkeypatch.setattr(run_predictions, "load_instances", lambda *a, **k: instances)
    monkeypatch.setattr(
        run_predictions, "select_instances", lambda insts, ids, limit: list(insts)
    )
    monkeypatch.setattr(
        run_predictions, "run_instance", lambda inst, *a, **kw: by_id[inst["instance_id"]]
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_predictions.py",
            "--out",
            str(tmp_path),
            "--run-id",
            "usage",
            "--workers",
            "1",
        ],
    )

    run_predictions.main()
    out = capsys.readouterr().out

    assert "输入 token 类别" in out
    assert "user: 40 / 20.0" in out
    assert "Read: 3 / 15" in out
    assert "摘要调用次数: 3 / 1.5" in out

    diagnostics = [
        json.loads(line)
        for line in (tmp_path / "usage" / "diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [rec["context_usage"]["summary_calls"] for rec in diagnostics] == [2, 1]

    predictions = [
        json.loads(line)
        for line in (tmp_path / "usage" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert all(set(rec) == {"instance_id", "model_name_or_path", "model_patch"} for rec in predictions)
