"""审批请求与批准决策。

专用工具在访问可读/可写根之外的路径时, 通过 `ApprovalHandler` 向用户询问。
本模块只依赖 agents/fs, 不导入 mode/agent, 避免循环依赖:
- `ApprovalRequest` / `ApprovalDecision` 是纯数据;
- `ConsoleApprovalHandler` 走终端交互;
- `DenyAllHandler` 是无头/评测环境的 fail-closed 默认值。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from ..fs import AccessKind, FsTarget, resolve_path


class ApprovalDecision(str, Enum):
    """对一次外部路径访问的批准结果。"""

    ALLOW = "allow"     # 策略内直接放行, 没有询问用户
    DENY = "deny"       # 拒绝
    ONCE = "once"       # 只允许本次调用
    ALWAYS = "always"   # 永久批准, 扩展用户批准目录


@dataclass(frozen=True)
class ApprovalRequest:
    """一次需要审批的路径访问。

    mode 由 ModeGuard 在询问前填入字符串值; 工具构造时可以留空。
    """

    kind: AccessKind
    target: FsTarget
    tool_name: str
    mode: str = ""
    detail: str = ""


@runtime_checkable
class ApprovalHandler(Protocol):
    """审批交互的抽象: 返回用户对本次请求的决定。"""

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        ...


def approval_root(target: FsTarget) -> FsTarget:
    """永久批准时实际加入的目录。

    目标是目录则取自身, 否则取父目录 —— 这样批准的是"目录", 与
    ModeGuard 的批准目录模型以及 bash 读根(只保留已存在目录)保持一致。
    """
    path = target.target_key
    if os.path.isdir(path):
        return target
    parent = os.path.dirname(path)
    if not parent or parent == path:
        return target
    return resolve_path(parent, parent)


class DenyAllHandler:
    """无头环境的默认审批器: 一律拒绝(fail-closed)。"""

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.DENY


class ConsoleApprovalHandler:
    """终端审批: 提示写到 stderr, 从 stdin 读取单行选择。

    o = 仅本次, a = 永久(扩展批准目录), d = 拒绝;
    EOF、空行或非法输入一律按拒绝处理。
    """

    _CHOICES = {
        "o": ApprovalDecision.ONCE,
        "a": ApprovalDecision.ALWAYS,
        "d": ApprovalDecision.DENY,
    }

    def __init__(self, input_stream=None, output_stream=None) -> None:
        self._input = input_stream if input_stream is not None else sys.stdin
        self._output = output_stream if output_stream is not None else sys.stderr

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        kind = request.kind if isinstance(request.kind, AccessKind) else AccessKind(request.kind)
        kind_label = "读取" if kind is AccessKind.READ else "写入"
        root = approval_root(request.target)
        lines = [
            f"[sandbox] 工具 '{request.tool_name}' 请求{kind_label}项目外路径:",
            f"  {request.target.display_path}",
            "永久批准将放行目录:",
            f"  {root.display_path}",
        ]
        if request.detail:
            lines.append(f"说明: {request.detail}")
        if request.mode:
            lines.append(f"当前模式: {request.mode}")
        lines.append("选择 [o] 仅本次 / [a] 永久 / [d] 拒绝 > ")
        self._output.write("\n".join(lines))
        self._output.flush()

        try:
            answer = self._input.readline()
        except (EOFError, OSError):
            return ApprovalDecision.DENY
        if not answer:
            return ApprovalDecision.DENY
        return self._CHOICES.get(answer.strip().lower(), ApprovalDecision.DENY)
