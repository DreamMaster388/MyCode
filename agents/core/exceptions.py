"""异常体系"""
from typing import Optional

from .llm_error import LLMErrorCode

class HelloAgentsException(Exception):
    """HelloAgents基础异常类"""
    pass

class LLMException(HelloAgentsException):
    def __init__(self, message, error_code=LLMErrorCode.UNKNOWN_ERROR,
                 retry_after=None, cause=None):
        super().__init__(message)
        self.error_code = error_code
        self.retry_after = retry_after
        self.cause = cause

    @property
    def retryable(self) -> bool:
        return LLMErrorCode.is_retryable(self.error_code)

class LLMAuthException(LLMException):
    def __init__(self, message: str,
                 error_code: str = LLMErrorCode.AUTH_INVALID_KEY,
                 retry_after: Optional[int] = None,
                 cause: Optional[Exception] = None):
        super().__init__(message, error_code, retry_after=retry_after, cause=cause)


class LLMRateLimitException(LLMException):
    def __init__(self, message: str,
                 error_code: str = LLMErrorCode.RATE_LIMITED,
                 retry_after: Optional[int] = None,
                 cause: Optional[Exception] = None):
        super().__init__(message, error_code, retry_after=retry_after, cause=cause)


class LLMTimeoutException(LLMException):
    def __init__(self, message: str,
                 error_code: str = LLMErrorCode.TIMEOUT_READ,
                 retry_after: Optional[int] = None,
                 cause: Optional[Exception] = None):
        super().__init__(message, error_code, retry_after=retry_after, cause=cause)


class LLMServerException(LLMException):
    def __init__(self, message: str,
                 error_code: str = LLMErrorCode.SERVER_ERROR,
                 retry_after: Optional[int] = None,
                 cause: Optional[Exception] = None):
        super().__init__(message, error_code, retry_after=retry_after, cause=cause)


class LLMContextExceededException(LLMException):
    def __init__(self, message: str,
                 error_code: str = LLMErrorCode.CONTEXT_LENGTH_EXCEEDED,
                 retry_after: Optional[int] = None,
                 cause: Optional[Exception] = None):
        super().__init__(message, error_code, retry_after=retry_after, cause=cause)


class LLMContentFilteredException(LLMException):
    def __init__(self, message: str,
                 error_code: str = LLMErrorCode.CONTENT_FILTERED,
                 retry_after: Optional[int] = None,
                 cause: Optional[Exception] = None):
        super().__init__(message, error_code, retry_after=retry_after, cause=cause)




class AgentException(HelloAgentsException):
    """Agent相关异常"""
    pass

class ConfigException(HelloAgentsException):
    """配置相关异常"""
    pass

class ToolException(HelloAgentsException):
    """工具相关异常"""
    pass

