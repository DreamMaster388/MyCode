"""`/allow` 命令: 查看与管理用户批准目录。

用法:
    /allow                         列出当前读/写批准目录
    /allow list                    同上
    /allow read <path>             添加可读批准目录
    /allow write <path>            添加可写批准目录
    /allow remove read <path>      移除可读批准目录
    /allow remove write <path>     移除可写批准目录

路径相对项目根解析; 添加时要求是已存在的目录。批准结果直接写入
ModeGuard, 因此专用工具与 bash 的下一次调用立即生效。
"""
from __future__ import annotations

import os

from ...core.mode import ModeGuard
from ...fs import PathError, FsTarget, resolve_path
from ..base import Command
from ..parsing import parse_command

_USAGE = (
    "用法: /allow | /allow read <path> | /allow write <path> | "
    "/allow remove read|write <path>"
)


class AllowCommand(Command):
    def __init__(self, name: str = "allow", description: str = "管理用户批准目录"):
        super().__init__(name, description)

    def execute_command(self, text: str, guard: ModeGuard):
        name, args = parse_command(text)
        if name.lower() != self.name:
            print(_USAGE)
            return
        if not args or args[0].lower() == "list":
            self._list_roots(guard)
            return

        action = args[0].lower()
        if action in ("read", "write"):
            self._add_root(guard, action, args[1:])
            return
        if action == "remove":
            self._remove_root(guard, args[1:])
            return
        print(_USAGE)

    # ---------------- 子命令 ----------------

    def _add_root(self, guard: ModeGuard, kind: str, args: list[str]) -> None:
        if not args:
            print(_USAGE)
            return
        target = self._existing_directory(guard, args[0])
        if target is None:
            return

        if kind == "read":
            existed = target.target_key in {
                root.target_key for root in guard.approved_read_roots
            }
            guard.add_read_root(target.display_path)
            label = "可读"
        else:
            existed = target.target_key in {
                root.target_key for root in guard.approved_write_roots
            }
            guard.add_write_root(target.display_path)
            label = "可写"

        if existed:
            print(f"目录已在{label}批准列表中: {target.display_path}")
        else:
            print(f"已批准{label}目录: {target.display_path}")

    def _remove_root(self, guard: ModeGuard, args: list[str]) -> None:
        if len(args) < 2 or args[0].lower() not in ("read", "write"):
            print(_USAGE)
            return
        kind, path = args[0].lower(), args[1]
        try:
            if kind == "read":
                removed = guard.remove_read_root(path)
            else:
                removed = guard.remove_write_root(path)
        except PathError as exc:
            print(f"无法解析路径: {exc}")
            return

        if removed:
            print(f"已移除{('可读' if kind == 'read' else '可写')}批准目录: {path}")
        else:
            print(f"该目录不在{('可读' if kind == 'read' else '可写')}批准列表中: {path}")

    def _list_roots(self, guard: ModeGuard) -> None:
        for label, roots in (
            ("可读", guard.approved_read_roots),
            ("可写", guard.approved_write_roots),
        ):
            print(f"{label}批准目录:")
            if not roots:
                print("  (无)")
            else:
                for root in sorted(roots, key=lambda item: item.display_path):
                    print(f"  {root.display_path}")

    def _existing_directory(self, guard: ModeGuard, path: str) -> FsTarget | None:
        try:
            target = resolve_path(path, guard.project_root)
        except PathError as exc:
            print(f"无法解析路径: {exc}")
            return None
        if not os.path.isdir(target.target_key):
            print(f"目录不存在或不是目录: {target.display_path}")
            return None
        return target
