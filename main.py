import json
from openai import OpenAI  # 假设使用OpenAI格式的API，或兼容Anthropic

client = OpenAI(base_url="https://api.deepseek.com", api_key="sk-1895391e5899440098fc83ccbf6b687a")

# 1. 定义最简单的两个工具Schema
tools = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "执行只读安全的系统shell命令。包括ls、grep、cat等，禁止破坏性操作，只能当前目录下进行操作",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取项目中的文本文件内容",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}
        }
    }
]

# 系统提示词（极简版，告诉AI它是编程助手）
system_prompt = "你是一个编程助手。获取信息时请调用工具。解决问题后，直接输出最终答案。"

# 初始化消息历史
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": "请帮我查看当前目录下有哪些文件，并读出最大的那个文件的前10行。"}
]

# 2. 核心无限循环（这就是 Claude Code 最底层的闭环）
max_iterations = 10  # 防止死循环
while max_iterations > 0:
    max_iterations -= 1

    # --- 步骤A：思考 (Think) ---
    response = client.chat.completions.create(
        model="deepseek-v4-flash",  # 或 claude-3
        messages=messages,
        tools=tools,
        tool_choice="auto"  # 让AI自己决定是否调用工具
    )

    # 获取模型回复
    assistant_message = response.choices[0].message
    messages.append(assistant_message)  # 将思考结果存入历史

    # --- 步骤B：检查是否有工具调用请求 ---
    if assistant_message.tool_calls is None:
        # 没有工具调用，说明AI认为任务已完成，直接输出最终结果，闭环结束
        print("【完成】", assistant_message.content)
        break

    # --- 步骤C：行动 (Act) & 观察 (Observe) ---
    # 遍历AI请求的所有工具
    for tool_call in assistant_message.tool_calls:
        tool_name = tool_call.function.name
        arguments = json.loads(tool_call.function.arguments)

        # 执行具体工具
        if tool_name == "bash":
            import subprocess

            try:
                result = subprocess.check_output(
                    arguments["command"], shell=True, text=True, stderr=subprocess.STDOUT
                )
            except Exception as e:
                result = f"命令执行失败: {e}"

        elif tool_name == "read_file":
            try:
                with open(arguments["path"], "r", encoding="utf-8") as f:
                    result = f.read()
            except Exception as e:
                result = f"读取文件失败: {e}"

        else:
            result = "未知工具"

        # --- 将观察（Observe）结果返回给AI ---
        # 注意：工具结果必须以 tool 角色返回，并关联上对应的 tool_call_id
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": result  # 把命令的输出或文件内容塞回给模型
        })

        # 将结果打印在终端，方便人类观察闭环过程
        print(f"【工具执行】{tool_name}: {arguments}")
        print(f"【观察结果】{result[:200]}...")

        # 此时循环进入下一轮，AI会基于刚刚的观察结果，再次决定是继续调用工具，还是输出最终答案。
