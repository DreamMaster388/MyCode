"""配置管理"""

import os
from typing import Optional, Dict, Any
from pydantic import BaseModel, model_validator

class Config(BaseModel):
    """HelloAgents配置类"""

    # LLM配置
    default_model: str = "deepseek-v4-flash"
    default_provider: str = "openai"
    temperature: float = 0.7
    max_tokens: Optional[int] = None

    # 系统配置
    debug: bool = False
    log_level: str = "INFO"

    # 上下文工程配置
    context_window: int = 128000  # 上下文窗口大小（tokens）
    compression_threshold: float = 0.8  # 压缩阈值（0.8 = 80%时触发压缩）
    context_truncation_enabled: bool = False  # 是否启用上下文裁剪
    context_truncation_threshold: int = 2000  # 触发裁剪的字符数下界
    context_truncation_head_chars: int = 1500  # 保留开头字符数
    context_truncation_tail_chars: int = 500  # 保留结尾字符数
    context_summary_enabled: bool = False  # 是否启用摘要
    context_summary_keep_rounds: int = 10  # 摘要时完整保留最近多少轮
    context_recall_enabled: bool = False  # 是否启用历史召回

    @model_validator(mode="after")
    def _fallback_invalid_truncation_config(self) -> "Config":
        """裁剪参数非法时整组回退默认值，不中断 Config 创建。"""
        if (
            self.context_truncation_threshold <= 0
            or self.context_truncation_head_chars < 0
            or self.context_truncation_tail_chars < 0
            or self.context_truncation_head_chars + self.context_truncation_tail_chars
            > self.context_truncation_threshold
        ):
            self.context_truncation_threshold = 2000
            self.context_truncation_head_chars = 1500
            self.context_truncation_tail_chars = 500
        return self

    @model_validator(mode="after")
    def _fallback_invalid_summary_config(self) -> "Config":
        """摘要保留轮数非法时回退默认值，不中断 Config 创建。"""
        if self.context_summary_keep_rounds < 1:
            self.context_summary_keep_rounds = 10
        return self

    # 可观测性配置
    trace_enabled: bool = True  # 是否启用 Trace 记录
    trace_dir: str = "memory/traces"  # Trace 文件保存目录
    trace_sanitize: bool = True  # 是否脱敏敏感信息
    trace_html_include_raw_response: bool = False  # HTML 是否包含原始响应

    # Skills 知识外化配置
    skills_enabled: bool = True  # 是否启用 Skills 系统
    skills_dir: str = "skills"  # Skills 目录路径
    skills_auto_register: bool = True  # 是否自动注册 SkillTool

    # 熔断器配置
    circuit_enabled: bool = True  # 是否启用熔断器
    circuit_failure_threshold: int = 3  # 连续失败多少次后熔断
    circuit_recovery_timeout: int = 300  # 熔断后恢复时间（秒）

    # 会话持久化配置
    session_enabled: bool = True  # 是否启用会话持久化
    session_dir: str = "memory/sessions"  # 会话文件保存目录
    auto_save_enabled: bool = False  # 是否启用自动保存
    auto_save_interval: int = 10  # 自动保存间隔（每N条消息）

    # 子代理机制配置
    subagent_enabled: bool = True  # 是否启用子代理机制
    subagent_max_steps: int = 15  # 子代理默认最大步数
    subagent_use_light_llm: bool = False  # 是否使用轻量模型（默认关闭，避免破坏现有行为）
    subagent_light_llm_provider: str = "deepseek"  # 轻量模型提供商
    subagent_light_llm_model: str = "deepseek-chat"  # 轻量模型名称

    # TodoWrite 进度管理配置
    todowrite_enabled: bool = True  # 是否启用 TodoWrite 工具
    todowrite_persistence_dir: str = "memory/todos"  # 任务列表持久化目录

    # DevLog 开发日志配置
    devlog_enabled: bool = True  # 是否启用 DevLog 工具
    devlog_persistence_dir: str = "memory/devlogs"  # 开发日志持久化目录

    # 异步生命周期配置
    async_enabled: bool = True  # 是否启用异步执行
    max_concurrent_tools: int = 3  # 最大并发工具数
    hook_timeout_seconds: float = 5.0  # 生命周期钩子超时时间（秒）
    llm_async_timeout: int = 120  # LLM 异步调用超时时间（秒）
    tool_async_timeout: int = 30  # 工具异步调用超时时间（秒）

    # 流式输出配置
    stream_enabled: bool = True  # 是否启用流式输出
    stream_buffer_size: int = 100  # 流式缓冲区大小
    stream_include_thinking: bool = True  # 是否包含思考过程
    stream_include_tool_calls: bool = True  # 是否包含工具调用

    # ── LLM 重试配置 ──
    retry_enabled: bool = True
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0
    retry_backoff_factor: float = 2.0
    retry_jitter: bool = True


    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量创建配置"""
        return cls(
            debug=os.getenv("DEBUG", "false").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            temperature=float(os.getenv("TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("MAX_TOKENS")) if os.getenv("MAX_TOKENS") else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump()