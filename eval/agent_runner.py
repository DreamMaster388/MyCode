"""运行 HelloAgents CodeAgent 于某个用例仓库，返回最终文本与统计信息。

工具已全部显式绑定到用例工作目录(BashTool 用 cwd, 文件/grep/glob 用 project_root),
因此无需切换进程 cwd; 截断器完整输出固定到仓库外的绝对路径, 避免污染模型补丁。
统计信息从 agent 的会话元数据读取。
"""
from __future__ import annotations

import os
import tempfile
from typing import Dict

from dotenv import load_dotenv

load_dotenv()  # 读取 LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL

from agents.agent.code_agent import CodeAgent
from agents.core.config import Config
from agents.core.llm import HelloAgentsLLM
from agents.core.mode import AgentMode, ModeGuard
from agents.fs import build_policy, SandboxMode
from agents.tools.builtin import (
    BashTool,
    EditTool,
    GlobTool,
    GrepTool,
    ReadTool,
    WriteTool,
)
from agents.tools.registry import ToolRegistry


# 面向"仓库内改代码"的简洁系统提示
SYSTEM_PROMPT = (
    "You are a coding agent working inside a software repository. "
    "Use the Read, Write, Edit, Grep, Glob and Bash tools to investigate and fix the issue. "
    "Prefer the dedicated file tools over shell equivalents. "
    "To verify a fix, run the project's tests with Bash. "
    "Keep the final answer short."
)

# 截断器完整输出目录: 固定到系统临时区, 不落在 workdir 内,
# 否则会被 harness 的 `git add -A` 计入模型补丁。
TOOL_OUTPUT_DIR = os.path.join(tempfile.gettempdir(), "helloagents-eval-tool-output")


def build_agent(workdir: str, max_steps: int = 25) -> CodeAgent:
    """构造绑定到 workdir 的 CodeAgent（关闭影响评测的附加功能）。"""
    llm = HelloAgentsLLM()
    registry = ToolRegistry()
    policy = build_policy(SandboxMode.WORKSPACE_WRITE, workdir)
    # 守卫与工具共用同一 policy, 保证 bash/文件工具的围栏根一致。
    guard = ModeGuard(AgentMode.BUILD, policy=policy)
    for tool in (
        ReadTool(project_root=workdir, policy=policy),
        WriteTool(project_root=workdir, policy=policy),
        EditTool(project_root=workdir, policy=policy),
        BashTool(project_root=workdir, policy=policy),
        GrepTool(project_root=workdir),
        GlobTool(project_root=workdir),
    ):
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
    return CodeAgent(
        name="EvalCodingAgent",
        llm=llm,
        tool_registry=registry,
        mode_guard=guard,
        system_prompt=SYSTEM_PROMPT,
        config=config,
        max_steps=max_steps,
        max_run_tokens=0,
    )


def run_agent(workdir: str, problem: str, max_steps: int = 25) -> Dict:
    """在 workdir 内运行 agent 解决问题。返回含最终文本与统计信息的 dict。

    不切换进程 cwd: bash/文件/grep/glob 均已显式绑定 workdir,
    截断器输出目录为 TOOL_OUTPUT_DIR 绝对路径。
    """
    agent = build_agent(workdir, max_steps=max_steps)
    final_text = agent.run(problem)

    meta = agent._session_metadata  # offical 内部字段：total_steps / total_tokens / duration_seconds
    return {
        "final_text": final_text,
        "steps": meta.get("total_steps", 0),
        "tokens": meta.get("total_tokens", 0),
        "duration_seconds": meta.get("duration_seconds", 0.0),
    }
