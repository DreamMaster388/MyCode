"""运行 HelloAgents CodeAgent 于某个用例仓库，返回最终文本与统计信息。

把工具绑定到用例工作目录，并将进程 cwd 切到仓库内，使 Bash 也在仓库内执行。
统计信息从 agent 的会话元数据读取。
"""
from __future__ import annotations

import os
from typing import Dict

from dotenv import load_dotenv

load_dotenv()  # 读取 LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL

from agents.agent.code_agent import CodeAgent
from agents.core.config import Config
from agents.core.llm import HelloAgentsLLM
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


def build_agent(workdir: str, max_steps: int = 25) -> CodeAgent:
    """构造绑定到 workdir 的 CodeAgent（关闭影响评测的附加功能）。"""
    llm = HelloAgentsLLM()
    registry = ToolRegistry()
    for tool in (
        ReadTool(project_root=workdir),
        WriteTool(project_root=workdir),
        EditTool(project_root=workdir),
        BashTool(),
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
    )
    return CodeAgent(
        name="EvalCodingAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=SYSTEM_PROMPT,
        config=config,
        max_steps=max_steps,
        max_run_tokens=0,
    )


def run_agent(workdir: str, problem: str, max_steps: int = 25) -> Dict:
    """在 workdir 内运行 agent 解决问题。返回含最终文本与统计信息的 dict。"""
    agent = build_agent(workdir, max_steps=max_steps)
    cwd = os.getcwd()
    os.chdir(workdir)  # 让 Bash / 相对路径都落在仓库内
    try:
        final_text = agent.run(problem)
    finally:
        os.chdir(cwd)

    meta = agent._session_metadata  # offical 内部字段：total_steps / total_tokens / duration_seconds
    return {
        "final_text": final_text,
        "steps": meta.get("total_steps", 0),
        "tokens": meta.get("total_tokens", 0),
        "duration_seconds": meta.get("duration_seconds", 0.0),
    }
