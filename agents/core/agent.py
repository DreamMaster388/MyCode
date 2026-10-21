from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, Union, TYPE_CHECKING, AsyncGenerator
import asyncio
from .message import Message
from .llm import HelloAgentsLLM
from .config import Config
from .lifecycle import AgentEvent, EventType, LifecycleHook, ExecutionContext
from .mode import AgentMode, ModeGuard

if TYPE_CHECKING:
    from ..tools.registry import ToolRegistry
    from ..observability.trace_logger import TraceLogger
    from ..tools.tool_filter import ToolFilter

class Agent(ABC):
    """Agent基类

    集成能力：
    - HistoryManager: 历史管理与 token 计量
    - ObservationTruncator: 工具输出截断
    - TraceLogger: 可观测性（JSONL + HTML）
    - ToolRegistry: 工具管理（可选）
    - SkillLoader: 知识外化（可选）

    向后兼容：
    - self._history 属性仍然可用（通过 property 代理）
    - add_message/clear_history/get_history 方法保持不变
    """

    def __init__(
        self,
        name: str,
        llm: HelloAgentsLLM,
        system_prompt: Optional[str] = None,
        config: Optional[Config] = None,
        tool_registry: Optional['ToolRegistry'] = None,
        mode_guard: Optional['ModeGuard'] = None
    ):
        self.name = name
        self.llm = llm
        self.system_prompt = system_prompt
        self.config = config or Config()

        # 工具注册表(可选)
        self.tool_registry = tool_registry

        # 执行入口守卫: 未显式提供时默认 build; 要与工具共用同一 policy 请显式传入。
        self.mode_guard = mode_guard or ModeGuard()

        # 新增：上下文工程组件
        from ..context.history import HistoryManager
        from ..context.truncator import ObservationTruncator

        self.history_manager = HistoryManager(
            min_retain_rounds=self.config.min_retain_rounds,
            compression_threshold=self.config.compression_threshold,
            context_window=self.config.context_window,
            config=self.config,
            llm=self.llm
        )

        self.truncator = ObservationTruncator(
            max_lines=self.config.tool_output_max_lines,
            max_bytes=self.config.tool_output_max_bytes,
            truncate_direction=self.config.tool_output_truncate_direction,
            output_dir=self.config.tool_output_dir
        )

        # 新增：可观测性组件
        from ..observability.trace_logger import TraceLogger
        self.trace_logger: Optional[TraceLogger] = None
        if self.config.trace_enabled:
            self.trace_logger = TraceLogger(
                output_dir=self.config.trace_dir,
                sanitize=self.config.trace_sanitize,
                html_include_raw_response=self.config.trace_html_include_raw_response
            )
            #记录会话开始
            self.trace_logger.log_event(
                "session_start",
                {
                    "agent_name": self.name,
                    "agent_type": self.__class__.__name__,
                    "config": self.config.model_dump()
                }
            )

        # 新增：Skills 知识外化组件
        from pathlib import Path
        from ..skill.loader import SkillLoader

        self.skill_loader: Optional[SkillLoader] = None
        if self.config.skills_enabled:
            skills_path = Path(self.config.skills_dir)
            self.skill_loader = SkillLoader(skills_dir=skills_path)

            # 自动注册 SkillTool
            if self.config.skills_auto_register and self.tool_registry:
                from ..tools.builtin.skill_tool import SkillTool
                skill_tool = SkillTool(skill_loader=self.skill_loader)
                self.tool_registry.register_tool(skill_tool)

        # 新增：会话持久化组件
        from datetime import datetime
        from .session_store import SessionStore

        self.session_store: Optional[SessionStore] = None
        if self.config.session_enabled:
            self.session_store = SessionStore(self.config.session_dir)

        # 会话元数据（用于保存）
        self._session_metadata = {
            "created_at": datetime.now().isoformat(),
            "total_tokens": 0,
            "total_steps": 0,
            "duration_seconds": 0
        }

        self._start_time = datetime.now()

        # 新增：子代理机制组件
        if self.config.subagent_enabled and self.tool_registry:
            self._register_task_tool()

        # 新增：TodoWrite 进度管理组件
        if self.config.todowrite_enabled and self.tool_registry:
            self._register_todowrite_tool()

        # 新增：DevLog 开发日志组件
        if self.config.devlog_enabled and self.tool_registry:
            self._register_devlog_tool()

    def set_mode(self, mode: AgentMode) -> None:
        """切换运行模式(plan/build)。"""
        self.mode_guard.set_mode(mode)

    @property
    def _history(self) -> List[Message]:
        """向后兼容：通过 property 代理到 HistoryManager"""
        return self.history_manager.get_history()

    @_history.setter
    def _history(self, value: List[Message]):
        """向后兼容：允许直接设置历史"""
        self.history_manager.clear()
        for msg in value:
            self.history_manager.append(msg)

    @abstractmethod
    def run(self, input_text: str, **kwargs) -> str:
        """运行Agent（同步版本）"""
        pass


    def add_message(self, message: Message):
        """添加消息到历史记录"""
        self.history_manager.append(message)

    def __str__(self) -> str:
        return f"Agent(name={self.name}, model={self.llm.model})"

    def __repr__(self) -> str:
        return self.__str__()

    # ==================== 工具调用通用能力（从 FunctionCallAgent 提取）====================

    def _build_tool_schemas(self) -> List[Dict[str, Any]]:
        """构建工具 JSON Schema

        统一的工具 schema 构建逻辑，支持：
        - Tool 对象（带参数定义）
        - 函数工具（简化注册）

        Returns:
            工具 schema 列表
        """
        if not self.tool_registry:
            return []

        schemas: List[Dict[str, Any]] = []

        # 1. 处理 Tool 对象
        for tool in self.tool_registry.get_all_tools():
            properties: Dict[str, Any] = {}
            required: List[str] = []

            try:
                parameters = tool.get_parameters()
            except Exception:
                parameters = []

            for param in parameters:
                properties[param.name] = {
                    "type": self._map_parameter_type(param.type),
                    "description": param.description or ""
                }
                if param.default is not None:
                    properties[param.name]["default"] = param.default
                if getattr(param, "required", True):
                    required.append(param.name)

            schema: Dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": {
                        "type": "object",
                        "properties": properties
                    }
                }
            }
            if required:
                schema["function"]["parameters"]["required"] = required
            schemas.append(schema)

        # 2. 处理函数工具
        function_map = getattr(self.tool_registry, "_functions", {})
        for name, info in function_map.items():
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": info.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "input": {
                                "type": "string",
                                "description": "输入文本"
                            },
                        },
                        "required": ["input"]
                    }
                }
            })

        return schemas

    @staticmethod
    def _map_parameter_type(param_type: str) -> str:
        """将工具参数类型映射为 JSON Schema 允许的类型

        Args:
            param_type: 工具参数类型

        Returns:
            JSON Schema 类型
        """
        normalized = (param_type or "").lower()
        if normalized in {"string", "number", "integer", "boolean", "array", "object"}:
            return normalized
        return "string"

    def _convert_parameter_types(self, tool_name: str, param_dict: Dict[str, Any]) -> Dict[str, Any]:
        """根据工具定义转换参数类型

        Args:
            tool_name: 工具名称
            param_dict: 参数字典

        Returns:
            类型转换后的参数字典
        """
        if not self.tool_registry:
            return param_dict

        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            return param_dict

        try:
            tool_params = tool.get_parameters()
        except Exception:
            return param_dict

        type_mapping = {param.name: param.type for param in tool_params}
        converted: Dict[str, Any] = {}

        for key, value in param_dict.items():
            param_type = type_mapping.get(key)
            if not param_type:
                converted[key] = value
                continue

            try:
                normalized = param_type.lower()
                if normalized in {"number", "float"}:
                    converted[key] = float(value)
                elif normalized in {"integer", "int"}:
                    converted[key] = int(value)
                elif normalized in {"boolean", "bool"}:
                    if isinstance(value, bool):
                        converted[key] = value
                    elif isinstance(value, (int, float)):
                        converted[key] = bool(value)
                    elif isinstance(value, str):
                        converted[key] = value.lower() in {"true", "1", "yes"}
                    else:
                        converted[key] = bool(value)
                else:
                    converted[key] = value
            except (TypeError, ValueError):
                converted[key] = value

        return converted

    def _execute_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """执行工具调用并返回字符串结果

        统一的工具执行逻辑，支持：
        - Tool 对象（带类型转换）
        - 函数工具（简化调用）

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果（字符串格式）
        """
        if not self.tool_registry:
            return "❌ 错误：未配置工具注册表"

        # 1. 尝试执行 Tool 对象
        tool = self.tool_registry.get_tool(tool_name)

        # 0) 入口守卫: 按当前模式审查; 被拒则直接返回, 不执行。
        #    mode_guard 未配置时跳过守卫(与配置了 guard 的 Agent 行为一致)。
        if self.mode_guard is not None:
            reason = self.mode_guard.check_tool(tool_name, arguments or {}, tool=tool)
            if reason:
                return f"🚫 已拦截 [{tool_name}]: {reason}"

        if tool:
            try:
                typed_arguments = self._convert_parameter_types(tool_name, arguments)
                response = self.tool_registry.execute_tool(tool_name, typed_arguments)

                # 根据状态添加前缀
                from ..tools.response import ToolStatus
                if response.status == ToolStatus.ERROR:
                    error_code = response.error_info.get("code", "UNKNOWN") if response.error_info else "UNKNOWN"
                    return f"❌ 错误 [{error_code}]: {response.text}"
                elif response.status == ToolStatus.PARTIAL:
                    return f"⚠️ 部分成功: {response.text}"

                else:
                    return response.text
            except Exception as e:
                return f"❌ 工具调用失败：{e}"

        # 2. 尝试执行函数工具
        func = self.tool_registry.get_function(tool_name)
        if func:
            try:
                input_text = arguments.get("input", "")
                response = self.tool_registry.execute_tool(tool_name, input_text)

                # 根据状态添加前缀
                from ..tools.response import ToolStatus
                if response.status == ToolStatus.ERROR:
                    error_code = response.error_info.get("code", "UNKNOWN") if response.error_info else "UNKNOWN"
                    return f"❌ 错误 [{error_code}]: {response.text}"
                elif response.status == ToolStatus.PARTIAL:
                    return f"⚠️ 部分成功: {response.text}"
                else:
                    return response.text
            except Exception as exc:
                return f"❌ 工具调用失败：{exc}"
        return f"❌ 错误：未找到工具 '{tool_name}'"

    # ==================== 会话持久化能力 ====================

    def _auto_save(self):
        """自动保存会话（静默失败）"""
        if not self.session_store:
            return

        try:
            self.session_store.save(
                agent_config=self._get_agent_config(),
                history=self.history_manager.get_history(),
                tool_schema_hash=self.compute_tool_schema_hash()
            )
        except Exception as e:
            # 自动保存失败不影响主流程
            if self.config.debug:
                print(f"⚠️ 自动保存失败: {e}")

    def _get_agent_config(self) -> Dict[str, Any]:
        """ 获取Agent配置信息

        Returns:
            配置字典
        """
        config = {
            "name": self.name,
            "agent_type": self.__class__.__name__,
            "llm_provider": getattr(self.llm, 'provider', 'unknown'),
            "llm_model": getattr(self.llm, 'model_id', getattr(self.llm, 'model', 'unknown')),
        }

        # 添加 max_steps（如果存在）
        if hasattr(self, 'max_steps'):
            config["max_steps"] = self.max_steps

        return config

    def _compute_tool_schema_hash(self) -> str:
        """计算工具 Schema 哈希

        用于检测工具定义是否变化

        Returns:
            工具 Schema 哈希值（16位）
        """
        if not self.tool_registry:
            return "no-tools"

        import json
        from hashlib import sha256

        # 收集所有工具的签名
        tools_signature = {}

        for tool_name in sorted(self.tool_registry.list_tools()):
            tool = self.tool_registry.get_tool(tool_name)
            if tool:
                tools_signature[tool_name] = {
                    "name": tool.name,
                    "description": tool.description[:100] if tool.description else "",
                    "parameters": list(tool.parameters.keys()) if hasattr(tool, 'parameters') and tool.parameters else []
                }

        schema_str = json.dumps(tools_signature, sort_keys=True)
        return sha256(schema_str.encode()).hexdigest()[:16]

    def _get_read_cache(self) -> Dict[str, Dict]:
        """获取 Read 工具的元数据缓存

        Returns:
            元数据缓存字典
        """
        if self.tool_registry and hasattr(self.tool_registry, 'read_metadata_cache'):
            return self.tool_registry.read_metadata_cache
        return {}

    # ==================== 子代理机制 ====================
    def run_as_subagent(
        self,
        task: str,
        tool_filter: Optional['ToolFilter'] = None,
        return_summary: bool = True,
        max_steps_override: Optional[int] = None
    ) -> Dict[str, Any]:
        """作为子代理运行（上下文隔离模式）

                特性：
                - 上下文隔离：创建独立的历史记录，不污染主 Agent 上下文
                - 工具过滤：可选的工具访问控制
                - 摘要返回：返回结构化摘要而非完整历史
                - 状态恢复：执行后自动恢复原始状态

                Args:
                    task: 子任务描述
                    tool_filter: 工具过滤器（可选），用于限制可用工具
                    return_summary: 是否返回摘要（True）或完整结果（False）
                    max_steps_override: 覆盖最大步数（可选）

                Returns:
                    {
                        "success": bool,           # 是否成功完成
                        "summary": str,            # 任务摘要（如果 return_summary=True）
                        "result": str,             # 完整结果（如果 return_summary=False）
                        "metadata": {              # 执行元数据
                            "steps": int,          # 执行步数
                            "tokens": int,         # 消耗 Token 数（估算）
                            "duration_seconds": float,  # 执行时长
                            "tools_used": List[str],    # 使用的工具列表
                            "error": Optional[str]      # 错误信息（如果失败）
                        }
                    }
                """

    def _register_task_tool(self):
        pass

    def _register_todowrite_tool(self):
        pass

    def _register_devlog_tool(self):
        pass