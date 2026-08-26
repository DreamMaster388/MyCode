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
from agents.agent.simple_agent import SimpleAgent
from agents.tools.registry import ToolRegistry
from agents.tools.builtin import ReadTool, WriteTool, EditTool


SYSTEM_PROMPT = """你是一个编程助手，运行在用户的本地工作目录中。

你可以通过以下工具完成任务：
- Read：读取文件内容，或列出目录内容
- Write：创建/覆盖文件（带冲突检测与备份）
- Edit：精确替换文件中的内容（带冲突检测与备份）

工作准则：
1. 动手前先用 Read 了解相关文件，不要凭空猜测代码。
2. 修改文件优先用 Edit（精确替换），仅在新建文件时才用 Write。
3. 保持回答简洁，直接给出结论与必要的代码片段。
"""


def build_agent() -> SimpleAgent:
    """构造编码助手 Agent（不发起任何网络请求）。"""
    llm = HelloAgentsLLM()
    registry = ToolRegistry()
    for tool in (
        ReadTool(project_root="."),
        WriteTool(project_root="."),
        EditTool(project_root="."),
    ):
        registry.register_tool(tool)

    config = Config(
        trace_enabled=False,
        skills_enabled=False,
        session_enabled=False,
        devlog_enabled=False,
        todowrite_enabled=False,
    )
    return SimpleAgent(
        name="CodingAgent",
        llm=llm,
        tool_registry=registry,
        system_prompt=SYSTEM_PROMPT,
        config=config,
    )


def main() -> None:
    agent = build_agent()
    print("=== 编码助手已启动（输入 exit / quit 退出，Ctrl+C 中断）===")
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
        except Exception as exc:  # 单轮出错不退出，继续对话
            print(f"\n[错误] {exc}")
            continue
        print(f"\nAgent> {answer}")


if __name__ == "__main__":
    main()
