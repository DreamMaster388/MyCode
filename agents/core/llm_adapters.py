"""LLM适配器 - 支持OpenAI、Anthropic、Gemini等不同接口格式"""
from abc import ABC, abstractmethod
import time
from typing import Optional, Any, List, Dict, Iterable, Union

from openai import APIStatusError

from .exceptions import *
from .llm_error import LLMErrorCode
from .llm_response import LLMResponse, StreamStats, LLMToolResponse, ToolCall, LLMStreamChunk, LLMStreamChunkType


class BaseLLMAdapter(ABC):
    """LLM适配器基类"""

    def __init__(self, api_key: str, base_url: str, timeout: int, model:str):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.model = model
        self._client = None
        self._async_client = None

    @abstractmethod
    def create_client(self) -> Any:
        """ 创建客户端实例 """
        pass

    def create_async_client(self) -> Any:
        """ 创建异步客户端实例(子类可选实现)"""
        return None

    @abstractmethod
    def invoke(self, message: List[Dict], **kwargs) -> LLMResponse:
        """ 非流式调用 """
        # invoke 一次性返回完整结果，包括content+tool_calls+usage统计+耗时等元数据，消费者一次性拿到完整结果

    @abstractmethod
    def stream_invoke(self, message: List[Dict], **kwargs) -> Iterable[str]:
        """ 流式调用，返回生成器 """
        # stream_invoke 只能陆续返回字符串，消费者只能边拿数据边消费，结束时才能拿到完整结果

    async def async_stream_invoke(self, message: List[Dict], **kwargs) -> Iterable[str]:
        """ 异步流式调用(子类可以实现真正的异步) """
        pass

    @abstractmethod
    def invoke_with_tools(self, message: List[Dict], tools: List[Dict], **kwargs) -> LLMToolResponse:
        """ 工具调用（Function Calling）"""
        pass

    def stream_invoke_with_tools(self, message: List[Dict], tools: List[Dict], **kwargs) -> Iterable[LLMStreamChunk]:
        """ 流式调用并支持工具调用（Function Calling），子类应实现 """
        raise NotImplementedError

    def _is_thinking_model(self, model_name: str) -> bool:
        """ 判断是否为thinking model """
        thinking_keywords = ["reasoner", "o1", "o3","thinking"]
        model_lower = model_name.lower()
        return any(keyword in model_lower for keyword in thinking_keywords)

    def _map_error(self, e: Exception) -> LLMException:
        """将 SDK 异常映射为统一 LLMException（子类可覆盖）"""
        return LLMException(str(e), LLMErrorCode.UNKNOWN_ERROR, cause=e)

class OpenAIAdapter(BaseLLMAdapter):
    """OpenAI兼容接口适配器（默认）

    支持：
    - OpenAI官方API
    - 所有OpenAI兼容接口（DeepSeek、Qwen、Kimi、智谱等）
    - Thinking Models（o1、deepseek-reasoner等）
    """

    def create_client(self) -> Any:
        """ 创建openai客户端 """
        from openai import OpenAI

        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout
        )

    def create_async_client(self) -> Any:
        """ 创建OpenAI异步客户端 """
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout
        )

    def invoke(self, messages: List[Dict], **kwargs) -> LLMResponse:
        """ 非流式调用 """
        if not self._client:
            self._client = self.create_client()

        start_time = time.time()

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # 提取内容和推理过程
            choice = response.choices[0]
            content = choice.message.content or ""
            reasoning_content = None

            #Thinking model特殊处理
            if self._is_thinking_model(self.model):
                # OpenAI o1系列：reasoning_content在message中
                if hasattr(choice.message, 'reasoning_content'):
                    reasoning_content = choice.message.reasoning_content
                # DeepSeek reasoner：可能在其他字段
                elif hasattr(choice, 'reasoning_content'):
                    reasoning_content = choice.reasoning_content

            # 提取Usage信息
            usage = {}
            if hasattr(response, 'usage'):
                usage = {
                    'prompt_tokens': response.usage.prompt_tokens,
                    'completion_tokens': response.usage.completion_tokens,
                    'total_tokens': response.usage.total_tokens,
                }

            return LLMResponse(
                content=content,
                model=self.model,
                usage=usage,
                latency_ms=latency_ms,
                reasoning_content=reasoning_content
            )

        except LLMException:
            raise  # 已经是统一异常，直接透传
        except Exception as e:
            raise self._map_error(e)

    def stream_invoke(self, messages: List[Dict], **kwargs) -> Iterable[str]:
        """ 流式调用 """
        if not self._client:
            self._client = self.create_client()

        start_time = time.time()

        try:
            # 开启流式后，会返回一个迭代器，需要遍历拿到里面的内容
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                **kwargs
            )

            collected_content = []
            reasoning_content = None
            usage = {}

            for chunk in response:
                # choices这个属性未必存在，以None为默认值避免流中断
                choices = getattr(chunk, "choices", None)
                if choices:
                    delta = getattr(choices[0], "delta", None)
                    if delta is not None:
                        # 提取内容
                        content = getattr(delta, "content", None)
                        if content:
                            collected_content.append(content)
                            # yield 阶段性return，stream_invoke调用时，每生成一个chunk，就供消费者消费
                            yield content

                        # Thinking model的推理过程
                        if self._is_thinking_model(model_name=self.model):
                            reasoning_delta = getattr(delta, "reasoning_content", None)
                            if reasoning_delta:
                                if reasoning_content is None:
                                    reasoning_content = ""
                                reasoning_content += reasoning_delta

                # 提取usage(流式最后一个chunk可能包含)
                if hasattr(chunk, 'usage') and chunk.usage:
                    usage = {
                        'prompt_tokens': chunk.usage.prompt_tokens,
                        'completion_tokens': chunk.usage.completion_tokens,
                        'total_tokens': chunk.usage.total_tokens,
                    }

            latency_ms = int((time.time() - start_time) * 1000)

            # 返回统计信息
            self.last_status = StreamStats(
                model=self.model,
                usage=usage,
                latency_ms=latency_ms,
                reasoning_content=reasoning_content
            )

        except LLMException:
            raise  # 已经是统一异常，直接透传
        except Exception as e:
            raise self._map_error(e)

    def invoke_with_tools(self, messages: List[Dict], tools: List[str],
                          tool_choice: Union[str, Dict] = "auto", **kwargs) -> LLMToolResponse:
        """ 工具调用(Function Calling) """
        if not self._client:
            self._client = self.create_client()

        start_time = time.time()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                **kwargs
            )

            latency_ms = int((time.time() - start_time) * 1000)
            message = response.choices[0].message

            tool_calls = []

            if message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append(
                        ToolCall(
                            id = tc.id,
                            name=tc.function.name,
                            arguments = tc.function.arguments
                        )
                    )
            usage = {}
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                }

            return LLMToolResponse(
                content=message.content,
                tool_calls=tool_calls,
                model=response.model,
                usage=usage,
                latency_ms=latency_ms
            )
        except LLMException:
            raise  # 已经是统一异常，直接透传
        except Exception as e:
            raise self._map_error(e)

    def stream_invoke_with_tools(self, messages: List[Dict], tools: List[Dict],
                                  tool_choice: Union[str, Dict] = "auto", **kwargs) -> Iterable[LLMStreamChunk]:
        """ 流式调用并支持工具调用（Function Calling）
        
        以 LLMStreamChunk 切片流式返回：
        - THINKING: 思考过程增量
        - CONTENT:  正文增量
        - TOOL_CALL: 工具调用增量
        - DONE:     一轮结束（携带完整 tool_calls / usage / 累计 reasoning）
        """
        if not self._client:
            self._client = self.create_client()

        start_time = time.time()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                stream=True,
                **kwargs
            )

            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            tool_index: Dict[int, Dict[str, str]] = {}  # index -> {id, name, arguments}
            usage: Dict[str, int] = {}
            finish_reason: Optional[str] = None
            is_thinking = self._is_thinking_model(self.model)

            for chunk in response:
                choices = getattr(chunk, "choices", None)
                if choices:
                    delta = getattr(choices[0], "delta", None)
                    fc = getattr(choices[0], "finish_reason", None)
                    if fc:
                        finish_reason = fc
                    if delta is not None:
                        # 思考过程（thinking model）
                        if is_thinking:
                            thinking = getattr(delta, "reasoning_content", None)
                            if thinking:
                                reasoning_parts.append(thinking)
                                yield LLMStreamChunk(
                                    type=LLMStreamChunkType.THINKING,
                                    text=thinking
                                )
                        # 正文
                        content = getattr(delta, "content", None)
                        if content:
                            content_parts.append(content)
                            yield LLMStreamChunk(
                                type=LLMStreamChunkType.CONTENT,
                                text=content
                            )
                        # 工具调用（OpenAI 流式按 index 分段拼接）
                        tool_calls = getattr(delta, "tool_calls", None)
                        if tool_calls:
                            for tc in tool_calls:
                                slot = tool_index.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                                slot["id"] = tc.id
                                if tc.function:
                                    if tc.function.name:
                                        slot["name"] = tc.function.name
                                    if tc.function.arguments:
                                        slot["arguments"] += tc.function.arguments
                            yield LLMStreamChunk(
                                type=LLMStreamChunkType.TOOL_CALL,
                                tool_calls=[
                                    ToolCall(
                                        id=slot["id"],
                                        name=slot["name"],
                                        arguments=s["arguments"]
                                    ) for s in tool_index.values()
                                ]
                            )
                # usage 一般在最后 chunk 携带
                if hasattr(chunk, 'usage') and chunk.usage:
                    usage = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens
                    }

            final_tool_calls = [
                ToolCall(id=s["id"], name=s["name"], arguments=s["arguments"])
                for _, s in sorted(tool_index.items())
            ]
            reasoning_content = "".join(reasoning_parts) if reasoning_parts else None

            latency_ms = int((time.time() - start_time) * 1000)
            self.last_status = StreamStats(
                model=self.model,
                usage=usage,
                latency_ms=latency_ms,
                reasoning_content=reasoning_content
            )
            yield LLMStreamChunk(
                type=LLMStreamChunkType.DONE,
                text="".join(content_parts),
                tool_calls=final_tool_calls,
                usage=usage,
                reasoning_content=reasoning_content,
                finish_reason=finish_reason
            )

        except LLMException:
            raise  # 已经是统一异常，直接透传
        except Exception as e:
            raise self._map_error(e)
            
    def _map_error(self, e: Exception) -> LLMException:
        """将 OpenAI SDK 异常映射为统一 LLMException"""
        from openai import (
            AuthenticationError, RateLimitError,
            APITimeoutError, APIConnectionError,
            InternalServerError, BadRequestError,
            NotFoundError, PermissionDeniedError,
            ConflictError, UnprocessableEntityError,
            ContentFilterFinishReasonError,
            LengthFinishReasonError,
            APIResponseValidationError,
            APIStatusError,
        )

        retry_after = self._extract_retry_after(e)

        # ── 超时（可重试） ──
        if isinstance(e, APITimeoutError):
            return LLMTimeoutException(str(e), LLMErrorCode.TIMEOUT_READ,
                                       retry_after=retry_after, cause=e)

        # ── 连接错误（可重试） ──
        if isinstance(e, APIConnectionError):
            return LLMException(str(e), LLMErrorCode.NETWORK_ERROR,
                                retry_after=retry_after, cause=e)

        # ── 认证（不可重试） ──
        if isinstance(e, AuthenticationError):
            return LLMAuthException(str(e), LLMErrorCode.AUTH_INVALID_KEY,
                                    retry_after=retry_after, cause=e)

        # ── 权限（不可重试） ──
        if isinstance(e, PermissionDeniedError):
            return LLMAuthException(str(e), LLMErrorCode.AUTH_INSUFFICIENT,
                                    retry_after=retry_after, cause=e)

        # ── 限流（可重试） ──
        if isinstance(e, RateLimitError):
            return LLMRateLimitException(str(e), LLMErrorCode.RATE_LIMITED,
                                         retry_after=retry_after, cause=e)

        # ── 内容审核拦截（不可重试） ──
        if isinstance(e, ContentFilterFinishReasonError):
            return LLMContentFilteredException(str(e), LLMErrorCode.CONTENT_FILTERED,
                                               cause=e)

        # ── Context 超长（不可重试） ──
        if isinstance(e, LengthFinishReasonError):
            return LLMContextExceededException(str(e), LLMErrorCode.CONTEXT_LENGTH_EXCEEDED,
                                               cause=e)

        # ── 响应校验失败（不可重试） ──
        if isinstance(e, APIResponseValidationError):
            return LLMException(str(e), LLMErrorCode.RESPONSE_VALIDATION,
                                retry_after=retry_after, cause=e)

        # ── 4xx/5xx 带状态码 ──
        if isinstance(e, APIStatusError):
            return self._map_status_error(e, retry_after)

        # ── 兜底 ──
        return LLMException(str(e), LLMErrorCode.UNKNOWN_ERROR, cause=e)

    def _map_status_error(self, e: "APIStatusError", retry_after: Optional[int]) -> LLMException:
        """精确映射 4xx/5xx 状态码错误"""
        from openai import InternalServerError, NotFoundError, BadRequestError

        # 5xx
        if isinstance(e, InternalServerError):
            return LLMServerException(str(e), LLMErrorCode.SERVER_ERROR,
                                      retry_after=retry_after, cause=e)

        # 404 — 判断是否与模型相关
        if isinstance(e, NotFoundError):
            # 尝试从响应 body 中提取 error code
            if self._is_model_error(e):
                return LLMException(str(e), LLMErrorCode.MODEL_NOT_FOUND,
                                    retry_after=retry_after, cause=e)
            return LLMException(str(e), LLMErrorCode.INVALID_MODEL,
                                retry_after=retry_after, cause=e)

        # 400 (BadRequestError)、409 (ConflictError)、422 (UnprocessableEntityError) 等
        # 全部归为 INVALID_REQUEST — 都是客户端不可重试的请求错误
        return LLMException(str(e), LLMErrorCode.INVALID_REQUEST,
                            retry_after=retry_after, cause=e)

    def _extract_retry_after(self, e: Exception) -> Optional[int]:
        """从API响应中提取重试等待时间"""
        from openai import APIStatusError
        if not isinstance(e, APIStatusError):
            return None

        # 1. Retry-After 响应头（标准 HTTP，单位秒）
        retry_after = e.response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return int(retry_after)
            except (ValueError, TypeError):
                pass

        # 2. 响应体中的 retry_after 字段
        #    OpenAI 格式: {"error": {"code": "...", "model": "...", "retry_after": 10}}
        body = e.body
        if isinstance(body, dict):
            error_obj = body.get("error")
            if isinstance(error_obj, dict):
                ra = error_obj.get("retry_after")
                if ra is not None:
                    try:
                        return int(ra)
                    except (ValueError, TypeError):
                        pass

            ra = body.get("retry_after")
            if ra is not None:
                try:
                    return int(ra)
                except (ValueError, TypeError):
                    pass

        return None

    def _is_model_error(self, e: "APIStatusError") -> bool:
        """判断 404 是否与模型相关"""
        body = e.body
        if not isinstance(body, dict):
            return False
        error_obj = body.get("error")
        if not isinstance(error_obj, dict):
            return False
        code = error_obj.get("code", "")
        if isinstance(code, str):
            return "model" in code.lower()
        return False


def create_adapter(
    api_key: str,
    base_url: str,
    timeout: int,
    model: str
)-> BaseLLMAdapter:

    """
    根据base_url自动选择适配器
    检测逻辑：
    - anthropic.com -> AnthropicAdapter
    - googleapis.com 或 generativelanguage -> GeminiAdapter
    - 其他 -> OpenAIAdapter（默认）
    """

    if base_url:
        base_url_lower = base_url.lower()

        if "anthropic.com" in base_url_lower:
            pass

        if "googleapis.com" in base_url_lower or "generativelanguage" in base_url_lower:
            pass

    # 默认使用OpenAI适配器（兼容所有OpenAI格式接口）
    return OpenAIAdapter(api_key, base_url, timeout, model)