"""评测编排：单实例的 SWE-bench 风格工作流。

流程：建仓库→跑 agent→提取 git diff→写 golden 测试→跑 pytest→判定。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional

from .agent_runner import run_agent
from .judge import judge, parse_test_results

INSTANCE_ID = "instance_id"
REPO_URL = "repo_url"
BASE_COMMIT = "base_commit"
REPO_DIR = "repo_dir"
TEST_PATCH = "test_patch"
TEST_FILES = "test_files"
TEST_CMD = "test_cmd"
PROBLEM = "problem_statement"


def _git(workdir: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(workdir), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def prepare_workdir(instance: Dict, workspace: str) -> Path:
    """在 workspace 下建立该实例的隔离工作目录（干净 git 基线）。"""
    workdir = Path(workspace) / instance[INSTANCE_ID]
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    repo_url = instance.get(REPO_URL)
    if repo_url:
        subprocess.run(
            ["git", "clone", "--quiet", repo_url, str(workdir)],
            check=True,
        )
        base = instance.get(BASE_COMMIT)
        if base:
            _git(workdir, "checkout", "--quiet", base)
        return workdir

    # 本地合成仓库：复制 repo_dir 并建 git 基线
    source = Path(__file__).parent / "instances" / instance[REPO_DIR]
    if not source.exists():
        raise FileNotFoundError(f"repo_dir not found: {source}")

    shutil.copytree(source, workdir, dirs_exist_ok=True)
    _git(workdir, "init", "-q")
    _git(workdir, "config", "user.email", "eval@example.com")
    _git(workdir, "config", "user.name", "eval")
    _git(workdir, "add", "-A")
    _git(workdir, "commit", "-q", "-m", "base")
    return workdir


def run_instance(instance: Dict, workspace: str, out_dir: str, max_steps: int = 25) -> Dict:
    """执行单实例完整流程，返回结果 dict（含补丁路径与判定）。"""
    workdir = prepare_workdir(instance, workspace)
    inst_id = instance[INSTANCE_ID]

    try:
        agent_result = run_agent(str(workdir), instance[PROBLEM], max_steps=max_steps)
    except Exception as exc:  # LLM / 工具链异常
        return {
            INSTANCE_ID: inst_id,
            "status": "error",
            "resolved": False,
            "final_text": f"[eval] agent error: {exc}",
        }

    # 1) 提取模型补丁（含新增文件）
    _git(workdir, "add", "-A")
    patch = _git(workdir, "diff", "--cached", "HEAD")
    patch_dir = Path(out_dir) / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_path = patch_dir / f"{inst_id}.patch"
    patch_path.write_text(patch)

    # 2) 应用 golden 测试（先于测试运行，但不污染模型补丁）
    test_files = instance.get(TEST_FILES) or {}
    for rel, content in test_files.items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    test_patch = instance.get(TEST_PATCH)
    if test_patch:
        apply_test_patch(workdir, test_patch)

    # 3) 运行测试
    test_cmd = instance.get(TEST_CMD) or "python -m pytest -v --tb=no"
    proc = subprocess.run(
        test_cmd,
        shell=True,
        cwd=str(workdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    test_output = proc.stdout + "\n" + proc.stderr
    results = parse_test_results(test_output)

    # 4) 判定
    resolved, unresolved_f2p, regressed_p2p = judge(instance, results)

    return {
        INSTANCE_ID: inst_id,
        "status": "ok" if resolved else "unresolved",
        "resolved": resolved,
        "patch": str(patch_path),
        "patch_bytes": len(patch.encode("utf-8")),
        "test_command": test_cmd,
        "test_exit_code": proc.returncode,
        "test_results": results,
        "fail_to_pass": instance.get("fail_to_pass") or [],
        "pass_to_pass": instance.get("pass_to_pass") or [],
        "unresolved_f2p": unresolved_f2p,
        "regressed_p2p": regressed_p2p,
        "steps": agent_result["steps"],
        "tokens": agent_result["tokens"],
        "duration_seconds": round(agent_result["duration_seconds"], 3),
        "final_text": agent_result["final_text"][:2000],
    }


def apply_test_patch(workdir: Path, test_patch: str) -> None:
    """应用 unified diff（git apply 优先，回退 patch -p1）。"""
    patch_file = workdir / ".eval_test.patch"
    patch_file.write_text(test_patch)
    try:
        _git(workdir, "apply", str(patch_file))
    except RuntimeError:
        subprocess.run(
            ["patch", "-p1", "-d", str(workdir)],
            input=test_patch,
            text=True,
            check=True,
        )
    finally:
        patch_file.unlink(missing_ok=True)


def write_jsonl(path: str, records: list[Dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
