"""主入口：启动基于 HelloAgents 框架的编码助手（简单交互式，多轮）。

用法：
    python main.py
    python -m agents        # 等价

首次使用：cp .env.example .env 并填入 LLM_API_KEY 等。
"""
import sys

from dotenv import load_dotenv

load_dotenv()

from agents.core.llm import HelloAgentsLLM
from agents.core.config import Config
from agents.agent.code_agent import CodeAgent
from agents.tools.registry import ToolRegistry
from agents.tools.builtin import ReadTool, WriteTool, EditTool, BashTool, GrepTool, GlobTool


SYSTEM_PROMPT = """You are a coding assistant operating in the user's local working directory. You accomplish tasks by calling tools. Follow this three-tier tool-calling hierarchy:

1. Dedicated tools (STRONGLY PREFERRED): use Read, Write, Edit, Grep, and Glob whenever possible. They are purpose-built, safer, and return structured results.
   - Read files/directories with Read.
   - Create new files with Write; modify existing files with Edit (prefer Edit over Write).
   - Search file contents with Grep; find files with Glob.
   - Always Read the relevant files before making changes; never guess code.

2. General-purpose tool - Bash (USE ONLY WHEN NECESSARY): invoke Bash solely for what the dedicated tools cannot do, such as running build/test/lint commands, installing dependencies, or inspecting the environment. Provide the full command in `command` and a short `description`. Do NOT pass file paths as a separate parameter - embed them directly in the command string.

3. Discouraged pattern (AVOID): do not reimplement dedicated-tool behavior inside Bash. Using shell commands such as `cat`/`head` to read files, `sed`/`awk` to edit, or `grep`/`find` to search is discouraged - use the dedicated Read/Edit/Grep/Glob tools instead. Bash is for execution, not for reading, searching, or editing files.

Keep responses concise: state conclusions directly and include only the necessary code snippets.
"""


def build_agent() -> CodeAgent:
    llm = HelloAgentsLLM()
    registry = ToolRegistry()
    for tool in (
        ReadTool(),
        WriteTool(),
        EditTool(),
        BashTool(),
        GrepTool(),
        GlobTool()
    ):
        registry.register_tool(tool)

    config = Config(
        trace_enabled=True,
        skills_enabled=False,
        session_enabled=False,
        devlog_enabled=False,
        todowrite_enabled=False,
        subagent_enabled=False,
    )
    return CodeAgent(
        name="CodingAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=SYSTEM_PROMPT,
        config=config,
        max_steps=25,
        max_run_tokens=0,
    )

def main() -> None:
    agent = build_agent()
    print("=== 编码助手已启动（输入 exit / quit 退出，Ctrl+C 中断）===")
    try:
        while True:
            try:
                text = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见。")
                break
            if not text:
                continue
            if text.lower() in ("exit", "quit"):
                break
            try:
                answer = agent.run(text)
            except Exception as exc:
                print(f"\n[错误] {exc}")
                continue
            print(f"\nAgent> {answer}")
    finally:
        agent.finalize_trace()


if __name__ == "__main__":
    main()
