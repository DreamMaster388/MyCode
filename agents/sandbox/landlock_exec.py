#!/usr/bin/env python3
"""Landlock 启动器: 先给自身套上内核读写围栏, 再 exec 目标命令。

用法(由 LandlockBackend 拼接):
    python landlock_exec.py --mode workspace-write \\
        --read-root /usr --read-root /path/project \\
        --write-root /path/ws -- /bin/bash -c "<command>"

设计要点:
- **同时处理读与写**: 读根授予 read_file/read_dir, 写根额外授予写相关权限
  (write/remove/make/refer/truncate); execute 不接管, 因此二进制仍可执行。
- 未被任何规则授予的读/写一律拒绝(Landlock 是"默认拒绝"的白名单模型)。
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
# 读权限: 读文件内容 + 列目录(打开文件求 read_file, readdir 求 read_dir)。
_READ_RIGHTS = ("read_file", "read_dir")

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


def _access_bits(names) -> int:
    """把权限名集合转成位掩码。"""
    bits = 0
    for name in names:
        bits |= _ACCESS_FS[name]
    return bits


def _handled_rights(abi: int) -> int:
    """按 ABI 计算"我们要接管"的读/写权限集合(execute 一律不碰)。"""
    names = [*_READ_RIGHTS, "write_file", "remove_dir", "remove_file", *_MAKE_RIGHTS]
    if abi >= 2:
        names.append("refer")
    if abi >= 3:
        names.append("truncate")
    return _access_bits(names)


def _rule_access(read_roots, write_roots, read_bits: int,
                 write_bits: int) -> dict[str, int]:
    """计算每个路径应授予的访问位: 读根只读, 写根读写; 同路径合并去重。

    读根可能是文件(如 /etc/passwd): 文件只授予 read_file, 因为对文件
    授予 read_dir 会被内核以 EINVAL 拒绝; 目录授予完整的 read 位。
    返回 {realpath: allowed_access}; 插入顺序保留, 便于确定性测试。
    """
    access: dict[str, int] = {}
    for root in read_roots:
        real = os.path.realpath(root)
        bits = read_bits if os.path.isdir(real) else _ACCESS_FS["read_file"]
        access[real] = access.get(real, 0) | bits
    for root in write_roots:
        real = os.path.realpath(root)
        access[real] = access.get(real, 0) | read_bits | write_bits
    return access


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


def _apply(read_roots: list[str], write_roots: list[str]) -> None:
    """给当前进程套上读写围栏。

    - 读根: 目录授予 read_file/read_dir, 文件只授予 read_file;
    - 写根: 额外授予 write/remove/make/refer/truncate(故写根也可读);
    - /dev/null 额外放行 write_file(常见重定向目标);
    - 未被授予的读/写一律由内核拒绝。
    """
    abi = _detect_abi()
    handled = _handled_rights(abi)
    read_bits = _access_bits(_READ_RIGHTS)
    write_bits = handled & ~read_bits

    attr = _RulesetAttr(handled_access_fs=handled)
    ruleset_fd = _syscall(
        _NR_CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)),
        ctypes.c_uint32(0),
    )
    try:
        for real, allowed in _rule_access(
            read_roots, write_roots, read_bits, write_bits
        ).items():
            _add_path_rule(ruleset_fd, real, allowed)
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
    read_roots: list[str] = []
    write_roots: list[str] = []
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
        elif tok == "--read-root":
            i += 1
            read_roots.append(argv[i])
        elif tok == "--write-root":
            i += 1
            write_roots.append(argv[i])
        elif tok == "--probe":
            probe = True
        else:
            raise ValueError(f"unknown argument: {tok}")
        i += 1
    return mode, read_roots, write_roots, probe, command


def _fail(message: str) -> "None":
    sys.stderr.write(f"{LAUNCHER_ERROR_PREFIX} {message}\n")
    sys.exit(LAUNCHER_ERROR_EXIT)


def main(argv: list[str]) -> int:
    try:
        mode, read_roots, write_roots, probe, command = _parse(argv)
    except (ValueError, IndexError) as exc:
        _fail(f"bad arguments: {exc}")

    if mode == "read-only":
        write_roots = []
    elif mode not in ("workspace-write", "danger-full-access"):
        _fail(f"unknown mode: {mode}")

    try:
        _apply(read_roots, write_roots)
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
