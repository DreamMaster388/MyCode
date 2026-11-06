"""容器内文件工具路径守卫。

本模块源码会被拼进 `python -c` 脚本，在 SWE-bench 实例容器里先于真正的
Read/Write/Edit/Grep/Glob I/O 执行。宿主机不做路径解析，只消费退出码
(DENY_EXIT) 和 DENIAL_MARKER，据此返回 ACCESS_DENIED。

默认语义与本地 trusted 模式对齐: 全盘可读; 仅 /testbed、/tmp 及 /dev/null
可写。目标路径按容器内 realpath 归一，软链逃逸与 `..` 都按真实身份判定。
"""
import os
import sys

DENIAL_MARKER = "[sandbox: file access denied]"
DENY_EXIT = 13
CONTAINER_WORKDIR = "/testbed"
READ_ROOTS = ("/",)
WRITE_ROOTS = ("/testbed", "/tmp")
WRITE_FILES = ("/dev/null",)


def resolve_path(path, cwd=CONTAINER_WORKDIR):
    """把调用方路径解析成容器内真实身份; 相对路径按 cwd 解析。"""
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.realpath(path)


def is_within(root, target):
    """target 是否位于 root 之内; 按路径分量比较, 避免 /testbed-other 误放行。"""
    root = os.path.realpath(root)
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False


def check(path, kind, cwd=CONTAINER_WORKDIR):
    """返回拒绝原因; 允许访问时返回 None。"""
    target = resolve_path(path, cwd)
    if kind == "read":
        if any(is_within(root, target) for root in READ_ROOTS):
            return None
        return "read target '%s' is outside readable roots." % target
    if kind == "write":
        if target in WRITE_FILES:
            return None
        if any(is_within(root, target) for root in WRITE_ROOTS):
            return None
        return "write target '%s' is outside writable roots %s." % (
            target, list(WRITE_ROOTS),
        )
    raise ValueError("unknown access kind: %r" % (kind,))


def enforce(path, kind, cwd=CONTAINER_WORKDIR):
    """容器脚本内的强制入口: 越界时打印标记并以 DENY_EXIT 退出。"""
    message = check(path, kind, cwd)
    if message is None:
        return
    print("%s %s" % (DENIAL_MARKER, message), flush=True)
    raise SystemExit(DENY_EXIT)


def main(argv=None):
    """Grep 等 shell 命令使用的 CLI: 参数为 kind cwd path [path ...]。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3:
        sys.stderr.write(
            "usage: container_path_guard <read|write> <cwd> <path> [path ...]\n"
        )
        return 2
    kind, cwd = args[0], args[1]
    for path in args[2:]:
        message = check(path, kind, cwd)
        if message is not None:
            print("%s %s" % (DENIAL_MARKER, message), flush=True)
            return DENY_EXIT
    return 0


def module_source():
    """本文件源码: 供 DockerSandbox 拼进容器侧 python -c。"""
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        return fh.read()
