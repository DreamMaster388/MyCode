"""文件路径解析与身份判定 seam。

对齐 deepseek-harness 的 ctx.fs 设计, 核心是把两件事分开:
- 解析(resolve): 决定相对路径如何拼到 base 上, base 只是"解析默认", 不是安全边界。
- 围栏(containment): 由 policy 层用解析出的不透明身份判断是否越界。

身份用 os.path.realpath 计算: 软链、别名、`..` 都由操作系统归一,
同一文件经不同路径访问得到同一个 target_key。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Union

PathLike = Union[str, os.PathLike]

# 冻结实例，对象一旦创建就不可改变
@dataclass(frozen=True)
class FsTarget:
    """一个已解析的路径身份。

    target_key  : 不透明身份(os.path.realpath), 供围栏/新鲜度比对; 消费者不应解析其结构。
    display_path: 展示/日志用路径(已规范化, 但未 realpath, 保留软链写法)。
    """

    target_key: str
    display_path: str


class PathError(Exception):
    """路径解析失败。携带稳定错误码, 调用方按 code 分支而非解析 message。"""

    def __init__(self, message: str, code: str = "FS_INVALID") -> None:
        super().__init__(message)
        self.code = code


def resolve_path(path: PathLike, base: PathLike = ".") -> FsTarget:
    """把调用方给的 path 解析成稳定 FsTarget。

    - 空/纯空白      -> PathError(code="FS_NOT_FOUND")
    - 相对路径       -> 相对 base 拼接
    - 绝对路径       -> 忽略 base
    - 父级是普通文件 -> PathError(code="FS_NOT_DIRECTORY")
    - 目标不存在时 realpath 仍会解析已存在前缀, 因此待创建文件的身份也稳定
    """
    raw = os.fspath(path) if path is not None else ""
    if not str(raw).strip():
        raise PathError("path must be a non-empty string", "FS_NOT_FOUND")

    base_str = os.fspath(base) if base is not None else "."
    if os.path.isabs(raw):
        display = os.path.normpath(raw)
    else:
        display = os.path.abspath(os.path.join(base_str, raw))

    _reject_file_ancestor(display)

    return FsTarget(target_key=os.path.realpath(display), display_path=display)


def _reject_file_ancestor(display: str) -> None:
    """若目标路径的某个已存在祖先是普通文件, 抛 FS_NOT_DIRECTORY。"""
    parent = os.path.dirname(display)
    while parent:
        if os.path.exists(parent):
            if not os.path.isdir(parent):
                raise PathError(f"not a directory: {parent}", "FS_NOT_DIRECTORY")
            return
        nxt = os.path.dirname(parent)
        if nxt == parent:  # 已到根
            return
        parent = nxt


def is_within(root: FsTarget, target: FsTarget) -> bool:
    """target 是否等于 root 或位于 root 之下(按 realpath 身份判断)。

    - 跨盘符(Windows) -> False
    - 大小写: Windows 下用 normcase 归一
    """
    root_key = root.target_key
    target_key = target.target_key
    if not root_key or not target_key:
        return False
    if os.name == "nt":
        root_key = os.path.normcase(root_key)
        target_key = os.path.normcase(target_key)
    try:
        return os.path.commonpath([root_key, target_key]) == root_key
    except ValueError:
        return False