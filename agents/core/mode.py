"""Agent 模式定义与沙箱策略。

模式决定三件事: 能读哪些目录、能写哪些目录、工具调用是否需要审批

    plan    : 可读 系统根目录 + 项目目录 + 用户批准目录; 不可写;
              专用工具审批, bash 工具不审批。
    build   : 可读 系统根目录 + 项目目录 + 用户批准目录; 可写 项目目录 + 额外可写根;
              专用工具审批, bash 工具不审批。
    trusted : 可读 全盘; 可写 项目目录 + 用户批准目录; 不审批。

- AgentMode: 目前实现 build / plan 两种, trusted 尚未接入。
- ModeGuard: 持有当前模式与沙箱策略, 通过 sandbox_policy() 给 bash 内核围栏提供边界。
- check_tool(): 审查逻辑已断开, 恒返回 None; 工具授权改由专用工具审批与内核沙箱承担。

沙箱是最终的硬边界: 不论审批结果如何, 一次调用真正能读写的目录范围都由
SandboxPolicy 决定; 用户批准目录同样要反映到策略里, 而不是靠放宽命令审查。
"""
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from ..fs import SandboxMode, SandboxPolicy, build_policy
from ..tools.tool_filter import ReadOnlyFilter

if TYPE_CHECKING:
    from ..tools.base import Tool


class AgentMode(str, Enum):
    """Agent 的运行模式。

    继承 (str, Enum) 是为了让成员本身也是字符串:
    - 可以直接与字符串 'build'/'plan' 比较, 也方便 JSON 序列化/日志输出。

    目标设计见 101-总体目标.md: plan/build/trusted 三种模式;
    当前仅接入 plan 与 build。
    """

    BUILD = 'build'  # 构建模式: 可写项目目录与额外可写根
    PLAN = "plan"    # 规划模式: 只读探索代码, 无任何可写目录


# plan 模式可用的只读工具(与 agents/tools/tool_filter.py 的 ReadOnlyFilter 同源,
# 避免两处漂移; 工具名统一小写后比较)。
# 入口守卫的审查逻辑断开后, 本常量暂未参与判定, 保留给后续工具审批逻辑使用。
PLAN_READONLY_TOOLS = frozenset(name.lower() for name in ReadOnlyFilter.READONLY_TOOLS)

# 注入给模型的系统提示: 告诉它当前处于 plan 模式。
# 注意这只是"软约束"(引导模型别乱来); 真正的硬边界是沙箱策略(不可写),
# 专用工具访问外部路径时另走审批流程, bash 工具则不在入口处审批。
PLAN_HINT = (
    "你现在处于 plan(规划)模式: 只可读取/探索代码与运行只读命令(如 ls/cat/grep/git status), "
    "并为后续构建制定方案; 严禁写文件、编辑、删除或运行任何会修改系统的命令。"
)


def _default_policy(mode: 'AgentMode', project_root: str) -> SandboxPolicy:
    """按模式构造默认沙箱策略。

    plan 没有可写目录 -> 只读; build 可写项目目录 -> workspace-write
    (额外可写根需要显式传入 policy 提供)。
    """
    sandbox_mode = SandboxMode.READ_ONLY if mode == AgentMode.PLAN else SandboxMode.WORKSPACE_WRITE
    return build_policy(sandbox_mode, project_root)


class ModeGuard:
    """模式与沙箱策略的持有者。

    负责切换模式, 并把当前模式折算成 SandboxPolicy 交给 bash 内核围栏
    (见 sandbox_policy())。真正的读写边界由沙箱强制, 不在入口处做命令审查。
    check_tool() 目前恒返回 None: 在后续模式鉴权接入前, 入口守卫不拦截任何调用。
    """

    def __init__(self, mode: AgentMode = AgentMode.BUILD, project_root: str = ".",
                 policy: Optional[SandboxPolicy] = None):
        # 当前模式, 默认 build。
        self.mode = mode
        # 项目根目录: 未显式传 policy 时用它构造默认策略。
        self.project_root = project_root
        self._explicit_policy = policy
        self.policy = policy or _default_policy(mode, project_root)

    def set_mode(self, mode: AgentMode) -> None:
        """切换当前模式(例如用户从 plan 切到 build)。"""
        self.mode = mode
        # 未显式传 policy 时, 模式切换需重建默认策略, 否则 plan->build 会残留只读策略。
        if self._explicit_policy is None:
            self.policy = _default_policy(mode, self.project_root)

    def sandbox_policy(self) -> SandboxPolicy:
        """当前模式对应的沙箱策略(供 bash 内核围栏使用)。

        - plan -> read-only: 写不进去(这才是 plan"无文件改动"的真正保证);
        - build -> 沿用配置策略的模式(通常 workspace-write, 也可能是 danger-full-access)。
        工作区根与额外可写根保持不变。
        """
        if self.mode == AgentMode.PLAN:
            mode = SandboxMode.READ_ONLY
        elif isinstance(self.policy.mode, SandboxMode):
            mode = self.policy.mode
        else:
            mode = SandboxMode(self.policy.mode)
        return SandboxPolicy(
            mode=mode,
            workspace_root=self.policy.workspace_root,
            extra_writable=self.policy.extra_writable,
        )

    def check_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        tool: Optional["Tool"] = None,
        read_only: Optional[bool] = None,
    ) -> Optional[str]:
        return None
