"""CLI 文本解析的小工具。

命令统一以 `/` 开头; 这里只负责把文本切成 (命令名, 参数列表),
具体子命令语义交给各 Command 处理。
"""
from __future__ import annotations

import shlex


def parse_command(text: str) -> tuple[str, list[str]]:
    """把 `/name arg1 arg2` 解析成 ("name", ["arg1", "arg2"])。

    非法输入(空文本、不以 / 开头、引号不配对)返回 ("", [])。
    使用 shlex 支持带空格的路径用引号包裹。
    """
    try:
        tokens = shlex.split((text or "").strip())
    except ValueError:
        return "", []
    if not tokens or not tokens[0].startswith("/"):
        return "", []
    return tokens[0][1:], tokens[1:]
