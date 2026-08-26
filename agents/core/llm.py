import os
import random
import time
from typing import Optional, List, Dict, Iterator, Union

from .llm_error import RetryConfig
from .exceptions import HelloAgentsException, LLMException
from .llm_adapters import BaseLLMAdapter, create_adapter
from .llm_response import LLMResponse, StreamStats, LLMToolResponse


class HelloAgentsLLM:
    """
    HelloAgents统一LLM客户端

    设计理念：
    - 统一配置：只需 LLM_MODEL_ID、LLM_API_KEY、LLM_BASE_URL、LLM_TIMEOUT
    - 自动适配：根据base_url自动选择适配器（OpenAI/Anthropic/Gemini）
    - 统计信息：返回token使用量、耗时等信息，方便日志记录
    - Thinking Model：自动识别并处理推理过程（o1、deepseek-reasoner等）

    支持的接口：
    - OpenAI及所有兼容接口（DeepSeek、Qwen、Kimi、智谱、Ollama等）
    - Anthropic Claude
    - Google Gemini
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        time_out: Optional[int] = None,
        retry_config: Optional[RetryConfig] = None,
        **kwargs
    ):
        """
        初始化LLM客户端

        参数优先级：传入参数 > 环境变量

        Args:
            model: 模型名称，默认从 LLM_MODEL_ID 读取
            api_key: API密钥，默认从 LLM_API_KEY 读取
            base_url: 服务地址，默认从 LLM_BASE_URL 读取
            temperature: 温度参数，默认0.7
            max_tokens: 最大token数
            timeout: 超时时间（秒），默认从 LLM_TIMEOUT 读取，默认60秒
        """
        self.model = model or os.getenv("LLM_MODEL_ID")
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.time_out = time_out or int(os.getenv("LLM_TIMEOUT", "60"))
        self.kwargs = kwargs
        self.retry_config = retry_config or RetryConfig()

        # 验证必要参数
        if not self.model:
            raise HelloAgentsException("必须提供模型名称（model参数或LLM_MODEL_ID环境变量）")
        if not self.api_key:
            raise HelloAgentsException("必须提供API密钥（api_key参数或LLM_API_KEY环境变量）")
        if not self.base_url:
            raise HelloAgentsException("必须提供服务地址（base_url参数或LLM_BASE_URL环境变量）")

        # 创建适配器（自动检测）
        self._adapter: BaseLLMAdapter = create_adapter(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.time_out,
            model=self.model
        )

        # 最后一次调用的统计信息（用于流式调用）
        self.last_call_status: Optional[StreamStats] = None

    def think(self, messages: List[Dict[str, str]], temperature: Optional[float] = None) -> Iterator[str]:
        """
        调用大语言模型进行思考，并返回流式响应。
        这是主要的调用方法，默认使用流式响应以获得更好的用户体验。

        Args:
            messages: 消息列表
            temperature: 温度参数，如果未提供则使用初始化时的值

        Yields:
            str: 流式响应的文本片段

        Note:
            流式调用结束后，可通过 llm.last_call_stats 获取统计信息
        """

        print(f"🧠 正在调用 {self.model} 模型...")

        # 准备参数
        kwargs = {
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens

        try:
            print("✅ 大语言模型响应成功:")
            for chunk in self._adapter.stream_invoke(messages, **kwargs):
                # print是控制台展示，yield是后续调用，两者不冲突
                print(chunk, end="", flush=True)
                yield chunk
            print() # 换行

            # 保留统计信息
            if hasattr(self._adapter, "last_stats"):
                self.last_call_stats = self._adapter.last_stats

        except Exception as e:
            print(f"❌ 调用LLM API时发生错误: {e}")
            raise

    def invoke(self, messages: List[Dict[str, str]], **kwargs) -> LLMResponse:
        """
        非流式调用LLM，返回完整响应对象。

        Args:
            messages: 消息列表
            **kwargs: 其他参数（temperature, max_tokens等）

        Returns:
            LLMResponse: 包含内容、统计信息、推理过程（thinking model）的响应对象

        Example:
            response = llm.invoke([{"role": "user", "content": "你好"}])
            print(response.content)  # 回复内容
            print(response.usage)    # token使用量
            print(response.latency_ms)  # 耗时
            if response.reasoning_content:  # thinking model的推理过程
                print(response.reasoning_content)
        """

        # 合并参数,显式声明 temperature 和 max_tokens 两个常用参数
        call_kwargs = {
            "temperature": kwargs.pop("temperature", self.temperature),
        }
        if self.max_tokens:
            call_kwargs["max_tokens"] = kwargs.pop("max_tokens", self.max_tokens)
        call_kwargs.update(kwargs)

        return self._retry_with_back_off(self._adapter.invoke, messages, **call_kwargs)

    def stream_invoke(self, messages: List[Dict[str, str]], **kwargs) -> Iterator[str]:
        """
        流式调用LLM的别名方法，与think方法功能相同。
        保持向后兼容性。

        Args:
            messages: 消息列表
            **kwargs: 其他参数

        Yields:
            str: 流式响应的文本片段

        Note:
            流式调用结束后，可通过 llm.last_call_stats 获取统计信息
        """
        temperature = kwargs.pop("temperature", self.temperature)

        # 准备参数
        call_kwargs = {}
        if self.max_tokens:
            call_kwargs["max_tokens"] = kwargs.pop("max_tokens", self.max_tokens)
        call_kwargs.update(kwargs)

        for chunk in self._adapter.stream_invoke(messages, temperature=temperature, **kwargs):
            yield chunk

        # 保存统计信息
        if hasattr(self._adapter, "last_stats"):
            self.last_call_stats = self._adapter.last_stats

    def invoke_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        tool_choice: Union[str, dict] = "auto",
        **kwargs
    ) -> LLMToolResponse:
        """
        调用 LLM 并支持工具调用（Function Calling）

        这是支持 OpenAI Function Calling 的核心方法，用于结构化工具调用。

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            tools: 工具 schema 列表，格式为 OpenAI Function Calling 规范
            tool_choice: 工具选择策略
                - "auto": 让模型自动决定是否调用工具（默认）
                - "none": 强制不调用工具
                - "required": 强制调用工具
                - {"type": "function", "function": {"name": "tool_name"}}: 强制调用指定工具
            **kwargs: 其他参数（temperature, max_tokens 等）

        Returns:
            统一的工具调用响应对象 (LLMToolResponse)

        Raises:
            HelloAgentsException: 当 LLM 调用失败时
        """
        # 合并参数
        call_kwargs = {
            "temperature": kwargs.pop("temperature", self.temperature),
            "tool_choice": tool_choice,
        }
        if self.max_tokens:
            call_kwargs["max_tokens"] = kwargs.pop("max_tokens", self.max_tokens)
        call_kwargs.update(kwargs)

        return self._retry_with_back_off(self._adapter.invoke_with_tools, messages, tools, **call_kwargs)

    def _retry_with_back_off(self, fn, *args, **kwargs):
        cfg = self.retry_config
        for attempt in range(cfg.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except LLMException as e:
                if not e.retryable:
                    raise
                if attempt == cfg.max_retries:
                    raise
                if e.retry_after is not None:
                    delay = min(e.retry_after, cfg.max_delay)
                else:
                    delay = min(
                        cfg.base_delay * (cfg.backoff_factor ** attempt),
                        cfg.max_delay,
                    )
                    if cfg.jitter:
                        delay *= 0.5 + random.random() * 0.5
                print(f"↻ LLM 调用失败 ({e.error_code})，第 {attempt + 1}/{cfg.max_retries} 次重试，等待 {delay:.1f}s...")
                time.sleep(delay)
            except Exception:
                raise


if __name__ == "__main__":
    agent = HelloAgentsLLM()
