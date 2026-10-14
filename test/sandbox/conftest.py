"""沙箱测试共用夹具。

关键点: pytest 的 tmp_path 落在 /tmp 下, 而 /tmp 本身是本方案放行的可写根,
因此"越界"用例必须在 /tmp 之外挑一个用户可写目录(放 HOME 下)才有意义。
"""
import os
import shutil
import tempfile

import pytest

from agents.fs import SandboxMode, build_policy


@pytest.fixture
def home_sandbox():
    """在 $HOME 下开一个临时基目录, 提供工作区与越界路径。

    返回 (policy, ws, outside_file):
    - ws: workspace-write 的根(应可写);
    - outside_file: ws 之外的兄弟路径(应被拒)。
    若 HOME 位于 /tmp(会导致越界判定失真)则跳过。
    """
    home = os.path.expanduser("~")
    if os.path.realpath(home).startswith("/tmp"):
        pytest.skip("HOME under /tmp; cannot pick a non-allowed writable dir")
    base = tempfile.mkdtemp(prefix="sandbox-home-", dir=home)
    ws = os.path.join(base, "ws")
    os.makedirs(ws, exist_ok=True)
    outside_file = os.path.join(base, "outside.txt")
    try:
        yield build_policy(SandboxMode.WORKSPACE_WRITE, ws), ws, outside_file
    finally:
        shutil.rmtree(base, ignore_errors=True)
