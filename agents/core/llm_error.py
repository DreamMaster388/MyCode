"""LLM错误码与错误信息定义"""

from dataclasses import dataclass, field
from typing import Optional, Set


class LLMErrorCode:
    # LLM统一错误码

    # ── 认证类 ──
    AUTH_INVALID_KEY = "AUTH_INVALID_KEY"        # API Key无效/过期（401）
    AUTH_INSUFFICIENT = "AUTH_INSUFFICIENT"      # 余额不足/配额用完（403/429）

    # ── 限流类 ──
    RATE_LIMITED = "RATE_LIMITED"                # 请求频率超限（429）

    # ── 超时类 ──
    TIMEOUT_CONNECT = "TIMEOUT_CONNECT"          # 连接超时
    TIMEOUT_READ = "TIMEOUT_READ"                # 读取超时
    TIMEOUT_WRITE = "TIMEOUT_WRITE"              # 发送超时

    # ── 服务端类 ──
    SERVER_ERROR = "SERVER_ERROR"                # 5xx（合并500/502/503/504）

    # ── 请求类 ──
    INVALID_REQUEST = "INVALID_REQUEST"          # 400/422（合并原INVALID_PARAMETER）
    CONTEXT_LENGTH_EXCEEDED = "CONTEXT_LENGTH_EXCEEDED"
    INVALID_MODEL = "INVALID_MODEL"              # 模型ID不存在（404）
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"          # 模型暂时不可用（error.code=model_not_available）
    MODEL_OVERLOADED = "MODEL_OVERLOADED"        # 模型负载高
    CONTENT_FILTERED = "CONTENT_FILTERED"        # 内容审核拦截

    # ── 网络类 ──
    NETWORK_ERROR = "NETWORK_ERROR"              # 网络不可达/连接重置
    PROXY_ERROR = "PROXY_ERROR"                  # 代理错误

    # ── 流式类 ──
    STREAM_INTERRUPTED = "STREAM_INTERRUPTED"    # 流式调用中途断开

    # ── 解析类 ──
    RESPONSE_PARSE_ERROR = "RESPONSE_PARSE_ERROR"
    RESPONSE_VALIDATION = "RESPONSE_VALIDATION"  # 响应schema校验失败
    EMPTY_RESPONSE = "EMPTY_RESPONSE"

    # ── 工具调用类 ──
    TOOL_CALL_PARSE_ERROR = "TOOL_CALL_PARSE_ERROR"
    TOOL_CALL_INVALID_ARGS = "TOOL_CALL_INVALID_ARGS"

    # ── 兜底 ──
    UNKNOWN_ERROR = "UNKNOWN_ERROR"

    # 可重试的错误码集合
    _RETRYABLE = {
        RATE_LIMITED,
        TIMEOUT_CONNECT,
        TIMEOUT_READ,
        TIMEOUT_WRITE,
        SERVER_ERROR,
        NETWORK_ERROR,
        MODEL_OVERLOADED,
        STREAM_INTERRUPTED,
        EMPTY_RESPONSE,
    }


    @classmethod
    def retryable_codes(cls) -> set:
        return cls._RETRYABLE

    @classmethod
    def is_retryable(cls, code: str) -> bool:
        return code in cls._RETRYABLE

    @classmethod
    def get_all_codes(cls) -> list:
        return [v for k, v in vars(cls).items()
                if not k.startswith('_') and isinstance(v, str)]

@dataclass
class LLMErrorInfo:
    """附加在响应对象中的错误信息"""
    code: str
    message: str
    retryable: bool = False
    retry_after: Optional[int] = None
    cause: Optional[str] = None

@dataclass
class RetryConfig:
    """重试策略配置"""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retryable_codes: set = field(
        default_factory=lambda: {
            LLMErrorCode.RATE_LIMITED,
            LLMErrorCode.TIMEOUT_CONNECT,
            LLMErrorCode.TIMEOUT_READ,
            LLMErrorCode.TIMEOUT_WRITE,
            LLMErrorCode.SERVER_ERROR,
            LLMErrorCode.NETWORK_ERROR,
            LLMErrorCode.MODEL_OVERLOADED,
            LLMErrorCode.STREAM_INTERRUPTED,
            LLMErrorCode.EMPTY_RESPONSE,
        }
    )



