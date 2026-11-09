"""容器编排用量/费用落盘与汇总的单测：用假 sandbox/agent，不需要 Docker。"""
import time
from types import SimpleNamespace

from eval import container_runner
from eval.run_predictions import summarize

class FakeSandbox:
    """只实现 run_instance 用到的四个方法。"""

    def __init__(self, image="img", image_ok=True, **kwargs):
        self.image_ok = image_ok

    def ensure_image(self, pull_timeout=0):
        if not self.image_ok:
            return SimpleNamespace(ok=False, out="", err="no image")
        return SimpleNamespace(ok=True, out="", err="")

    def start(self):
        return SimpleNamespace(ok=True, out="", err="")

    def model_patch(self, exclude_paths=None):
        return SimpleNamespace(ok=True, out="diff --git a/x b/x\n", err="")

    def stop(self):
        return None


class FakeAgent:
    def __init__(self, meta):
        self._session_metadata = meta
        self.diagnostics = {
            "tool_calls": {"Read": 1},
            "bash_calls": 0,
            "test_runs": 0,
            "unknown_tools": 0,
            "tool_errors": 0,
            "edited_files": set(),
        }

    def run(self, text):
        time.sleep(0.01)  # 让端到端时长有非零值，避免 round 到 0.0
        return "done"


def _patch_sandbox(monkeypatch, **kwargs):
    monkeypatch.setattr(container_runner, "DockerSandbox", lambda **kw: FakeSandbox(**kwargs))


def _patch_agent(monkeypatch, meta):
    monkeypatch.setattr(container_runner, "build_agent", lambda sandbox, max_steps=50: FakeAgent(meta))


def _instance():
    return {
        "instance_id": "demo__1",
        "image": "demo:latest",
        "problem_statement": "fix it",
        "test_patch": "",
        "test_files": [],
    }


def test_diagnostics_six_fields(monkeypatch):
    """成功实例落六项；费用按单价算出；请求次数与 steps 同源。"""
    _patch_sandbox(monkeypatch)
    _patch_agent(monkeypatch, {
        "total_steps": 7,
        "total_tokens": 1200,
        "input_tokens": 1000,
        "output_tokens": 200,
        "cached_tokens": 400,
        "cache_hit_available": True,
        "duration_seconds": 3.0,
    })
    rec = container_runner.run_instance(_instance())

    assert rec["status"] == "ok"
    assert rec["input_tokens"] == 1000
    assert rec["output_tokens"] == 200
    assert rec["cached_tokens"] == 400
    assert rec["llm_requests"] == rec["steps"] == 7
    assert rec["duration_seconds"] > 0
    assert abs(rec["total_cost"] - 0.002816) < 1e-9
    assert rec["diagnostics"]["cache_hit_available"] is True


def test_early_exit_keeps_zero_shape(monkeypatch):
    """拉镜像失败早退：字段形状与成功实例一致，六项为 0。"""
    _patch_sandbox(monkeypatch, image_ok=False)
    rec = container_runner.run_instance(_instance())

    assert rec["status"] == "error"
    for key in ("input_tokens", "output_tokens", "cached_tokens", "llm_requests", "total_cost"):
        assert rec[key] == 0
    assert rec["duration_seconds"] == 0.0
    assert rec["diagnostics"]["cache_hit_available"] is False


def test_summary_matches_details():
    """汇总对明细逐项求和取均值，与 diagnostics 记录逐条一致。"""
    results = [
        {"input_tokens": 1000, "output_tokens": 200, "cached_tokens": 400,
         "llm_requests": 7, "duration_seconds": 10.0, "total_cost": 0.002816},
        {"input_tokens": 500, "output_tokens": 100, "cached_tokens": 0,
         "llm_requests": 3, "duration_seconds": 20.0, "total_cost": 0.0018},
    ]
    summary = summarize(results)
    tot = summary["totals"]
    assert summary["count"] == 2
    assert tot["input_tokens"] == 1500
    assert tot["output_tokens"] == 300
    assert tot["cached_tokens"] == 400
    assert tot["llm_requests"] == 10
    assert tot["duration_seconds"] == 30.0
    assert abs(tot["total_cost"] - 0.004616) < 1e-12
    # 均值 = 总量 / 实例数，分母为全部实例（与 avg_steps 口径一致）
    assert summary["averages"]["input_tokens"] == 750
    assert summary["averages"]["llm_requests"] == 5
    assert abs(summary["averages"]["total_cost"] - 0.002308) < 1e-12
    # 空结果不除零
    empty = summarize([])
    assert empty["totals"]["input_tokens"] == 0 and empty["averages"]["total_cost"] == 0.0


def test_legacy_fields_unchanged(monkeypatch):
    """既有字段语义不变：tokens 仍为输入+输出，steps/patch/prediction 结构不动。"""
    _patch_sandbox(monkeypatch)
    _patch_agent(monkeypatch, {
        "total_steps": 2, "total_tokens": 1200,
        "input_tokens": 1000, "output_tokens": 200, "cached_tokens": 0,
        "cache_hit_available": True, "duration_seconds": 1.0,
    })
    rec = container_runner.run_instance(_instance())

    assert rec["tokens"] == rec["input_tokens"] + rec["output_tokens"] == 1200
    assert rec["steps"] == 2
    assert rec["model_patch"].startswith("diff --git")
    assert rec["patch_bytes"] == len(rec["model_patch"].encode("utf-8"))
    for key in ("instance_id", "model_name_or_path", "status", "resolved", "diagnostics", "error"):
        assert key in rec
