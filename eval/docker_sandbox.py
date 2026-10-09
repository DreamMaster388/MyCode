"""实例级 Docker 沙箱：生命周期 + 命令执行 + 文件读写 + 补丁提取。

B1 方案里 Agent 循环在宿主机，所有"落地操作"都通过 docker exec 进入实例容器，
从而让 Agent 真的看到/修改 SWE-bench 的真实仓库，并能用仓库 conda env 跑测试。

约定（对齐 SWE-bench 官方镜像）：
- 工作目录 /testbed，容器用户 root。
- 仓库 conda env 由 eval_script 指定（通常 testbed）。
- 补丁 = `git add -A && git diff --cached HEAD`（HEAD 即 base_commit）。
"""
from __future__ import annotations

import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

CONTAINER_WORKDIR = "/testbed"
DEFAULT_PY = "/opt/miniconda3/bin/python"

# ---------------------------------------------------------------------------
# 容器内运行的 Python 小脚本（作为 argv 传入 python -c，无 shell 引号问题）
# ---------------------------------------------------------------------------

_READ_SCRIPT = r"""
import os, sys
p, off, lim = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
if os.path.isdir(p):
    for e in sorted(os.listdir(p)):
        print(e + ("/" if os.path.isdir(os.path.join(p, e)) else ""))
    sys.exit(0)
try:
    with open(p, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
except FileNotFoundError:
    print("ERROR: not found: " + p); sys.exit(2)
total = len(lines)
sel = lines[off: off + lim] if lim > 0 else lines[off:]
for i, l in enumerate(sel, start=off + 1):
    print(f"{i}\t{l}")
print(f"[total {total} lines]")
"""

_WRITE_SCRIPT = r"""
import os, sys
p = sys.argv[1]
os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
data = sys.stdin.read()
with open(p, "w", encoding="utf-8") as f:
    f.write(data)
print(f"wrote {len(data)} chars to {p}")
"""

_EDIT_SCRIPT = r"""
import json, sys
d = json.load(sys.stdin)
p, old, new = d["path"], d["old"], d["new"]
with open(p, encoding="utf-8") as f:
    s = f.read()
n = s.count(old)
if n == 0:
    print("ERROR: old_string not found"); sys.exit(2)
if n > 1:
    print(f"ERROR: old_string not unique ({n} matches); add more context"); sys.exit(3)
with open(p, "w", encoding="utf-8") as f:
    f.write(s.replace(old, new, 1))
print(f"edited {p}: replaced 1 occurrence")
"""

_GLOB_SCRIPT = r"""
import glob, os, sys
pattern, base = sys.argv[1], sys.argv[2]
matches = sorted(glob.glob(os.path.join(base, pattern), recursive=True))
for m in matches[:2000]:
    rel = os.path.relpath(m, base)
    print(rel + ("/" if os.path.isdir(m) else ""))
print(f"[{len(matches)} matches]")
"""


@dataclass
class ExecResult:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


class DockerSandbox:
    """管理一个 SWE-bench 实例容器。"""

    def __init__(
        self,
        image: str,
        instance_id: str,
        activation: str = "cd /testbed",
        python_bin: str = DEFAULT_PY,
        network: str = "host",
        name: Optional[str] = None,
        workdir: str = CONTAINER_WORKDIR,
    ) -> None:
        self.image = image
        self.instance_id = instance_id
        self.activation = activation or "cd /testbed"
        self.python_bin = python_bin
        self.network = network
        self.workdir = workdir
        safe = instance_id.replace("/", "_")
        self.name = name or f"agent-eval-{safe}-{uuid.uuid4().hex[:8]}"
        self._started = False

    # ---------------- 底层 ----------------
    def _run(self, args: Sequence[str], input: Optional[str] = None, timeout: Optional[int] = None) -> ExecResult:
        try:
            proc = subprocess.run(
                ["docker", *args],
                input=input,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
            return ExecResult(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = (exc.stderr or "") + f"\n[eval] docker exec timed out after {timeout}s"
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            return ExecResult(124, out, err)
        except FileNotFoundError:
            return ExecResult(127, "", "docker CLI not found on PATH")

    # ---------------- 镜像/容器 ----------------
    def ensure_image(self, pull: bool = True, pull_timeout: int = 7200, retries: int = 3) -> ExecResult:
        inspect = self._run(["image", "inspect", self.image], timeout=60)
        if inspect.ok:
            return inspect
        if not pull:
            return inspect
        last = inspect
        for attempt in range(1, retries + 1):
            print(f"[eval] pulling image {self.image} (attempt {attempt}/{retries}) ...", flush=True)
            last = self._run(["pull", self.image], timeout=pull_timeout)
            if last.ok:
                return last
            # 失败后重试前先清理可能残留的中间层，等下再试
            time.sleep(5 * attempt)
        return last

    def start(self) -> ExecResult:
        # 清理可能残留的同名容器
        self._run(["rm", "-f", self.name], timeout=120)
        res = self._run(
            [
                "run", "-d", "--name", self.name,
                "--network", self.network,
                "-w", self.workdir,
                self.image,
                "sleep", "infinity",
            ],
            timeout=300,
        )
        if res.ok:
            self._started = True
        return res

    def stop(self) -> None:
        if self._started:
            self._run(["rm", "-f", self.name], timeout=180)
            self._started = False
        else:
            self._run(["rm", "-f", self.name], timeout=120)

    # ---------------- 执行 ----------------
    def exec(
        self,
        argv: Sequence[str],
        input: Optional[str] = None,
        timeout: int = 120,
        cwd: Optional[str] = None,
    ) -> ExecResult:
        args = ["exec"]
        if input is not None:
            args.append("-i")
        args += ["-w", cwd or self.workdir, self.name, *argv]
        return self._run(args, input=input, timeout=timeout)

    def bash(self, command: str, timeout: int = 300) -> ExecResult:
        """在仓库 conda 环境下执行 shell 命令。"""
        full = f"{self.activation} && {command}"
        return self.exec(["bash", "-lc", full], timeout=timeout)

    # ---------------- 文件操作 ----------------
    def read_text(self, path: str, offset: int = 0, limit: int = 2000, timeout: int = 120) -> ExecResult:
        return self.exec([self.python_bin, "-c", _READ_SCRIPT, path, str(offset), str(limit)], timeout=timeout)

    def write_text(self, path: str, content: str, timeout: int = 120) -> ExecResult:
        return self.exec([self.python_bin, "-c", _WRITE_SCRIPT, path], input=content, timeout=timeout)

    def edit_text(self, path: str, old: str, new: str, timeout: int = 120) -> ExecResult:
        import json

        payload = json.dumps({"path": path, "old": old, "new": new})
        return self.exec([self.python_bin, "-c", _EDIT_SCRIPT], input=payload, timeout=timeout)

    def glob_files(self, pattern: str, path: str = ".", timeout: int = 120) -> ExecResult:
        return self.exec([self.python_bin, "-c", _GLOB_SCRIPT, pattern, path], timeout=timeout)

    def grep(self, pattern: str, path: str = ".", glob_pat: Optional[str] = None, timeout: int = 180) -> ExecResult:
        p = shlex.quote(pattern)
        t = shlex.quote(path or ".")
        rg = ["rg", "--line-number", "--no-heading", "--with-filename", "--regexp", p]
        if glob_pat:
            rg += ["--glob", shlex.quote(glob_pat)]
        rg.append(t)
        grep = ["grep", "-r", "-n", "-H", "--", p, t]
        cmd = f"(command -v rg >/dev/null 2>&1 && {' '.join(rg)}) || {' '.join(grep)}"
        return self.bash(cmd, timeout=timeout)

    # ---------------- 补丁提取 ----------------
    def model_patch(self, timeout: int = 180) -> ExecResult:
        """清洗构建产物后，提取相对 HEAD（base_commit）的完整改动。"""
        clean = (
            "find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null; "
            "find . -type d \\( -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \\) "
            "-prune -exec rm -rf {} + 2>/dev/null; "
            "find . -name '*.pyc' -delete 2>/dev/null; "
            "git add -A && "
            "git -c core.fileMode=false diff --cached HEAD"
        )
        return self.bash(clean, timeout=timeout)

    def git_head(self) -> str:
        res = self.exec(["git", "rev-parse", "HEAD"], timeout=60)
        return res.out.strip()
