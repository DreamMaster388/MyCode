"""Agent 模式定义与执行守卫。

本模块是整个 Agent 的"安全阀门": 无论模型想调用什么工具/命令, 都要先经过
ModeGuard 审查, 只有在当前模式下合法的操作才会被放行。

- AgentMode: build / plan 两种模式。
- ModeGuard: 工具执行入口守卫。
    * 非 bash 工具: plan 只允许只读白名单内的工具; build 对写工具的路径参数做沙箱。
    * bash 工具:  plan 按"只读白名单"审查; build 按"高危拦截 + 路径沙箱"审查。
- 规则函数(read_only/high_risk/path_sandbox)消费 CommandAudit。

设计原则(与 command_audit 一致): 默认拒绝。凡是解析不了、判断不了的情况,
一律返回拦截原因, 而不是猜测安全后放行。
"""
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from ..fs import (
    PathError, SandboxDenied, SandboxMode, SandboxPolicy,
    authorize_write, build_policy, resolve_path,
)
from ..tools.tool_filter import ReadOnlyFilter

if TYPE_CHECKING:
    from ..tools.base import Tool


class AgentMode(str, Enum):
    """Agent 的两种运行模式。

    继承 (str, Enum) 是为了让成员本身也是字符串:
    - 可以直接与字符串 'build'/'plan' 比较, 也方便 JSON 序列化/日志输出。
    """

    BUILD = 'build'  # 构建模式: 允许写文件、执行修改性命令(仍受高危/沙箱约束)
    PLAN = "plan"    # 规划模式: 只读探索代码, 禁止任何写操作与系统修改


# plan 模式承认的只读程序(白名单之外一律拦截; sed/awk/git/解释器另做特判)
# 采用"白名单"而非"黑名单": 只有明确安全的命令才放行, 未知命令默认拒绝。
READONLY_PROGRAMS = frozenset({
    "ls", "cat", "head", "tail", "less", "more", "wc", "grep", "rg", "find", "tree",
    "pwd", "echo", "printf", "env", "printenv", "date", "which", "whereis", "type",
    "file", "du", "df", "stat", "readlink", "realpath", "dirname", "basename",
    "sort", "uniq", "cut", "tr", "seq", "man", "help", "true", "false", "test",
    "command", "time",
})
# git 只读子命令: `git` 本身可读可写, 所以必须看子命令(如 status/diff/log 放行, reset/clean 拦截)
GIT_READONLY = frozenset({
    "status", "diff", "log", "show", "branch", "remote", "tag", "describe",
    "rev-parse", "config", "ls-files", "ls-tree", "grep", "blame", "cat-file",
    "show-ref", "name-rev", "shortlog",
})
# plan 模式承认的只读工具(与 agents/tools/tool_filter.py 的 ReadOnlyFilter 同源,
# 避免两处漂移; 工具名统一小写后比较)。
PLAN_READONLY_TOOLS = frozenset(name.lower() for name in ReadOnlyFilter.READONLY_TOOLS)

# 注入给模型的系统提示: 告诉它当前处于 plan 模式。
# 注意这只是"软约束"(引导模型别乱来), 真正的硬拦截仍由 ModeGuard 在工具调用处完成。
PLAN_HINT = (
    "你现在处于 plan(规划)模式: 只可读取/探索代码与运行只读命令(如 ls/cat/grep/git status), "
    "并为后续构建制定方案; 严禁写文件、编辑、删除或运行任何会修改系统的命令。"
)

def _default_policy(mode: 'AgentMode', project_root: str) -> SandboxPolicy:
    sandbox_mode = SandboxMode.READ_ONLY if mode == AgentMode.PLAN else SandboxMode.WORKSPACE_WRITE
    return build_policy(sandbox_mode, project_root)


class ModeGuard:
    """执行入口守卫: 返回拦截原因(str)表示拒绝, 返回None表示放行。

    使用方式: 在真正执行任何工具前调用 check_tool(), 只要返回值非 None,
    就应把该字符串作为错误反馈给模型, 并取消本次执行。
    """

    def __init__(self, mode: AgentMode = AgentMode.BUILD, project_root: str = ".",
                 policy: Optional[SandboxPolicy] = None):
        # 当前模式, 默认 build(最宽松); 由调用方按会话/请求切换。
        self.mode = mode
        # 项目根目录: 未显式传 policy 时用它构造默认策略。
        self.project_root = project_root
        self._explicit_policy = policy
        self.policy = policy or _default_policy(mode, project_root)

    def set_mode(self, mode: AgentMode) -> None:
        """切换当前模式(例如用户从 plan 切到 build)。"""
        self.mode = mode
        # 未显式传 policy 时, 模式切换需重建默认策略, 否则 plan->build 会残留只读策略
        if self._explicit_policy is None:
            self.policy = _default_policy(mode, self.project_root)

    def check_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        tool: Optional["Tool"] = None,
        read_only: Optional[bool] = None,
    ) -> Optional[str]:
        """工具执行前的统一入口检查。

        参数:
            tool_name: 工具名(如 'bash'/'Write'/'read')。
            args:      工具调用参数。
            tool:      对应的 Tool 对象(可选)。提供时按其 is_write_tool /
                       path_params 元数据判定, 元数据优先于 read_only。
            read_only: 无 Tool 对象时的回退声明(旧语义): True 表示只读。

        返回:
            None 表示放行; 非空 str 表示拦截原因。
        """
        # bash 命令内容复杂(管道/重定向/子命令), 统一交给命令审计处理。
        if tool_name.lower() == "bash":
            return self._check_bash(args)

        # 取能力元数据; 没有 Tool 对象时回退到 read_only 形参。
        if tool is not None:
            is_write = bool(getattr(tool, "is_write_tool", False))
            path_params = tuple(getattr(tool, "path_params", ()) or ())
        else:
            is_write = not bool(read_only)
            path_params = ()

        # plan: 只读白名单, 默认拒绝(不在名单内的一律拦截)。
        if self.mode == AgentMode.PLAN:
            if tool_name.lower() not in PLAN_READONLY_TOOLS:
                return (f"plan 模式拦截: 工具 '{tool_name}' 不可用。"
                        f"仅 Read/Grep/Glob 与只读 bash 可用。")
            return None

        # build: 写工具对每个路径参数做路径沙箱(与文件工具共用同一策略)。
        if is_write and path_params:
            return self._check_path_params(args, path_params)
        return None

    def _check_path_params(self, args: dict[str, Any], path_params) -> Optional[str]:
        """build规则: 对写工具的路径参数逐个做可写根校验。"""
        root = self.policy.workspace_root.display_path
        for name in path_params:
            raw = (args or {}).get(name)
            if not raw:
                continue
            try:
                target = resolve_path(raw, root)
            except PathError as exc:
                return f"路径参数 '{name}' 无法解析: {exc}"
            try:
                authorize_write(self.policy, target)
            except SandboxDenied as exc:
                return f"路径参数 '{name}' 被拦截: {exc}"
        return None

    def _check_bash(self, args: dict[str, Any]) -> Optional[str]:
        """审查一条 bash 命令字符串。"""
        # 延迟导入: 避免与 command_audit 形成模块级循环依赖, 也减少无谓开销。
        from .command_audit import audit
        # 参数缺失时按空命令处理, 后续 audit 会判定为无法解析而拦截。
        command = (args or {}).get("command") or ""
        a = audit(command, self.project_root)
        # 解析失败不猜测, 直接拒绝。
        if a.parse_error:
            return f"命令无法安全解析, 已拦截。原因: {a.parse_error}"
        # plan: 按"全树只读"规则审查; build: 高危拦截 + 路径沙箱。
        if self.mode == AgentMode.PLAN:
            return read_only_violation(a)
        # build 模式: 任一规则命中即拦截(短路求值, 先判高危, 再判路径越界)。
        return high_risk_violation(a) or path_sandbox_violation(a, self.policy)


# ---------------- 规则函数: 消费 CommandAudit ----------------
def _prog_ok(seg) -> bool:
    """plan 模式: 单段命令是否属于只读白名单。"""
    p = seg.command.lower()
    if not p:
        return True
    # 按行按列处理文件的命令，有写相关的参数则拒绝
    # (sed/awk 本身是只读的, 但 -i/--in-place 会原地改写文件, 因此单独判断)
    if p in ("sed", "awk"):
        return not any(x in ("-i", "--in-place") for x in seg.args)
    # 放行git的白名单读操作
    # (git 形态是 `git <subcommand>`, 故取第一个参数作为子命令判断)
    if p == "git":
        return (seg.args[0].lower() if seg.args else "") in GIT_READONLY
    # 各类解释器可以执行任意代码(如 `python -c '...'`), 即使命令名看起来无害也一律拒绝。
    if p in ("python", "python3", "python2", "node", "php", "ruby", "perl", "bash", "sh"):
        return False
    # 其余命令: 命中只读白名单才放行。
    return p in READONLY_PROGRAMS


def _iter_redirections(a):
    """遍历段内 + compound 级的所有重定向。

    重定向可能挂在具体命令段上(如 `echo x > f`), 也可能挂在 compound 上
    (如 `{ ...; } > f`), 这里统一展开, 方便规则层一次性检查。
    """
    for seg in a.segments:
        yield from seg.redirections
    yield from a.redirections


def read_only_violation(audit) -> Optional[str]:
    """plan规则: 全树只读才放行。

    判定顺序(任一命中即返回拦截原因):
    1. 解析失败 -> 拒绝;
    2. 含控制流(if/for/while) -> 拒绝, 让模型改成在方案里描述;
    3. 任一段带 privilege/footgun/download/pkg_install 高危标记 -> 拒绝;
    4. 任一重定向是写操作 -> 拒绝;
    5. 任一段命令不在只读白名单 -> 拒绝;
    6. 对嵌套子命令($()/子 shell)递归套用同样规则。
    """
    if audit.parse_error:
        return f"命令无法解析: {audit.parse_error}"
    if audit.has_control_flow:
        return "包含控制流(if/for/while), plan 模式不执行, 请在方案中说明。"
    for seg in audit.segments:
        # plan 连 pkg_install 都拦: 安装依赖属于修改系统, 只应写进方案。
        if seg.risk_flags & {"privilege", "footgun", "download", "pkg_install"}:
            return f"高危命令 '{seg.command}'({'/'.join(sorted(seg.risk_flags))}), 已拦截。"
    for r in _iter_redirections(audit):
        if r.is_write:
            return f"含写重定向 '{r.operator}' -> {r.target}, plan 模式已拦截。"
    for seg in audit.segments:
        if not _prog_ok(seg):
            return f"命令 '{seg.command}' 不在只读白名单, plan 模式已拦截。"
    # 递归审查嵌套命令, 防止 `ls $(rm -rf /)` 这类"外层只读、内层破坏"的绕过。
    for sub in audit.nested_commands:
        reason = read_only_violation(sub)
        if reason:
            return reason
    return None


def high_risk_violation(a) -> Optional[str]:
    """build规则: 拦截高危命令。

    与 plan 的区别: build 允许 pkg_install(安装依赖是正常构建行为),
    但仍拦截 privilege(提权)/footgun(破坏性)/download(任意下载)三类。
    同样递归检查嵌套子命令, 防止靠 $() 藏高危命令绕过。
    """
    for seg in a.segments:
        high = seg.risk_flags & {"privilege", "footgun", "download"}
        if high:
            return f"高危命令 '{seg.command}' ({'/'.join(sorted(high))}), 已拦截, 请人工确认。"
    for sub in a.nested_commands:
        reason = high_risk_violation(sub)
        if reason:
            return reason
    return None


def path_sandbox_violation(a, policy: SandboxPolicy) -> Optional[str]:
    """build规则: 路径沙箱——写目标必须落在可写根内。

    只检查"写重定向"(如 `> /etc/passwd`)的目标路径:
    相对路径以可写根为基准解析; 与文件工具共用同一策略与同一判定。
    """
    root = policy.workspace_root.display_path

    def check(r):
        if not (r.is_write and r.path):
            return None
        try:
            target = resolve_path(r.path, root)          # 相对 cwd=可写根
        except PathError as exc:
            return f"写重定向 '{r.operator}' -> {r.path} 无法解析: {exc}"
        try:
            authorize_write(policy, target)
        except SandboxDenied as exc:
            return f"含写重定向 '{r.operator}' -> {r.path}: {exc}"
        return None

    for r in _iter_redirections(a):
        v = check(r)
        if v:
            return v
    # 递归检查嵌套子命令里的写重定向。
    for sub in a.nested_commands:
        v = path_sandbox_violation(sub, policy)
        if v:
            return v

if __name__ == '__main__':
    mg = ModeGuard()
    mg.set_mode(AgentMode.PLAN)
    args = {
        "command": '{ echo "=== start inspect ==="; ROOT=$(pwd); FILE_LIST=$(ls -la "$ROOT" | grep -v total | head -5); (stat . && echo "inode check") && cat <(echo "$FILE_LIST") | cut -d' ' -f9 | wc -l; echo $(find . -maxdepth 2 -type f | grep -E "\.(py|md)$" | sort | head -3); echo "hello world" > test.txt; } < /etc/hosts'
    }
    tool_name = 'bash'
    print(mg.check_tool(tool_name, args))
