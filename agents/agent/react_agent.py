"""ReAct Agent - 基于 Function Calling 的实现"""

import json
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncGenerator
from ..core.agent import Agent
from ..core.llm import HelloAgentsLLM
from ..core.config import Config
from ..core.message import Message
from ..core.lifecycle import AgentEvent, EventType, LifecycleHook
from ..core.streaming import StreamEvent, StreamEventType
from ..observability.trace_logger import TraceLogger
from ..tools.registry import ToolRegistry
from ..tools.response import ToolStatus
from ..tools.errors import ToolErrorCode

# 新的系统提示词
DEFAULT_REACT_SYSTEM_PROMPT = """你是一个具备推理和行动能力的 AI 助手。

## 工作流程
你可以通过调用工具来完成任务：

1. **Thought 工具**：用于记录你的推理过程和分析
   - 在需要思考时调用
   - 参数：reasoning（你的推理内容）

2. **业务工具**：用于获取信息或执行操作
   - 根据任务需求选择合适的工具
   - 可以多次调用不同工具

3. **Finish 工具**：用于返回最终答案
   - 当你有足够信息得出结论时调用
   - 参数：answer（最终答案）

## 重要提醒
- 主动使用 Thought 工具记录推理过程
- 可以多次调用工具获取信息
- 只有在确信有足够信息时才调用 Finish
"""


class ReActAgent(Agent):
    """
    ReAct Agent - 基于 Function Calling 的推理与行动

    核心改进：
    - 使用 OpenAI Function Calling（结构化输出）
    - 支持 Thought 工具（显式推理）
    - 支持 Finish 工具（结束流程）
    - 无需正则解析，解析成功率 99%+
    """
    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        tool_registry: Optional[ToolRegistry] = None,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        max_steps: int = 5
    ):
        """
        初始化 ReActAgent

        Args:
            name: Agent 名称
            llm: LLM 实例
            tool_registry: 工具注册表（可选）
            system_prompt: 系统提示词（可选，默认使用 DEFAULT_REACT_SYSTEM_PROMPT）
            config: 配置对象
            max_steps: 最大执行步数
        """
        # 传递 tool_registry 到基类
        super().__init__(
            name,
            llm,
            system_prompt or DEFAULT_REACT_SYSTEM_PROMPT,
            config,
            tool_registry=tool_registry or ToolRegistry()
        )

        self.max_steps = max_steps

        # 内置工具标记（用于特殊处理）
        self._builtin_tools = {"Thought", "Finish"}

    def add_tool(self, tool):
        """添加工具到注册表"""
        self.tool_registry.register_tool(tool)

    def run(self, input_text: str, **kwargs) -> str:
        """
        运行 ReAct Agent

        Args:
            input_text: 用户问题
            **kwargs: 其他参数

        Returns:
            最终答案
        """
        session_start_time = datetime.now()

        try:
            # 执行主逻辑
            final_answer = self._run_impl(input_text, session_start_time, **kwargs)

            # 更新元数据
            self._session_metadata["total_steps"] = getattr(self, "_current_steps", 0)
            self._session_metadata["total_time"] = getattr(self, "_total_tokens", 0)

            return final_answer

        except KeyboardInterrupt:
            # Ctrl+C 时自动保存
            print("\n⚠️ 用户中断，自动保存会话...")
            if self.session_store:
                try:
                    filepath = self.save_session("session-interrupted")
                    print(f"✅ 会话已保存: {filepath}")
                except Exception as e:
                    print(f"❌ 保存失败: {e}")
            raise

        except Exception as e:
            # 错误时也尝试保存
            print(f"\n❌ 发生错误: {e}")
            if self.session_store:
                try:
                    filepath = self.save_session("session-error")
                    print(f"✅ 会话已保存: {filepath}")
                except Exception as save_error:
                    print(f"❌ 保存失败: {save_error}")
            raise

    def _run_impl(self, input_text: str, session_start_time, **kwargs) -> str:
        """
        ReAct Agent 主逻辑实现

        Args:
            input_text: 用户问题
            session_start_time: 会话开始时间
            **kwargs: 其他参数

        Returns:
            最终答案
        """
        # 构建消息列表
        messages = self._build_messages(input_text)

        # 构建工具 schemas（包含内置工具与用户工具）
        tool_schemas = self._build_tool_schemas()

        current_step = 0
        total_tokens = 0

        # 记录用户消息
        if self.trace_logger:
            self.trace_logger.log_event(
                "message_written",
                {"role": "user", "content": input_text}
            )

        print(f"\n🤖 {self.name} 开始处理问题: {input_text}")

        while current_step < self.max_steps:
            current_step += 1
            print(f"\n--- 第 {current_step} 步 ---")

            # 保存当前步数
            self._current_step = current_step

            # 调用 LLM（Function Calling）
            try:
                response = self.llm.invoke_with_tools(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice="auto",
                    **kwargs
                )
            except Exception as e:
                print(f"❌ LLM 调用失败: {e}")
                if self.trace_logger:
                    self.trace_logger.log_event(
                        "error",
                        {"error_type": "LLM_ERROR", "message": str(e)},
                        step=current_step
                    )
                break

            # 获取响应信息
            # response 现在是 LLMToolResponse 对象

            # 累计tokens
            if response.usage:
                total_tokens += response.usage.get("total_tokens", 0)
                self._total_tokens = total_tokens

            # 记录模型输出
            if self.trace_logger:
                self.trace_logger.log_event(
                    "model_output",
                    {
                        "content": response.content or "",
                        "tool_calls": len(response.tool_calls) if response.tool_calls else 0,
                        "usage": {
                            "total_tokens": response.usage.get("total_tokens", 0) if response.usage else 0,
                            "cost": 0.0
                        }
                    },
                    step=current_step
                )

            # 处理工具调用
            tool_calls = response.tool_calls
            if not tool_calls:
                # 没有工具调用，直接返回文本响应
                final_answer = response.content or "抱歉,我无法回答这个问题"
                print(f"💬 直接回复: {final_answer}")

                # 保存到历史记录
                self.add_message(Message(input_text, "user"))
                self.add_message(Message(final_answer, "assistant"))

                if self.trace_logger:
                    duration = (datetime.now() - session_start_time).total_seconds()
                    self.trace_logger.log_event(
                        "session_end",
                        {
                            "duration": duration,
                            "total_steps": current_step,
                            "final_answer": final_answer,
                            "status": "success"
                        }
                    )
                    self.trace_logger.finalize()

                return final_answer

            # 将助手消息添加到历史
            messages.append({
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments
                        }
                    }
                    for tc in tool_calls
                ]
            })

            # 执行所有工具调用
            for tool_call in tool_calls:
                tool_name = tool_call.name
                tool_call_id = tool_call.id

                try:
                    arguments = json.loads(tool_call.arguments)
                except json.JSONDecodeError as e:
                    print(f"❌ 工具参数解析失败: {e}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": f"错误：参数格式不正确 - {str(e)}"
                    })
                    continue

                # 记录工具调用
                if self.trace_logger:
                    self.trace_logger.log_event(
                        "tool_call",
                        {
                            "tool_name": tool_name,
                            "tool_call_id": tool_call_id,
                            "args": arguments
                        },
                        step=current_step
                    )

                # 检查是否是内置工具
                if tool_name in self._builtin_tools:
                    result = self._handle_builtin_tool(tool_name, arguments)
                    print(f"🔧 {tool_name}: {result['content']}")

                    # 记录工具结果
                    if self.trace_logger:
                        self.trace_logger.log_event(
                            "tool_result",
                            {
                                "tool_name": tool_name,
                                "tool_call_id": tool_call_id,
                                "status": "success",
                                "result": result['content']
                            },
                            step=current_step
                        )

                    # 检查是否Finish
                    if tool_name == "Finish" and result.get("finished"):
                        final_answer = result["final_answer"]
                        print(f"🎉 最终答案: {final_answer}")

                        # 保存到历史记录
                        self.add_message(Message(input_text, "user"))
                        self.add_message(Message(final_answer, "assistant"))

                        if self.trace_logger:
                            duration = (datetime.now() - session_start_time).total_seconds()
                            self.trace_logger.log_event(
                                "session_end",
                                {
                                    "duration": duration,
                                    "total_steps": current_step,
                                    "final_answer": final_answer,
                                    "status": "success"
                                }
                            )
                            self.trace_logger.finalize()

                        return final_answer

                    # 添加工具结果到消息
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result['content']
                    })

                else:
                    # 用户工具
                    print(f"🎬 调用工具: {tool_name}({arguments})")

                    # 执行工具(使用基类方法，支持字典参数)
                    result = self._execute_tool_call(tool_name, arguments)

                    # 记录工具结果
                    if self.trace_logger:
                        self.trace_logger.log_event(
                            "tool_result",
                            {
                                "tool_name": tool_name,
                                "tool_call_id": tool_call_id,
                                "result": result
                            },
                            step=current_step
                        )

                    # 检查是否是错误
                    if result.startswith("❌"):
                        print(result)
                    else:
                        print(f"👀 观察: {result}")

                    # 添加工具结果到消息
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result
                    })
        # 达到最大步数
        print("⏰ 已达到最大步数，流程终止。")
        final_answer = "抱歉，我无法在限定步数内完成这个任务。"

        # 保存到历史记录
        self.add_message(Message(input_text, "user"))
        self.add_message(Message(final_answer, "assistant"))

        # 记录会话结束（超时）
        if self.trace_logger:
            duration = (datetime.now() - session_start_time).total_seconds()
            self.trace_logger.log_event(
                "session_end",
                {
                    "duration": duration,
                    "total_steps": current_step,
                    "final_answer": final_answer,
                    "status": "timeout"
                }
            )
            self.trace_logger.finalize()

        return final_answer

    def _build_messages(self, input_text: str) -> List[Dict[str, str]]:
        """构建消息列表"""
        messages = []

        # 添加系统提示词
        if self.system_prompt:
            messages.append({
                "role": "system",
                "content": self.system_prompt
            })

        # 添加用户问题
        messages.append({
            "role": "user",
            "content": input_text
        })

        return messages

    def _build_tool_schemas(self) -> List[Dict[str, Any]]:
        """构建工具 JSON Schema（包含内置工具和用户工具）

        复用基类的 _build_tool_schemas()，并追加 ReAct 内置工具
        """
        schemas = []

        # 1. 添加内置工具：Thought
        schemas.append({
            "type": "function",
            "function": {
                "name": "Thought",
                "description": "分析问题，制定策略，记录推理过程。在需要思考时调用此工具。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reasoning": {
                            "type": "string",
                            "description": "你的推理过程和分析"
                        }
                    },
                    "required": ["reasoning"]
                }
            }
        })

        # 2. 添加内置工具：Finish
        schemas.append({
            "type": "function",
            "function": {
                "name": "Finish",
                "description": "当你有足够信息得出结论时，使用此工具返回最终答案。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {
                       "type": "string",
                            "description": "最终答案"
                        }
                    },
                    "required": ["answer"]
                }
            }
        })

        # 3. 添加用户工具（复用基类方法）
        if self.tool_registry:
            user_tool_schemas = super()._build_tool_schemas()
            schemas.extend(user_tool_schemas)

        return schemas
    
    def _handle_builtin_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """处理内置工具调用"""
        if tool_name == "Thought":
            reasoning = arguments.get("reasoning", "")
            return {
                "content": f"推理: {reasoning}",
                "finished": False
            }
        elif tool_name == "Finish":
            answer = arguments.get("answer", "")
            return {
                "content": f"最终答案: {answer}",
                "finished": True,
                "final_answer": answer
            }
        else:
            return {
                "content": f"未知的内置工具: {tool_name}",
                "finished": False
            }




