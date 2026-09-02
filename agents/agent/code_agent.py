from typing import Optional, Iterator, TYPE_CHECKING, List, Dict, Any, AsyncGenerator, Iterable
import json

from ..core.agent import Agent
from ..core.llm import HelloAgentsLLM
from ..core.config import Config
from ..core.llm_response import LLMStreamChunkType, ToolCall
from ..core.message import Message
from ..core.streaming import StreamEvent, StreamEventType
from ..core.lifecycle import LifecycleHook


DEFAULT_CODEC_SYSTEM_PROMPT = (
    "You are a coding assistant operating in the user's local working directory. "
    "Accomplish tasks by calling tools. Prefer the dedicated tools (Read/Write/Edit/Grep/Glob) "
    "for files; use Bash only to run build/test/lint/install commands or inspect the environment. "
    "Always read relevant files before editing; never guess code. Keep responses concise: state "
    "conclusions directly and include only the necessary code snippets."
)


if TYPE_CHECKING:
    from ..tools.registry import ToolRegistry

class CodeAgent(Agent):
    def __init__(
    self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        tool_registry: Optional['ToolRegistry'] = None,
        max_steps: int = 25,
        max_run_tokens: int = 0,
    ):
        """初始化CodeAgent
        Args:
            name: Agent 名称。
            llm: HelloAgentsLLM 实例。
            tool_registry: 工具注册表（可选）。
            system_prompt: 系统提示词（默认使用 DEFAULT_CODEC_SYSTEM_PROMPT)。
            config: 配置对象（可选）。
            max_steps: 最大交互步数，默认 25。
            max_run_tokens: 单次 run 的 token 预算,0 表示不限（默认 0)。
        """

        super().__init__(
            name,
            llm,
            system_prompt or DEFAULT_CODEC_SYSTEM_PROMPT,
            config,
            tool_registry=tool_registry
        )
        self.max_steps = max_steps
        self.max_run_tokens = max_run_tokens
        self._run_index = 0
        self._last_transcript: List[Dict[str, Any]] = []

    # ------------- 主循环 -------------
    def run(self, input_text: str, **kwargs) -> str:
        """ 运行 CodeAgent（非流式兼容入口，内部消费 stream_run）"""
        final_text = ""
        for ev in self.stream_run(input_text, **kwargs):
            if ev.type == StreamEventType.AGENT_FINISH:
                final_text = ev.data.get("final_text", "")
        return final_text

        
    def stream_run(self, input_text: str, **kwargs) -> Iterator[StreamEvent]:
        """ 流式运行 CodeAgent agentic 循环

        Yields:
            StreamEvent:
            - THINKING:   思考过程增量
            - LLM_CHUNK:  正文增量
            - AGENT_FINISH: 最终结果（data.final_text / data.status）
        """

        from datetime import datetime

        self._run_index += 1
        run_index = self._run_index
        session_started = datetime.now()

        messages = self._build_messages(input_text)
        self._last_transcript = list(messages)
        schemas = self._build_tool_schemas()

        final_text = ""
        status = "success"
        step = 0
        tokens_used = 0

        # ------------ 运行循环 -------------
        while step < self.max_steps:
            step += 1

            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            final_calls: List[ToolCall] = []
            usage = {}

            # 1) 调用 LLM（Function Calling）
            try:
                for chunk in self.llm.stream_invoke_with_tools(
                    messages=messages, tools=schemas, tool_choice="auto", **kwargs
                ):
                    if chunk.type == LLMStreamChunkType.THINKING:
                        reasoning_parts.append(chunk.text)
                        yield StreamEvent.create(StreamEventType.THINKING, self.name, text=chunk.text)
                    elif chunk.type == LLMStreamChunkType.CONTENT:
                        content_parts.append(chunk.text)
                        yield StreamEvent.create(StreamEventType.LLM_CHUNK, self.name, text=chunk.text)
                    elif chunk.type == LLMStreamChunkType.DONE:
                        final_calls = chunk.tool_calls or []
                        usage = chunk.usage or {}
            except Exception as e:
                final_text = f"[出错] LLM 调用失败：{e}"
                status = "error"
                break

            content = "".join(content_parts)
            reasoning_content = "".join(reasoning_parts)
            tokens_used += usage.get("total_tokens", 0) if usage else 0
            self._log("model_output", {
                "content": content,
                "tool_calls": len(final_calls) if final_calls else 0,
                "usage": usage or {},
                "reasoning_content": reasoning_content,
            }, step=step, run=run_index)

            # 2) 自然终止: 没有工具调用，模型输出就是最终答案
            if not final_calls:
                final_text =content or "抱歉, 我无法完成该任务"
                status = "success"
                break

            # 3) 把助手消息追加进上下文
            messages.append(self._assistant_msg(_StreamResponse(content, final_calls)))

            # 4) 顺序执行工具并回填 tool 消息
            for tc in final_calls:
                tool_call_id = tc.id
                tool_name = tc.name
                try:
                    arg = json.loads(tc.arguments)
                except Exception as e:
                    arg = {}
                    result = f"[出错] 工具参数格式错误: {e}"
                    self._log("tool_call", {
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "args": arg
                    }, step=step, run=run_index)
                    self._log("tool_result", {
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "result": result
                    }, step=step, run=run_index)
                    messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})
                    continue

                self._log("tool_call",{
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "args": arg
                }, step=step, run=run_index)
                print(f"Executing tool: {tool_name}")
                result = self._run_and_truncate(tool_name, arg)
                tool = self.tool_registry.get_tool(tool_name)
                print(tool.brief(result))
                print('\n')
                self._log("tool_result", {
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "result": result
                }, step=step, run=run_index)
                messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": result})

            # 5) token 预算检查（步数上限由 while 条件处理）
            if self._budget_exhausted(step, tokens_used):
                final_text = self._conclude(messages, schemas, step, run_index, **kwargs)
                status = "budget"
                break
        else:
            # while 未 break，达到最大步数
            final_text = self._conclude(messages, schemas, step, run_index, **kwargs)
            status = "max_steps"

        # ------------ 运行结束 -------------
        # 保存到历史
        self._persist_history(input_text, final_text)
        self._session_metadata["total_steps"] = step
        self._session_metadata["total_tokens"] = tokens_used
        self._session_metadata["duration_seconds"] = (datetime.now() - session_started).total_seconds()

        self._log("session_end", {
            "duration": (datetime.now() - session_started).total_seconds(),
            "total_steps": step,
            "total_tokens": tokens_used,
            "status": status,
        }, step=step,run=run_index)

        yield StreamEvent.create(
            StreamEventType.AGENT_FINISH,
            self.name,
            final_text=final_text,
            status=status
        )

    # -------------------- 终止兜底（修复残渣 bug） --------------------
    _CONCLUDE_INSTRUCTION = (
        "本轮为最终收尾——可用步数已用尽。请你基于以上全部信息，"
        "用简洁中文给出最终结论或下一步建议。务必只输出普通文本，"
        "不要输出任何工具调用语法（如 <tool_calls>），也不要试图继续调用工具。"
    )

    def _conclude(self, messages, schemas, step, run_index, **kwargs) -> str:
        """在预算耗尽或达到最大步数时，收敛为纯文本最终结论。

        三层防线：
        1. 追加收尾指令（语义约束：告诉模型"为何收尾、收成什么样"）。
        2. 主通道 invoke_with_tools(tool_choice="none")：仍带工具，但结构性禁止
           本次产出 tool_calls，逼出纯文本。
        3. 降级 invoke：无工具/不支持 none 时用普通调用（收尾指令仍在）。

        绝不返回消息列表或原始调用段，最终兜底为确定性提示。
        """
        final_messages = list(messages) + [
            {"role": "user", "content": self._CONCLUDE_INSTRUCTION}
        ]

        # 主通道：保留工具但强制 tool_choice="none"
        if schemas:
            try:
                resp = self.llm.invoke_with_tools(
                    messages=final_messages, tools=schemas,
                    tool_choice="none", **kwargs
                )
                text = (resp.content or "").strip()
                if text:
                    self._log("model_output", {
                        "content": text, "tool_calls": 0, "usage": resp.usage or {},
                    }, step=step + 1, run=run_index)
                    return text
            except Exception:
                pass

        # 降级：无工具/不支持 none 时用普通 invoke
        try:
            resp = self.llm.invoke(final_messages, **kwargs)
            text = (resp.content or "").strip()
            if text:
                self._log("model_output", {
                    "content": text, "tool_calls": 0, "usage": resp.usage or {},
                }, step=step + 1, run=run_index)
                return text
        except Exception:
            pass

        # 确定性兜底
        return f"[已达到最大步数 max_steps={self.max_steps}，请告诉我是否继续。]"


    # -------------------- 工具执行与截断 --------------------
    def _run_and_truncate(self, tool_name: str, args: Dict[str, Any]) -> str:
        """执行工具并对结果做输出截断（复用基类逻辑 + ObservationTruncator）。"""
        raw = self._execute_tool_call(tool_name, args)
        try:
            info = self.truncator.truncate(tool_name, raw)
            if info.get("truncated"):
                return f"{info.get('preview', '')}\n...(输出过长，完整内容见: {info.get('full_output_path')})"
            return info.get("preview", raw)
        except Exception as e:
            return raw

     # -------------------- 预算辅助 --------------------

    def _budget_exhausted(self, step: int, tokens_used: int) -> bool:
        if self.max_run_tokens > 0 and tokens_used >= self.max_run_tokens:
            return True
        return False


    # -------------------- 消息构造 --------------------
    def _build_messages(self, input_text: str) -> List[Dict[str, Any]]:
        """ 构建消息列表 """
        messages: List[Dict[str, Any]] = []
        # 加入系统消息
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        # 加入完整历史信息
        for msg in self.history_manager.get_history():
            messages.append({"role": msg.role, "content": msg.content})
        # 加入用户输入
        messages.append({"role": "user", "content": input_text})
        return messages

    @staticmethod
    def _assistant_msg(response) -> Dict[str, Any]:
        """ 构建助手消息 """
        return{
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
                for tc in response.tool_calls
            ]
        }

    def _persist_history(self, input_text: str, final_text: str) -> None:
            self.add_message(Message(input_text, "user"))
            self.add_message(Message(final_text, "assistant"))

    # -------------------- 可观测性 --------------------
    def _log(self, event: str,  payload: Dict[str, Any], step: Optional[int] = None, run: Optional[int] = None) -> None:
        """记录日志"""
        if not self.trace_logger:
            return

        if run is not None:
            payload = dict(payload)
            payload.setdefault("run_index", run)

        self.trace_logger.log_event(event, payload, step=step)

    def finalize_trace(self) -> None:
        """进程结束时调用一次，写入 HTML footer 并关闭文件流。"""
        if self.trace_logger:
            self.trace_logger.finalize()

class _StreamResponse:
    """流式路径的轻量响应壳，供 _assistant_msg 复用（提供 content / tool_calls）"""
    def __init__(self, content: str, tool_calls: List[ToolCall]):
        self.content = content
        self.tool_calls = tool_calls
