"""B1 单实例编排：容器内运行 CodeAgent 并产出 model_patch + 诊断。

与 `agent_runner.py` 的区别：
- Agent 的工具全部是"容器版"，读写/执行都发生在 SWE-bench 实例容器 /testbed 内。
- 只负责"产补丁 + 产诊断"，判定仍交给官方 swebench harness。
"""
from __future__ import annotations

import os
import re
import tempfile
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from agents.agent.code_agent import CodeAgent
from agents.core.config import Config
from agents.core.llm import HelloAgentsLLM
from agents.core.mode import AgentMode, ModeGuard
from agents.fs import SandboxMode, build_policy
from agents.tools.registry import ToolRegistry

from .container_tools import build_container_tools
from .docker_sandbox import CONTAINER_WORKDIR, DockerSandbox
from .swebench_io import eval_activation_prefix, image_for

DEFAULT_MODEL_NAME = os.environ.get("LLM_MODEL_ID", "codeagent")

# 工具完整输出固定到仓库外的目录；优先 /mnt/data，避免撑爆根盘。
_EVAL_TMP = "/mnt/data/lyd/tmp" if os.path.isdir("/mnt/data/lyd/tmp") else tempfile.gettempdir()
TOOL_OUTPUT_DIR = os.path.join(_EVAL_TMP, "helloagents-eval-tool-output")

SYSTEM_PROMPT = (
    "You are an autonomous coding agent fixing a real issue in a software repository. "
    "The repository is checked out at /testbed and its dependencies are already installed in a conda environment. "
    "Investigate the code with Read/Grep/Glob, then modify it with Edit/Write. "
    "Run the relevant tests with Bash to verify your fix and iterate until they pass. "
    "Make the minimal change that resolves the issue and do not break existing behavior. "
    "When finished, briefly state what you changed."
)

_TEST_CMD_RE = re.compile(r"\b(pytest|unittest|tox|nose|py\.test)\b|manage\.py\s+test")


class InstrumentedCodeAgent(CodeAgent):
    """记录工具调用/错误/是否跑测试等诊断，供 agent 性能分析。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.diagnostics: Dict[str, Any] = {
            "tool_calls": {},
            "bash_calls": 0,
            "test_runs": 0,
            "unknown_tools": 0,
            "guard_blocks": 0,
            "guard_reasons": [],
            "tool_errors": 0,
            "edited_files": set(),
        }

    def _execute_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        diag = self.diagnostics
        key = (tool_name or "").strip()
        diag["tool_calls"][key] = diag["tool_calls"].get(key, 0) + 1

        if key.lower() == "bash":
            diag["bash_calls"] += 1
            command = (arguments or {}).get("command", "") or ""
            if _TEST_CMD_RE.search(command):
                diag["test_runs"] += 1
        if key.lower() in ("edit", "write"):
            path = (arguments or {}).get("path")
            if path:
                diag["edited_files"].add(path)

        result = super()._execute_tool_call(tool_name, arguments)
        if "未找到工具" in result:
            diag["unknown_tools"] += 1
        if result.startswith("🚫"):
            diag["guard_blocks"] += 1
            if len(diag["guard_reasons"]) < 10:
                diag["guard_reasons"].append(result[:300])
        if result.startswith("❌"):
            diag["tool_errors"] += 1
        return result


def build_agent(sandbox: DockerSandbox, max_steps: int = 50) -> InstrumentedCodeAgent:
    llm = HelloAgentsLLM()
    registry = ToolRegistry()
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, CONTAINER_WORKDIR)
    guard = ModeGuard(AgentMode.BUILD, project_root=CONTAINER_WORKDIR, policy=policy)
    for tool in build_container_tools(sandbox):
        registry.register_tool(tool)

    config = Config(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        subagent_enabled=False,
        todowrite_enabled=False,
        devlog_enabled=False,
        tool_output_dir=TOOL_OUTPUT_DIR,
    )
    return InstrumentedCodeAgent(
        name="EvalCodingAgent",
        llm=llm,
        tool_registry=registry,
        mode_guard=guard,
        system_prompt=SYSTEM_PROMPT,
        config=config,
        max_steps=max_steps,
        max_run_tokens=0,
    )


def run_instance(
    instance: Dict,
    max_steps: int = 50,
    pull_timeout: int = 7200,
    keep_container: bool = False,
) -> Dict[str, Any]:
    """在实例容器内运行 Agent，返回补丁与诊断。"""
    inst_id = instance["instance_id"]
    image = image_for(instance)
    sandbox = DockerSandbox(
        image=image,
        instance_id=inst_id,
        activation=eval_activation_prefix(instance),
    )

    base: Dict[str, Any] = {
        "instance_id": inst_id,
        "image": image,
        "model_name_or_path": DEFAULT_MODEL_NAME,
        "status": "error",
        "resolved": None,
        "model_patch": "",
        "patch_bytes": 0,
        "steps": 0,
        "tokens": 0,
        "duration_seconds": 0.0,
        "final_text": "",
        "error": "",
        "diagnostics": {},
    }

    try:
        ensure = sandbox.ensure_image(pull_timeout=pull_timeout)
        if not ensure.ok:
            base["error"] = f"image unavailable: {(ensure.err or ensure.out).strip()[:500]}"
            return base

        started = sandbox.start()
        if not started.ok:
            base["error"] = f"container start failed: {(started.err or started.out).strip()[:500]}"
            return base

        t0 = time.time()
        try:
            agent = build_agent(sandbox, max_steps=max_steps)
            final_text = agent.run(instance["problem_statement"])
            patch_res = sandbox.model_patch()
            patch = patch_res.out if patch_res.ok else ""
            if not patch_res.ok:
                base["error"] = f"patch extraction failed: {(patch_res.err or patch_res.out).strip()[:500]}"

            meta = agent._session_metadata
            diag = dict(agent.diagnostics)
            diag["edited_files"] = sorted(diag["edited_files"])
            base.update(
                {
                    "status": "ok",
                    "model_patch": patch,
                    "patch_bytes": len(patch.encode("utf-8")),
                    "steps": meta.get("total_steps", 0),
                    "tokens": meta.get("total_tokens", 0),
                    "duration_seconds": round(time.time() - t0, 3),
                    "final_text": (final_text or "")[:2000],
                    "diagnostics": diag,
                }
            )
        finally:
            if not keep_container:
                sandbox.stop()
    except Exception as exc:  # noqa: BLE001 - 评测需吞掉单实例异常继续
        base["error"] = f"{type(exc).__name__}: {exc}"
    return base
