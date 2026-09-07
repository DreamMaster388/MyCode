"""bash 命令审计核心。

把任意 bash 命令字符串解析为归一化的 CommandAudit 模型, 供 plan/build 两种规则共用。
解析器使用 bashlex (Adapter 可插拔); 任何解析失败一律默认拒绝(安全优先)。

基于 bashlex 实测 AST 结构:
- list / pipeline / compound / function / for / while / until / select / if / case / subshell : 容器
- operator / pipe / reservedword / separator : 分隔符(跳过)
- command : 简单命令, parts 为 word / assignment / redirect
- word    : 值在 .word
- redirect: 操作符在 .type, 目标在 .output.word, fd 在 .input
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import bashlex


@dataclass
class Redirection:
    operator: str        # node.type: '>', '>>', '<', '&>', ...
    target: str          # 目标(文件路径或 fd, 如 '&1')
    is_write: bool
    path: Optional[str]  # 解析后的文件路径(用于沙箱), 仅在目标为普通路径时非 None


@dataclass
class Segment:
    command: str                          # 程序名(如 'ls', 'git')
    args: list[str] = field(default_factory=list)
    redirections: list[Redirection] = field(default_factory=list)
    risk_flags: set[str] = field(default_factory=set)


@dataclass
class CommandAudit:
    segments: list[Segment] = field(default_factory=list)
    nested_commands: list["CommandAudit"] = field(default_factory=list)  # $()/子shell
    has_control_flow: bool = False
    has_pipeline: bool = False
    has_subshell: bool = False
    has_command_substitution: bool = False
    parse_error: Optional[str] = None


# 写类操作符; < / << / <<< / <& 属于读/here-doc
_WRITE_OPS = {">", ">>", ">|", "&>", "&>>", "<>"}


# ---------- 容器 / 忽略 kind(与 bashlex 实测一致) ----------
_CONTAINERS = {"list", "pipeline", "compound", "function", "for", "while",
               "until", "select", "if", "case", "subshell"}
_SKIP = {"operator", "pipe", "reservedword", "separator"}


def is_write_redirect(operator: str) -> bool:
    return (operator or "") in _WRITE_OPS


def audit(command: str, project_root: str = ".") -> CommandAudit:
    """解析一条命令(字符串)并产出审计模型。"""
    try:
        nodes = bashlex.parse(command)
    except Exception as exc:  # ParsingError / 其它语法错误 -> 默认拒绝
        return CommandAudit(parse_error=f"{type(exc).__name__}: {exc}")

    ctx = CommandAudit()
    root = Path(project_root).resolve()
    for node in nodes:  # 顶层可能是单个 list 节点, 由 _walk 递归处理
        _walk(node, ctx, root)
    return ctx


def _walk(node: Any, ctx: CommandAudit, root: Path) -> None:
    kind = getattr(node, "kind", None)
    if kind is None or kind in _SKIP:
        return

    if kind == "command":
        seg = _extract_segment(node, root)
        if seg:
            ctx.segments.append(seg)
        return

    if kind in _CONTAINERS:
        if kind == "pipeline":
            ctx.has_pipeline = True
        if kind in ("for", "while", "until", "select", "if", "case", "function"):
            ctx.has_control_flow = True
        if kind == "compound" and (getattr(node, "subtype", "") or "") == "subshell":
            ctx.has_subshell = True
        for part in _parts(node):
            _walk(part, ctx, root)
        return

    # 命令替换 $( ) / ` `
    if kind in ("commandsubstitution", "command_substitution"):
        ctx.has_command_substitution = True
        inner = CommandAudit()
        for part in _parts(node):
            if isinstance(part, bashlex.ast.node):
                _walk(part, inner, root)
        ctx.nested_commands.append(inner)
        return

    # 其它未知节点: 继续下钻其 parts(不掩盖内部写操作)
    for part in _parts(node):
        if isinstance(part, bashlex.ast.node):
            _walk(part, ctx, root)


def _parts(node: Any) -> list:
    return list(getattr(node, "parts", None) or [])


def _word_value(part: Any) -> Optional[str]:
    w = getattr(part, "word", None)
    if isinstance(w, str):
        return w
    if isinstance(part, str):
        return part
    return None


def _extract_segment(node: Any, root: Path) -> Optional[Segment]:
    seg = Segment(command="")
    for part in _parts(node):
        kind = getattr(part, "kind", None)

        if kind == "word":
            value = _word_value(part)
            if value is None:
                continue
            if not seg.command:
                seg.command = value          # 首 word = 命令名
            else:
                seg.args.append(value)
        elif kind == "assignment":
            continue                          # FOO=bar 前缀, 不当命令名
        elif kind == "redirect":
            redir = _extract_redirection(part, root)
            if redir:
                seg.redirections.append(redir)
        # operator / pipe / 其它子节点: 一律跳过, 绝不 append 进 args

    seg.command = seg.command or ""
    seg.risk_flags = _risk_flags(seg.command, seg.args)
    return seg


def _extract_redirection(node: Any, root: Path) -> Optional[Redirection]:
    operator = getattr(node, "type", None) or ""        # node.type
    out_node = getattr(node, "output", None)            # WordNode | None
    target = _word_value(out_node) if out_node is not None else ""

    is_write = is_write_redirect(operator)
    path = target if (is_write and target and not target.startswith("&")) else None
    return Redirection(operator=operator, target=target, is_write=is_write, path=path)


# ---------------- 高危标记(上下文相关) ----------------

def _risk_flags(command: str, args: list[str]) -> set[str]:
    p = command.lower()
    flags: set[str] = set()

    if p in ("sudo", "doas", "su"):
        flags.add("privilege")
        return flags

    if p in ("curl", "wget"):
        flags.add("download")            # 任意网络下载
        flags.add("footgun")             # curl | sh 风险

    if p in ("rm", "dd", "mkfs", "shred"):
        flags.add("footgun")

    if p in ("pip", "pip3", "npm", "yarn", "pnpm", "gem", "cargo",
             "apt", "apt-get", "yum", "dnf", "brew", "go", "make", "cmake"):
        flags.add("pkg_install")         # 仅记录/提示, build 放行

    if p == "git":
        sub = args[0].lower() if args else ""
        if sub in ("reset", "clean", "rebase", "checkout", "merge", "cherry-pick",
                   "revert", "apply", "switch", "restore", "push", "branch", "delete", "rm", "mv"):
            flags.add("footgun")

    return flags


if __name__ == "__main__":
    # ---- 自测(accept commit2)----
    cmd = 'ls -l > out.txt; cd /tmp; echo "done" | wc -l; grep test < input.txt 2>> err.log'
    a = audit(cmd)

    def check(cond: bool, msg: str) -> None:
        print(("PASS " if cond else "FAIL ") + msg)
        return cond

    ok = True
    ok &= check(a.parse_error is None, f"解析成功 (parse_error={a.parse_error})")
    ok &= check(len(a.segments) == 5, f"segments 数量 == 5 (实际 {len(a.segments)})")
    ok &= check(a.has_pipeline is True, "has_pipeline True")

    by = {s.command: s for s in a.segments if s.command}
    ok &= check("ls" in by and "cd" in by and "echo" in by and "wc" in by and "grep" in by,
                f"包含 ls/cd/echo/wc/grep (实际 {sorted(by)})")

    if "ls" in by:
        r = by["ls"].redirections
        ok &= check(len(r) >= 1 and r[0].is_write and r[0].path == "out.txt",
                    f"ls > out.txt write=True path=out.txt (实际 {r})")

    if "echo" in by:
        ok &= check(by["echo"].args == ["done"], f"echo args==['done'] (实际 {by['echo'].args})")
    if "wc" in by:
        ok &= check(by["wc"].args == ["-l"], f"wc args==['-l'] (实际 {by['wc'].args})")

    if "grep" in by:
        reds = by["grep"].redirections
        read = [x for x in reds if not x.is_write]
        write = [x for x in reds if x.is_write]
        ok &= check(any(x.operator == "<" and x.path is None for x in reds),
                    f"grep < input.txt 只读 (实际 {reds})")
        ok &= check(any(x.operator == ">>" and x.is_write and x.path == "err.log" for x in reds),
                    f"grep 2>> err.log write=True path=err.log (实际 {reds})")

    # 畸形输入: 置 parse_error 而不抛异常
    bad = audit('echo "unclosed')
    ok &= check(bad.parse_error is not None, f"畸形输入置 parse_error (实际 {bad.parse_error})")

    print("\n== 明细 ==")
    for s in a.segments:
        rr = [(x.operator, x.target, x.is_write, x.path) for x in s.redirections]
        print(f"  command={s.command!r} args={s.args} redirs={rr} flags={sorted(s.risk_flags)}")

    print("\n=> commit2 ACCEPTED" if ok else "\n=> commit2 REJECTED")
    raise SystemExit(0 if ok else 1)
