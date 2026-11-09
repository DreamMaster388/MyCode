"""验收测试：容器评测的六项用量/费用指标与 run 汇总（spec R1-R8）。

独立说明：本文件按 `docs/sdlc/feat/eval-add-metrics/spec.md` 的 R1-R8 推导
断言，再按 `eval.container_runner` / `eval.run_predictions` 的公开行为接线；
不修改实现与既有测试。

全部用例在宿主机运行：DockerSandbox 与 build_agent 都用假对象替换，
不依赖 Docker、网络或真实模型调用。
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from eval import container_runner
from eval import run_predictions

SIX_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "llm_requests",
    "duration_seconds",
    "total_cost",
)

LEGACY_DIAGNOSTIC_KEYS = (
    "path_guard",
    "tool_calls",
    "bash_calls",
    "test_runs",
    "unknown_tools",
    "tool_errors",
    "edited_files",
)


# ------------------------------------------------------------------ 假对象 --

class _FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def advance(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


class _Result:
    def __init__(self, ok=True, out="", err=""):
        self.ok = ok
        self.out = out
        self.err = err


class _FakeSandbox:
    """只实现 run_instance 用到的四个方法；生命周期各段推进假时钟。"""

    def __init__(self, clock=None, ensure_ok=True, start_ok=True):
        self.clock = clock
        self.ensure_ok = ensure_ok
        self.start_ok = start_ok
        self.stop_calls = 0
        self.patch_calls = 0

    def _advance(self, seconds):
        if self.clock is not None:
            self.clock.advance(seconds)

    def ensure_image(self, pull_timeout=7200):
        self._advance(300)  # 拉镜像不应计入端到端时长
        return _Result(self.ensure_ok, "image", "" if self.ensure_ok else "pull failed")

    def start(self):
        self._advance(200)  # 容器启动不应计入端到端时长
        return _Result(self.start_ok, "started", "" if self.start_ok else "start failed")

    def model_patch(self, exclude_paths=None):
        self.patch_calls += 1
        self._advance(0.5)  # 补丁提取应计入端到端时长
        return _Result(True, "diff --git a/x b/x", "")

    def stop(self):
        self.stop_calls += 1
        self._advance(100)  # 容器停止发生在计时之后


class _FakeAgent:
    def __init__(
        self,
        meta=None,
        diagnostics=None,
        clock=None,
        final="done",
        run_error=None,
    ):
        self._session_metadata = meta if meta is not None else _meta()
        self.diagnostics = diagnostics if diagnostics is not None else _diag()
        self.clock = clock
        self.final = final
        self.run_error = run_error
        self.run_calls = 0

    def run(self, problem_statement):
        self.run_calls += 1
        if self.clock is not None:
            self.clock.advance(7.0)  # agent 启动到产出最终文本
        if self.run_error is not None:
            raise self.run_error
        return self.final


def _meta(
    steps=3,
    input_tokens=1000,
    output_tokens=500,
    cached_tokens=200,
    available=True,
):
    return {
        "total_steps": steps,
        "total_tokens": input_tokens + output_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "cache_hit_available": available,
        # 与 run_instance 自己的计时无关，故意给一个不同的值作哨兵。
        "duration_seconds": 999.0,
    }


def _diag():
    return {
        "tool_calls": {"Bash": 2, "Edit": 1},
        "bash_calls": 2,
        "test_runs": 1,
        "unknown_tools": 0,
        "tool_errors": 1,
        "edited_files": {"src/b.py", "src/a.py"},
    }


def _instance(instance_id="demo__repo-1"):
    return {
        "instance_id": instance_id,
        "image": "swebench/demo:latest",
        "problem_statement": "fix the bug",
        "test_patch": "",
        "test_files": [],
    }


def _install_runner(monkeypatch, sandbox, agent, build_calls=None):
    monkeypatch.setattr(container_runner, "DockerSandbox", lambda **kwargs: sandbox)

    def fake_build(sb, max_steps=50):
        if build_calls is not None:
            build_calls.append(max_steps)
        return agent

    monkeypatch.setattr(container_runner, "build_agent", fake_build)


def _run_ok(monkeypatch, meta=None, diagnostics=None):
    clock = _FakeClock()
    sandbox = _FakeSandbox(clock)
    agent = _FakeAgent(meta, diagnostics, clock)
    _install_runner(monkeypatch, sandbox, agent)
    monkeypatch.setattr(container_runner, "time", SimpleNamespace(time=clock.time))
    return container_runner.run_instance(_instance()), sandbox, agent


def _record(
    instance_id,
    steps,
    input_tokens,
    output_tokens,
    cached_tokens,
    duration_seconds,
    status="ok",
):
    return {
        "instance_id": instance_id,
        "status": status,
        "model_patch": "diff --git a/x b/x",
        "patch_bytes": 19,
        "steps": steps,
        "tokens": input_tokens + output_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "llm_requests": steps,
        "duration_seconds": duration_seconds,
        "total_cost": round(
            container_runner.estimate_cost(
                input_tokens, output_tokens, cached_tokens
            ),
            6,
        ),
        "final_text": "done",
        "error": "" if status == "ok" else "boom",
        "diagnostics": {
            "path_guard": True,
            "cache_hit_available": True,
            "tool_calls": {},
            "bash_calls": 0,
            "test_runs": 0,
            "unknown_tools": 0,
            "tool_errors": 0,
            "edited_files": [],
        },
    }


# =================================================================== R1 ====
# 每实例记录含六项指标，数值来自 agent 会话元数据。

def test_r1_instance_record_contains_six_metrics(monkeypatch):
    rec, _, _ = _run_ok(monkeypatch)

    assert rec["status"] == "ok"
    assert set(SIX_FIELDS) <= set(rec)
    assert rec["input_tokens"] == 1000
    assert rec["output_tokens"] == 500
    assert rec["cached_tokens"] == 200
    assert rec["diagnostics"]["cache_hit_available"] is True


def test_r1_record_round_trips_through_diagnostics_jsonl(monkeypatch):
    rec, _, _ = _run_ok(monkeypatch)

    loaded = json.loads(json.dumps(rec, ensure_ascii=False))

    assert {key: loaded[key] for key in SIX_FIELDS} == {
        key: rec[key] for key in SIX_FIELDS
    }


def test_r1_cache_availability_marker_lands_in_diagnostics(monkeypatch):
    rec, _, _ = _run_ok(monkeypatch, meta=_meta(cached_tokens=0, available=False))

    assert rec["cached_tokens"] == 0
    assert rec["diagnostics"]["cache_hit_available"] is False


# =================================================================== R5 ====
# 请求次数与 total_steps 同源（不独立计次、不计 HTTP 重试）。

def test_r5_llm_requests_shares_steps_source(monkeypatch):
    meta = _meta(steps=7)
    meta["llm_requests"] = 999  # 哨兵：实现若从其它字段取值会读到这里

    rec, _, _ = _run_ok(monkeypatch, meta=meta)

    assert rec["steps"] == 7
    assert rec["llm_requests"] == 7


# =================================================================== R6 ====
# 端到端时长：agent 启动到补丁提取完成，不含拉镜像与容器启停。

def test_r6_duration_excludes_pull_and_container_lifecycle(monkeypatch):
    clock = _FakeClock(1000.0)
    sandbox = _FakeSandbox(clock)
    agent = _FakeAgent(_meta(), clock=clock)
    _install_runner(monkeypatch, sandbox, agent)
    monkeypatch.setattr(container_runner, "time", SimpleNamespace(time=clock.time))

    rec = container_runner.run_instance(_instance())

    # 拉镜像 300s、启动 200s、停止 100s 都不计；agent 7.0s + 补丁提取 0.5s 计入。
    assert rec["duration_seconds"] == pytest.approx(7.5, abs=1e-6)
    assert rec["duration_seconds"] < 100
    assert sandbox.stop_calls == 1


# =================================================================== R4 ====
# 计价公式：未命中输入 × 2 + 缓存 × 0.04 + 输出 × 8（元/百万 token）。

@pytest.mark.parametrize(
    "input_tokens,output_tokens,cached_tokens,expected",
    [
        (1_000_000, 0, 0, 2.0),          # 未命中输入 2 元/百万
        (0, 0, 1_000_000, 0.04),         # 缓存命中 0.04 元/百万
        (0, 1_000_000, 0, 8.0),          # 输出 8 元/百万
        (1_000_000, 250_000, 400_000, 3.216),  # 60 万未命中 + 40 万缓存 + 25 万输出
        (0, 0, 0, 0.0),
    ],
)
def test_r4_cost_formula(input_tokens, output_tokens, cached_tokens, expected):
    cost = container_runner.estimate_cost(input_tokens, output_tokens, cached_tokens)

    assert cost == pytest.approx(expected, abs=1e-12)


def test_r4_cache_larger_than_input_never_negative():
    cost = container_runner.estimate_cost(100, 0, 500)

    # 未命中输入按 max(0, input - cache) 计，不因缓存超额而出现负费用。
    assert cost == pytest.approx(500 * 0.04 / 1_000_000, abs=1e-12)
    assert cost > 0


def test_r4_record_cost_rounded_to_six_decimals(monkeypatch):
    meta = _meta(input_tokens=1234, output_tokens=567, cached_tokens=234)

    rec, _, _ = _run_ok(monkeypatch, meta=meta)

    expected = container_runner.estimate_cost(1234, 567, 234)
    assert rec["total_cost"] == round(expected, 6)
    assert rec["total_cost"] == pytest.approx(expected, abs=1e-6)


def test_r4_summary_cost_matches_detail_values(monkeypatch):
    rec_a, _, _ = _run_ok(monkeypatch, meta=_meta(1, 1234, 567, 234))
    rec_b, _, _ = _run_ok(monkeypatch, meta=_meta(4, 2000, 1000, 0))

    summary = run_predictions.summarize([rec_a, rec_b])

    expected_total = rec_a["total_cost"] + rec_b["total_cost"]
    assert summary["totals"]["total_cost"] == pytest.approx(expected_total, abs=1e-12)
    assert summary["averages"]["total_cost"] == pytest.approx(
        expected_total / 2, abs=1e-12
    )


# =================================================================== R7 ====
# run 结束打印六项汇总（总量 + 均值），与 diagnostics 明细逐项一致。

def test_r7_summary_totals_and_averages_match_details():
    records = [
        _record("a-1", steps=3, input_tokens=1000, output_tokens=500,
                cached_tokens=200, duration_seconds=12.0),
        _record("b-2", steps=5, input_tokens=2000, output_tokens=1000,
                cached_tokens=0, duration_seconds=8.0),
    ]

    summary = run_predictions.summarize(records)

    assert summary["count"] == 2
    for key in SIX_FIELDS:
        total = sum(rec[key] for rec in records)
        assert summary["totals"][key] == pytest.approx(total)
        assert summary["averages"][key] == pytest.approx(total / 2)


def test_r7_summary_empty_run_is_zero_without_divide_error():
    summary = run_predictions.summarize([])

    assert summary["count"] == 0
    assert all(value == 0 for value in summary["totals"].values())
    assert all(value == 0 for value in summary["averages"].values())


def test_r7_average_denominator_includes_failed_instances():
    ok = _record("a-1", 3, 1000, 500, 200, 12.0)
    failed = _record("b-2", 0, 0, 0, 0, 0.0, status="error")

    summary = run_predictions.summarize([ok, failed])

    assert summary["count"] == 2
    assert summary["averages"]["input_tokens"] == pytest.approx(500.0)


def _run_main(monkeypatch, tmp_path, records):
    instances = [_instance(rec["instance_id"]) for rec in records]
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
            "acceptance",
            "--workers",
            "1",
        ],
    )
    run_predictions.main()
    return tmp_path / "acceptance"


def test_r7_cli_prints_six_metric_totals_and_averages(monkeypatch, tmp_path, capsys):
    records = [
        _record("a-1", steps=3, input_tokens=1000, output_tokens=500,
                cached_tokens=200, duration_seconds=12.0),
        _record("b-2", steps=5, input_tokens=2000, output_tokens=1000,
                cached_tokens=0, duration_seconds=8.0),
    ]

    out_dir = _run_main(monkeypatch, tmp_path, records)
    out = capsys.readouterr().out

    totals = {key: sum(rec[key] for rec in records) for key in SIX_FIELDS}
    averages = {key: totals[key] / len(records) for key in SIX_FIELDS}

    assert (
        f"输入 tokens: {totals['input_tokens']}  "
        f"输出 tokens: {totals['output_tokens']}  "
        f"缓存命中: {totals['cached_tokens']}"
    ) in out
    assert (
        f"模型请求: {totals['llm_requests']}  "
        f"端到端耗时: {totals['duration_seconds']:.0f}s  "
        f"总费用: {totals['total_cost']:.4f} 元"
    ) in out
    assert (
        f"均值/实例: 输入 {averages['input_tokens']:.0f}  "
        f"输出 {averages['output_tokens']:.0f}  "
        f"缓存 {averages['cached_tokens']:.0f}  "
        f"请求 {averages['llm_requests']:.1f}  "
        f"耗时 {averages['duration_seconds']:.1f}s  "
        f"费用 {averages['total_cost']:.4f} 元"
    ) in out

    # 终端汇总与落盘明细逐项一致（同源）。
    written = [
        json.loads(line)
        for line in (out_dir / "diagnostics.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(written) == len(records)
    for key in SIX_FIELDS:
        assert sum(rec[key] for rec in written) == pytest.approx(totals[key])


# =================================================================== R8 ====
# 旧字段语义不变：total_tokens=输入+输出、steps、predictions 结构、诊断旧键。

def test_r8_legacy_record_fields_unchanged(monkeypatch):
    rec, _, _ = _run_ok(monkeypatch)

    assert rec["tokens"] == rec["input_tokens"] + rec["output_tokens"] == 1500
    assert rec["steps"] == 3
    assert rec["model_patch"] == "diff --git a/x b/x"
    assert rec["patch_bytes"] == len("diff --git a/x b/x".encode("utf-8"))
    assert rec["final_text"] == "done"
    assert rec["error"] == ""


def test_r8_legacy_diagnostics_keys_stay(monkeypatch):
    rec, _, _ = _run_ok(monkeypatch)

    for key in LEGACY_DIAGNOSTIC_KEYS:
        assert key in rec["diagnostics"]
    assert rec["diagnostics"]["edited_files"] == ["src/a.py", "src/b.py"]
    assert rec["diagnostics"]["tool_calls"] == {"Bash": 2, "Edit": 1}


def test_r8_predictions_structure_unchanged(monkeypatch, tmp_path):
    records = [
        _record("a-1", steps=3, input_tokens=1000, output_tokens=500,
                cached_tokens=200, duration_seconds=12.0),
        _record("b-2", steps=5, input_tokens=2000, output_tokens=1000,
                cached_tokens=0, duration_seconds=8.0),
    ]

    out_dir = _run_main(monkeypatch, tmp_path, records)

    predictions = [
        json.loads(line)
        for line in (out_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(predictions) == 2
    assert [set(pred) for pred in predictions] == [
        {"instance_id", "model_name_or_path", "model_patch"}
    ] * 2
    assert [pred["instance_id"] for pred in predictions] == ["a-1", "b-2"]
    assert all(pred["model_patch"] == "diff --git a/x b/x" for pred in predictions)


# ============================================================ 早退/异常 ====
# 早退/异常实例形状与成功实例一致，六项为 0。

def test_image_pull_failure_has_same_shape_and_zero_metrics(monkeypatch):
    success, _, _ = _run_ok(monkeypatch)

    clock = _FakeClock()
    sandbox = _FakeSandbox(clock, ensure_ok=False)
    built = []
    _install_runner(monkeypatch, sandbox, _FakeAgent(_meta()), build_calls=built)

    rec = container_runner.run_instance(_instance())

    assert rec["status"] == "error"
    assert "image unavailable" in rec["error"]
    assert set(rec) == set(success)
    for key in SIX_FIELDS:
        assert rec[key] == 0
    assert rec["diagnostics"]["cache_hit_available"] is False
    assert rec["steps"] == 0 and rec["tokens"] == 0
    assert built == []
    assert sandbox.stop_calls == 0


def test_container_start_failure_has_same_shape_and_zero_metrics(monkeypatch):
    success, _, _ = _run_ok(monkeypatch)

    clock = _FakeClock()
    sandbox = _FakeSandbox(clock, start_ok=False)
    built = []
    _install_runner(monkeypatch, sandbox, _FakeAgent(_meta()), build_calls=built)

    rec = container_runner.run_instance(_instance())

    assert rec["status"] == "error"
    assert "container start failed" in rec["error"]
    assert set(rec) == set(success)
    for key in SIX_FIELDS:
        assert rec[key] == 0
    assert rec["diagnostics"]["cache_hit_available"] is False
    assert built == []


def test_agent_exception_has_same_shape_and_zero_metrics(monkeypatch):
    success, _, _ = _run_ok(monkeypatch)

    clock = _FakeClock()
    sandbox = _FakeSandbox(clock)
    agent = _FakeAgent(_meta(), clock=clock, run_error=RuntimeError("boom"))
    _install_runner(monkeypatch, sandbox, agent)

    rec = container_runner.run_instance(_instance())

    assert rec["status"] == "error"
    assert "RuntimeError" in rec["error"] and "boom" in rec["error"]
    assert set(rec) == set(success)
    for key in SIX_FIELDS:
        assert rec[key] == 0
    assert rec["diagnostics"]["cache_hit_available"] is False
    assert sandbox.stop_calls == 1
