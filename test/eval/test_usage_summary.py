"""Ticket 04 验收：本地 eval 透传与汇总。

用假 agent/假结果验证 context_usage 进入本地 harness 结果、终端汇总与
results.jsonl 明细同源；不联网、不跑真实 harness。
"""
from __future__ import annotations

import json
import sys

from eval import agent_runner, harness, run_eval
from eval.usage_summary import (
    empty_context_usage,
    format_context_usage,
    summarize_context_usage,
)


def _usage(categories=None, tools=None, summary_calls=0):
    usage = empty_context_usage()
    usage["categories"].update(categories or {})
    usage["tools"] = tools or {}
    usage["summary_calls"] = summary_calls
    return usage


def test_summarize_context_usage_totals_and_averages():
    results = [
        {"context_usage": _usage({"user": 10, "assistant": 20}, {"Read": {"input_tokens": 5, "calls": 1}}, 2)},
        {"context_usage": _usage({"user": 30, "assistant": 40}, {"Read": {"input_tokens": 10, "calls": 2}}, 1)},
    ]

    summary = summarize_context_usage(results)

    assert summary["count"] == 2
    assert summary["totals"]["categories"]["user"] == 40
    assert summary["totals"]["categories"]["assistant"] == 60
    assert summary["totals"]["tools"]["Read"] == {"input_tokens": 15, "calls": 3}
    assert summary["totals"]["summary_calls"] == 3
    assert summary["averages"]["categories"]["user"] == 20.0
    assert summary["averages"]["tools"]["Read"]["calls"] == 1.5
    assert summary["averages"]["summary_calls"] == 1.5

    lines = format_context_usage(summary)
    assert any("user: 40 / 20.0" in line for line in lines)
    assert any("Read: 3 / 15" in line for line in lines)
    assert any("摘要调用次数: 3 / 1.5" in line for line in lines)


def test_summarize_context_usage_empty_results_is_zero_shape():
    summary = summarize_context_usage([])

    assert summary["count"] == 0
    assert set(summary["totals"]["categories"]) == set(empty_context_usage()["categories"])
    assert all(value == 0 for value in summary["totals"]["categories"].values())
    assert summary["totals"]["tools"] == {}
    assert summary["totals"]["summary_calls"] == 0
    assert summary["averages"]["summary_calls"] == 0.0


def test_local_harness_passes_context_usage(monkeypatch, tmp_path):
    usage = _usage({"user": 12}, {"Bash": {"input_tokens": 7, "calls": 1}}, 1)
    monkeypatch.setattr(harness, "prepare_workdir", lambda inst, workspace: tmp_path)
    monkeypatch.setattr(harness, "_git", lambda workdir, *args, check=True: "")
    monkeypatch.setattr(
        harness,
        "run_agent",
        lambda workdir, problem, max_steps=25: {
            "final_text": "done",
            "steps": 1,
            "tokens": 10,
            "duration_seconds": 0.1,
            "context_usage": usage,
        },
    )
    monkeypatch.setattr(harness, "parse_test_results", lambda output: {})
    monkeypatch.setattr(harness, "judge", lambda inst, results: (True, [], []))

    rec = harness.run_instance(
        {
            "instance_id": "demo__1",
            "problem_statement": "fix it",
            "test_patch": "",
            "test_files": {},
            "test_cmd": "true",
        },
        str(tmp_path),
        str(tmp_path),
    )

    assert rec["context_usage"] == usage


def test_local_harness_error_keeps_zero_context_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(harness, "prepare_workdir", lambda inst, workspace: tmp_path)

    def _raise(*args, **kwargs):
        raise RuntimeError("agent boom")

    monkeypatch.setattr(harness, "run_agent", _raise)

    rec = harness.run_instance(
        {"instance_id": "demo__1", "problem_statement": "fix it"},
        str(tmp_path),
        str(tmp_path),
    )

    assert rec["status"] == "error"
    assert rec["context_usage"] == empty_context_usage()


def test_agent_runner_returns_context_usage(monkeypatch):
    usage = _usage({"user": 9}, summary_calls=1)

    class _FakeAgent:
        def __init__(self):
            self._session_metadata = {
                "total_steps": 1,
                "total_tokens": 9,
                "duration_seconds": 0.2,
                "context_usage": usage,
            }

        def run(self, problem):
            return "done"

    monkeypatch.setattr(agent_runner, "build_agent", lambda workdir, max_steps=25: _FakeAgent())

    result = agent_runner.run_agent("/tmp/demo", "fix it")

    assert result["context_usage"] == usage


def test_run_eval_prints_and_writes_context_usage(monkeypatch, tmp_path, capsys):
    usage = _usage({"user": 12, "assistant": 20}, {"Read": {"input_tokens": 5, "calls": 1}}, 2)
    record = {
        "instance_id": "demo__1",
        "resolved": True,
        "status": "ok",
        "steps": 1,
        "tokens": 32,
        "duration_seconds": 0.1,
        "context_usage": usage,
    }
    monkeypatch.setattr(run_eval, "load_instances", lambda path: [{"instance_id": "demo__1"}])
    monkeypatch.setattr(
        run_eval,
        "run_instance",
        lambda inst, workspace, out, max_steps=25: dict(record),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_eval.py", "--instances", "x", "--out", str(tmp_path)],
    )

    run_eval.main()
    out = capsys.readouterr().out

    assert "输入 token 类别" in out
    assert "user: 12 / 12.0" in out
    assert "Read: 1 / 5" in out
    assert "摘要调用次数: 2 / 2.0" in out

    written = json.loads((tmp_path / "results.jsonl").read_text(encoding="utf-8"))
    assert written["context_usage"] == usage
