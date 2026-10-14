#!/usr/bin/env python3
"""Landlock 启动器: 先给自身套上内核写围栏, 再 exec 目标命令。

用法(由 LandlockBackend 拼接):
    python landlock_exec.py --mode workspace-write \\
        --write-root /path/ws --write-root /tmp -- /bin/bash -c "<command>"

设计要点:
- **只处理写相关权限**(write/remove/make/refer/truncate), 不处理读/执行 ->
  无需为整个文件系统构造读规则, 读行为不受影响。
- 必须在 **exec 之前** 应用, 且只作用于"子进程"(本启动器), 不会关住 agent 自身。
- Landlock 规则对 exec 后的进程**继续生效**, 因此天然覆盖 bash 内部再派生的
  `env`/`xargs`/`sh -c`/`$(...)`/重定向等所有子命令。
- 本文件刻意不 import 本仓库任何包, 以便被当作独立脚本按路径直接调用
  (不依赖 cwd / PYTHONPATH)。

这是 Linux 专有实现。不可用时由上层 fail-closed。
"""
from __future__ import annotations

import ctypes
import os
import sys

# ---- 退出码约定: 125 = 启动器自身失败(应视为"沙箱不可用", 而非命令被拒) ----
LAUNCHER_ERROR_EXIT = 125
LAUNCHER_ERROR_PREFIX = "[sandbox-launcher]"

# landlock 系统调用号(x86_64 / aarch64 / riscv64 均为这套通用号)。
_NR_CREATE_RULESET = 444
_NR_ADD_RULE = 445
_NR_RESTRICT_SELF = 446

_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

# landlock 访问权限位(按 ABI 递增)。
_ACCESS_FS = {
    "execute": 1 << 0,
    "write_file": 1 << 1,
    "read_file": 1 << 2,
    "read_dir": 1 << 3,
    "remove_dir": 1 << 4,
    "remove_file": 1 << 5,
    "make_char": 1 << 6,
    "make_dir": 1 << 7,
    "make_reg": 1 << 8,
    "make_sock": 1 << 9,
    "make_fifo": 1 << 10,
    "make_block": 1 << 11,
    "make_sym": 1 << 12,
    "refer": 1 << 13,      # ABI >= 2
    "truncate": 1 << 14,   # ABI >= 3
}
_MAKE_RIGHTS = (
    "make_char", "make_dir", "make_reg", "make_sock",
    "make_fifo", "make_block", "make_sym",
)

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long
_libc.prctl.restype = ctypes.c_int
_libc.prctl.argtypes = [
    ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
]


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    # 内核定义: struct landlock_path_beneath_attr { __u64 allowed_access; __s32 parent_fd; } __packed;
    # allowed_access 是 64 位; ctypes 默认对齐会把 parent_fd 放在偏移 8, 与内核一致。
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


def _syscall(number: int, *args):
    res = _libc.syscall(ctypes.c_long(number), *args)
    if res < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return res


def _detect_abi() -> int:
    """返回内核支持的 Landlock ABI 版本; 不支持时抛 OSError。"""
    res = _libc.syscall(
        ctypes.c_long(_NR_CREATE_RULESET),
        ctypes.c_void_p(0),
        ctypes.c_size_t(0),
        ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION),
    )
    if res < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return int(res)


def _handled_rights(abi: int) -> int:
    """按 ABI 计算"我们要接管"的写权限集合(读/执行一律不碰)。"""
    names = ["write_file", "remove_dir", "remove_file", *_MAKE_RIGHTS]
    if abi >= 2:
        names.append("refer")
    if abi >= 3:
        names.append("truncate")
    bits = 0
    for name in names:
        bits |= _ACCESS_FS[name]
    return bits


def _add_path_rule(ruleset_fd: int, path: str, allowed: int) -> bool:
    """给一个路径(文件或目录)授予 allowed 权限。路径不存在则跳过。"""
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        attr = _PathBeneathAttr(allowed_access=allowed, parent_fd=fd)
        _syscall(
            _NR_ADD_RULE,
            ctypes.c_int(ruleset_fd),
            ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH),
            ctypes.byref(attr),
            ctypes.c_uint32(0),
        )
        return True
    finally:
        os.close(fd)


def _apply(write_roots: list[str]) -> None:
    """给当前进程套上写围栏: 只允许 write_roots(+ /dev/null) 可写。"""
    abi = _detect_abi()
    handled = _handled_rights(abi)

    attr = _RulesetAttr(handled_access_fs=handled)
    ruleset_fd = _syscall(
        _NR_CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)),
        ctypes.c_uint32(0),
    )
    try:
        seen: set[str] = set()
        for root in write_roots:
            real = os.path.realpath(root)
            if real in seen:
                continue
            seen.add(real)
            _add_path_rule(ruleset_fd, real, handled)
        # 惯例放行 /dev/null(常见重定向目标)。
        _add_path_rule(ruleset_fd, "/dev/null", _ACCESS_FS["write_file"])

        if _libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            errno = ctypes.get_errno()
            raise OSError(errno, os.strerror(errno))
        _syscall(_NR_RESTRICT_SELF, ctypes.c_int(ruleset_fd), ctypes.c_uint32(0))
    finally:
        os.close(ruleset_fd)


def _parse(argv: list[str]):
    mode = "workspace-write"
    roots: list[str] = []
    probe = False
    i = 0
    command: list[str] = []
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            command = argv[i + 1:]
            break
        if tok == "--mode":
            i += 1
            mode = argv[i]
        elif tok == "--write-root":
            i += 1
            roots.append(argv[i])
        elif tok == "--probe":
            probe = True
        else:
            raise ValueError(f"unknown argument: {tok}")
        i += 1
    return mode, roots, probe, command


def _fail(message: str) -> "None":
    sys.stderr.write(f"{LAUNCHER_ERROR_PREFIX} {message}\n")
    sys.exit(LAUNCHER_ERROR_EXIT)


def main(argv: list[str]) -> int:
    try:
        mode, roots, probe, command = _parse(argv)
    except (ValueError, IndexError) as exc:
        _fail(f"bad arguments: {exc}")

    if mode == "read-only":
        roots = []
    elif mode not in ("workspace-write", "danger-full-access"):
        _fail(f"unknown mode: {mode}")

    try:
        _apply(roots)
    except OSError as exc:
        _fail(f"landlock unavailable: {exc}")

    if probe:
        return 0

    if not command:
        _fail("no command to execute")
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        _fail(f"exec failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
