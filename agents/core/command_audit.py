"""bash 命令审计核心。

把任意 bash 命令字符串解析为归一化的 CommandAudit 模型, 供 plan/build 两种规则共用。
解析器使用 bashlex; 任何解析失败一律默认拒绝(安全优先)。

节点结构以 bashlex 源码为准 (ast.py / parser.py / subst.py):
- list/pipeline/command/for/while/if/case/select/pattern/function 用 .parts
- compound 用 .list(列表) + .redirects(列表), 无 .parts
- redirect 用 .input(int|None) / .type(操作符) / .output(节点或字符串) / .heredoc
- commandsubstitution/processsubstitution 用 .command(单个节点)
- 子 shell `( ... )` 与分组 `{ ... }` 都是 compound.list = [reservedword('('或'{'), body, reservedword(')'或'}')]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import bashlex
from bashlex.ast import node as BashNode


@dataclass
class Redirection:
    """一次重定向的归一化描述。

    例:
        `2>> err.log` -> operator='>>', target='err.log', is_write=True,  path='err.log'
        `2>&1`        -> operator='>&', target='1',       is_write=False, path=None
        `< input`     -> operator='<',  target='input',   is_write=False, path=None
    """
    operator: str        # 重定向操作符(取自 node.type)。'>','>>','>|','<>','&>','&>>' 判为写; '<','<<','>&','<&' 等为读/复制
    target: str          # 重定向目标(取自 node.output)。'&1'/'3' 之类表示文件描述符, 其余为路径
    is_write: bool       # 是否"写文件"语义 —— plan 模式据此拦截
    path: Optional[str]  # 目标文件路径(相对 cwd), 供 build 模式"路径沙箱"比对; 非写或目标是 fd 时为 None


@dataclass
class Segment:
    """一条简单命令(一个可执行动作)。管道/分号会被拆成多条。

    例: `echo "done" | wc -l` -> [Segment('echo'), Segment('wc')]
    """
    command: str          # 程序名(首 word), 如 'ls'/'git'; 空串表示该段解析异常
    args: list[str] = field(default_factory=list)   # 其余单词参数, 含 git 子命令、sed 的 '-i' 等, 供后续风险判定
    redirections: list[Redirection] = field(default_factory=list)
    risk_flags: set[str] = field(default_factory=set)  # 按 command+args 算出的高危标记, 如 {'footgun'} / {'privilege'} / {'pkg_install'}


@dataclass
class CommandAudit:
    """整条命令的归一化审计结果, 供 plan/build 规则层(mode.py)消费。

    规则层判定顺序通常为:
    1. parse_error 非空 -> 默认拒绝(解析不了就不猜)。
    2. 遍历 segments -> 逐段判断只读/写/高危。
    3. 遍历 nested_commands -> 对其递归套用同一套判断(防 `ls $(rm -rf /)` 绕过)。
    4. 遍历 redirections -> compound 级重定向(如 `{ ...; } > file`)。
    5. 结合 has_control_flow 等标志做整体放行/拦截。
    """
    segments: list[Segment] = field(default_factory=list)      # 顶层/管道中的各简单命令
    nested_commands: list["CommandAudit"] = field(default_factory=list)  # $()/子shell 里的子命令(递归审计)
    redirections: list[Redirection] = field(default_factory=list)         # compound 级重定向(游离于 command 之外)
    has_control_flow: bool = False       # 含 for/while/if/case/function(plan 默认拦截)
    has_pipeline: bool = False           # 含 `|`(用于日志/文案)
    has_subshell: bool = False           # 含 `( ... )`(子 shell 会独立执行, 需递归审计)
    has_command_substitution: bool = False  # 含 `$( )`/反引号(会真实执行子命令, 需递归审计)
    parse_error: Optional[str] = None    # 解析失败原因; 非空即应默认拒绝


# 判"写"的重定向操作符(对应 parser.py 的 token):
#   '>' 覆盖 / '>>' 追加 / '>|' 覆盖 / '<>' 读写同文件 / '&>'、'&>>' stdout+stderr 一起写
_WRITE_OPS = {">", ">>", ">|", "<>", "&>", "&>>"}
# 控制流节点 kind: 它们包含可执行 body, plan 模式默认不执行, 只让模型在方案里说明
_CONTROL_KINDS = {"for", "while", "until", "if", "case", "select", "function"}
# 纯语法/叶子节点, 不承载可执行命令, 遍历时直接跳过
_SKIP = {"operator", "reservedword", "pipe", "parameter", "tilde", "heredoc"}


def is_write_redirect(operator: str) -> bool:
    """单个重定向操作符是否判为"写文件"(供规则与测试复用)。"""
    return (operator or "") in _WRITE_OPS


def audit(command: str, project_root: str = ".") -> CommandAudit:
    """入口: 把 bash 命令字符串解析成 CommandAudit。

    - 任何解析失败都返回 parse_error 非空的审计对象, 而不是抛异常(安全优先)。
    - 顶层可能是一个 list/compound 节点, 也可能是一组节点, 所以统一 for 遍历 + 递归 _walk。
    - project_root 只在后续"路径沙箱"比对时使用, 解析本身不依赖它。
    """
    try:
        nodes = bashlex.parse(command)
    except Exception as exc:  # ParsingError / 其它语法错误 -> 默认拒绝
        return CommandAudit(parse_error=f"{type(exc).__name__}: {exc}")

    ctx = CommandAudit()
    root = Path(project_root).resolve()
    for n in nodes:
        if isinstance(n, BashNode):
            _walk(n, ctx, root)
    return ctx


def _children(node: BashNode) -> list[BashNode]:
    """返回该节点下需要继续下钻的子节点列表。

    不同 node 存放子节点的字段不一致(照抄 ast.py 的 nodevisitor.visit):
    - 用 `.parts` 的: list/pipeline/command/if/for/while/until/case/select/pattern/function/unimplemented
    - 用 `.list` + `.redirects` 的: compound(注意它没有 .parts!)
    - 用 `.command`(单个节点)的: commandsubstitution / processsubstitution
    - word/assignment 的 `.parts` 里藏着命令替换/参数展开等子节点
    其余(operator/reservedword/pipe/parameter/...)没有子节点, 返回空列表。
    """
    k = node.kind
    if k in ("list", "pipeline", "if", "for", "while", "until", "case",
             "select", "pattern", "command", "unimplemented"):
        return list(getattr(node, "parts", None) or ())
    if k == "compound":
        return list(getattr(node, "list", None) or ()) + \
               list(getattr(node, "redirects", None) or ())
    if k == "function":
        return list(getattr(node, "parts", None) or ())
    if k in ("word", "assignment"):
        return list(getattr(node, "parts", None) or ())
    if k in ("commandsubstitution", "processsubstitution"):
        cmd = getattr(node, "command", None)
        return [cmd] if isinstance(cmd, BashNode) else []
    if k == "redirect":
        out = (getattr(node, "output", None), getattr(node, "heredoc", None))
        return [o for o in out if isinstance(o, BashNode)]
    return []


def _word_value(part: Any) -> Optional[str]:
    """取一个"词"节点的字符串值。

    既可能是 WordNode(取 .word), 也可能是纯字符串(如 `2>&1` 里 node.output='1')。
    返回 None 表示无法提取, 调用方应跳过。
    """
    if isinstance(part, str):
        return part
    w = getattr(part, "word", None)
    return w if isinstance(w, str) else None


def _walk(node: BashNode, ctx: CommandAudit, root: Path) -> None:
    """递归遍历 bashlex AST, 把结果填进 ctx。

    每个分支处理一种 node.kind:
    - command           -> 压成一条 Segment。
    - redirect          -> 游离于 command 的 compound 级重定向, 收进 ctx.redirections。
    - compound          -> 可能含子 shell `( )`, 先判 has_subshell, 再下钻 .list/.redirects。
    - 控制流/管道        -> 置对应标志后下钻。
    - 命令替换          -> 单独解析成子审计, 放进 nested_commands。
    - 其余(list/word/…) -> 无条件下钻子节点。
    """
    kind = getattr(node, "kind", None)
    if kind is None or kind in _SKIP:
        return

    if kind == "command":
        seg = _extract_segment(node, root, ctx)
        if seg:
            ctx.segments.append(seg)
        return

    if kind == "redirect":                       # compound 级重定向, 如 `{ ...; } > file`
        redir = _extract_redirection(node, root)
        if redir:
            ctx.redirections.append(redir)
        return

    if kind == "compound":
        kids = getattr(node, "list", None) or []
        if (len(kids) >= 3 and kids[0].kind == "reservedword"
                and kids[-1].kind == "reservedword"
                and kids[0].word == "(" and kids[-1].word == ")"):
            ctx.has_subshell = True
        for child in _children(node):
            _walk(child, ctx, root)
        return

    if kind in _CONTROL_KINDS:
        ctx.has_control_flow = True
        for child in _children(node):
            _walk(child, ctx, root)
        return

    if kind == "pipeline":
        ctx.has_pipeline = True
        for child in _children(node):
            _walk(child, ctx, root)
        return

    if kind in ("commandsubstitution", "processsubstitution"):
        ctx.has_command_substitution = True
        cmd = getattr(node, "command", None)
        if isinstance(cmd, BashNode):
            inner = CommandAudit()
            _walk(cmd, inner, root)
            ctx.nested_commands.append(inner)
        return

    for child in _children(node):                # list / word / assignment / 未知
        if isinstance(child, BashNode):
            _walk(child, ctx, root)


def _extract_segment(node: BashNode, root: Path, ctx: CommandAudit) -> Optional[Segment]:
    """把 command 节点压成一条 Segment。

    command.parts = [word, assignment, redirect, ...](bashlex 的 simple_command):
    - 第一个 word -> command(命令名); 其余 word -> args(参数)
    - assignment  -> 是 `FOO=bar` 前缀或环境变量赋值, 不当作命令名
    - redirect    -> 收进段的 redirections
    - word/assignment 内部的 $( ) 递归进 nested_commands
    """
    seg = Segment(command="")
    for part in getattr(node, "parts", None) or ():
        kind = getattr(part, "kind", None)
        if kind == "word":
            value = getattr(part, "word", None)
            if value is None:
                continue
            if not seg.command:
                seg.command = value          # 首 word = 命令名
            else:
                seg.args.append(value)
            _collect_substitutions(_children(part), ctx, root)   # 参数里的 $( )
        elif kind == "assignment":
            _collect_substitutions(_children(part), ctx, root)   # FOO=$(...) 之类的赋值
        elif kind == "redirect":
            redir = _extract_redirection(part, root)
            if redir:
                seg.redirections.append(redir)
        elif kind in ("commandsubstitution", "processsubstitution"):
            ctx.has_command_substitution = True
            cmd = getattr(part, "command", None)
            if isinstance(cmd, BashNode):
                inner = CommandAudit()
                _walk(cmd, inner, root)
                ctx.nested_commands.append(inner)
        # operator / reservedword / parameter / tilde: 跳过
    seg.command = seg.command or ""
    seg.risk_flags = _risk_flags(seg.command, seg.args)
    return seg


def _collect_substitutions(children: list, ctx: CommandAudit, root: Path) -> None:
    """在给定节点列表里寻找命令替换/进程替换, 递归解析成嵌套审计。

    命令替换本质是"重新解析一段子命令", 所以这里再走一遍 _walk,
    把结果塞进 ctx.nested_commands —— 这样外层只读、内层爆破的写法也会被审计到。
    """
    for ch in children:
        if not isinstance(ch, BashNode):
            continue
        k = ch.kind
        if k in ("commandsubstitution", "processsubstitution"):
            ctx.has_command_substitution = True
            cmd = getattr(ch, "command", None)
            if isinstance(cmd, BashNode):
                inner = CommandAudit()
                _walk(cmd, inner, root)
                ctx.nested_commands.append(inner)
        elif k in ("word", "assignment", "list", "pipeline", "compound",
                   "if", "for", "while", "until", "case"):
            _collect_substitutions(_children(ch), ctx, root)   # 递归下钻


def _extract_redirection(node: BashNode, root: Path) -> Optional[Redirection]:
    """把 bashlex 的 redirect 节点映射成 Redirection。

    redirect 字段(来自 parser.py p_redirection):
    - .type   -> 操作符, 如 '>'/'>>'/'<'/'>&'
    - .input  -> fd(如 `2>>` 的 input=2), 操作符本身已不含前缀
    - .output -> 目标(WordNode 或字符串); `>&1` 时为字符串 '1'
    """
    op = getattr(node, "type", None) or ""          # '>', '>>', '<', '>&', '&>', ...
    out = getattr(node, "output", None)
    target = _word_value(out) if out is not None else ""
    is_write = op in _WRITE_OPS
    path = target if (is_write and target and not target.startswith("&")) else None
    return Redirection(operator=op, target=target or "", is_write=is_write, path=path)


# ---------------- 高危标记(上下文相关) ----------------

def _risk_flags(command: str, args: list[str]) -> set[str]:
    """根据命令名+参数打出危险性标记, 供 plan/build 规则参考。

    标记含义:
    - privilege    : sudo/su/doas 提权 —— plan/build 都拦
    - download     : curl/wget 任意下载 —— plan/build 都拦(尤其 `curl | sh`)
    - footgun      : rm/dd/mkfs/shred 等破坏性, 以及 git 的破坏性子命令
    - pkg_install  : pip/npm/make 等安装动作 —— plan 拦, build 放行
    """
    p = command.lower()
    flags: set[str] = set()

    if p in ("sudo", "doas", "su"):
        flags.add("privilege")
        return flags

    if p in ("curl", "wget"):
        flags.add("download")
        flags.add("footgun")

    if p in ("rm", "dd", "mkfs", "shred"):
        flags.add("footgun")

    if p in ("pip", "pip3", "npm", "yarn", "pnpm", "gem", "cargo",
             "apt", "apt-get", "yum", "dnf", "brew", "go", "make", "cmake"):
        flags.add("pkg_install")

    if p == "git":
        sub = args[0].lower() if args else ""
        if sub in ("reset", "clean", "rebase", "checkout", "merge", "cherry-pick",
                   "revert", "apply", "switch", "restore", "push", "branch", "delete", "rm", "mv"):
            flags.add("footgun")

    return flags


if __name__ == "__main__":
    ok = True

    def check(cond: bool, msg: str) -> None:
        global ok
        ok &= cond
        print(("PASS " if cond else "FAIL ") + msg)
    a = audit('cat <<EOF \n abc \n EOF')
    print(a)

    # # 1) 多命令 + 管道 + 重定向
    # a = audit('ls -l > out.txt; cd /tmp; echo "done" | wc -l; grep test < input.txt 2>> err.log')
    # check(a.parse_error is None, f"五段命令解析成功 (parse_error={a.parse_error})")
    # check(len(a.segments) == 5, f"五段命令 segments==5 (实际 {len(a.segments)})")
    # check(a.has_pipeline is True, "五段命令 has_pipeline True")
    # by = {s.command: s for s in a.segments}
    # check(by["echo"].args == ["done"], f"echo args==['done'] (实际 {by['echo'].args})")
    # check(by["grep"].redirections[1].is_write and by["grep"].redirections[1].path == "err.log",
    #       f"grep 2>> err.log write/path (实际 {by['grep'].redirections})")

    # # 2) 控制流 for
    # b = audit('for i in 1 2 3; do echo $i; done')
    # print(b)
    # 3) 子 shell
    # c = audit('( echo c )')

    # # 4) 命令替换(嵌套命令)应被捕获
    # d = audit('echo $(rm -rf /)')
    # check(d.has_command_substitution and len(d.nested_commands) >= 1,
    #       f"命令替换捕获嵌套命令 (nested={len(d.nested_commands)})")
    # inner = d.nested_commands[0] if d.nested_commands else None
    # check(inner is not None and inner.segments and inner.segments[0].command == "rm"
    #       and "footgun" in inner.segments[0].risk_flags,
    #       f"嵌套 rm 高危标记 (实际 {[(s.command, s.risk_flags) for s in inner.segments] if inner else None})")

    # # 5) fd 复制非写; && 管道类
    # e = audit('ls 2>&1')
    # check(not any(r.is_write for s in e.segments for r in s.redirections),
    #       f"2>&1 不判为写 (实际 {[(r.operator, r.is_write) for s in e.segments for r in s.redirections]})")

    # 6) compound 级重定向 { echo; } > file 应判写
    # f = audit('{ echo a; echo b; } > out.txt')
    # print(f.redirections)
    # check(any(r.is_write and r.path == "out.txt" for r in f.redirections),
    #       f"compound 级 > out.txt 判写 (实际 {[(r.operator, r.is_write, r.path) for r in f.redirections]})")

    # # 7) 畸形输入: 置 parse_error 而非抛异常
    # bad = audit('echo "unclosed')
    # check(bad.parse_error is not None, f"畸形输入置 parse_error (实际 {bad.parse_error})")

    # print("\n== 明细 ===")
    # print("for:", [(s.command, s.args) for s in b.segments], "control=", b.has_control_flow)
    # print("subshell:", [(s.command, s.args) for s in c.segments], "sub=", c.has_subshell)
    # print("compound redirs:", [(r.operator, r.is_write, r.path) for r in f.redirections])

    # print("\n=> command_audit ACCEPTED" if ok else "\n=> command_audit REJECTED")
    # raise SystemExit(0 if ok else 1)
