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
DEFAULT_NETWORK = "none"

# 容器启动时注入的环境变量：
# - 代理指向 127.0.0.1:9（discard 端口），让联网命令立即失败而非长时间等待；
# - pip/HF/transformers 强制离线，禁止下载依赖或模型；
# - 关闭字节码与 pytest 缓存，避免污染补丁。
CONTAINER_ENV = {
    "PIP_NO_INDEX": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTEST_ADDOPTS": "-p no:cacheprovider",
    "TMPDIR": "/tmp",
    "http_proxy": "http://127.0.0.1:9",
    "https_proxy": "http://127.0.0.1:9",
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
}

# 补丁过滤阈值：单个文件 / 整份补丁超过 1MB 都裁掉，避免超大 patch 拖垮判分。
PATCH_MAX_BYTES = 1_000_000
PATCH_MAX_FILE_BYTES = 1_000_000

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

# 补丁提取：清缓存 → git add -A → 过滤 → 输出 diff。
# 过滤规则（对齐 300 例全量跑出的脏补丁）：
# - 二进制文件（git 判定 binary 的、图片/字体/压缩包等）；
# - 构建产物（__pycache__、*.pyc、build/、dist/、*.egg-info、node_modules、缓存目录）；
# - 临时文件（downloads/、tmp*、downloads 解压目录、repro/scratch 脚本等）；
# - 文档（docs/、*.rst/.md/.txt、CHANGES/README 等）；
# - 测试文件（instance.test_patch / test_files 中的路径 + tests/testing 目录 + test_*.py）；
# - 单文件或整份补丁超过 max_bytes 的超大 patch。
_PATCH_SCRIPT = r"""
import json, os, re, shutil, subprocess, sys

workdir, cfg_json = sys.argv[1], sys.argv[2]
cfg = json.loads(cfg_json)
exclude = [p.strip("/") for p in (cfg.get("exclude_paths") or []) if p and p.strip("/")]
max_total = int(cfg.get("max_bytes") or 1000000)
max_file = int(cfg.get("max_file_bytes") or 1000000)
os.chdir(workdir)

JUNK_DIRS = {
    "downloads", "build", "dist", ".eggs", ".tox", ".nox", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    ".venv", "venv", "site-packages", "tmp", "tmp_pkg", "tmp_repro", "tmpdl",
    "_scratch", "scratch", "tmp_repo", "tmp_unroll",
}
JUNK_SUFFIXES = (
    ".pyc", ".pyo", ".so", ".o", ".a", ".class", ".jar", ".whl", ".tar.gz",
    ".tgz", ".zip", ".sqlite3", ".db", ".log", ".pid", ".swp", ".orig",
    ".rej", ".bak", ".tmp", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".pdf", ".woff", ".woff2", ".ttf", ".eot",
)
JUNK_NAMES = {".DS_Store", "coverage.xml", ".coverage"}
# 只匹配"完整临时脚本名"（repro.py / repro_2.py / tmp_repro.py ...），
# 避免误伤 tmpdir.py、templates.py、debug.py 这类正常源码文件。
JUNK_NAME_RE = re.compile(
    r"^(?:(?:repro|scratch|tmp|temp)(?:[0-9]+|[_-][\w.-]+)?"
    r"|fetch_tmp|gettests|extract_tests|geturl|ping)"
    r"\.(?:py|txt|sh|log|out)$"
)
DOC_SUFFIXES = (".rst", ".md", ".txt", ".1", ".man", ".tex", ".html", ".po")
DOC_NAMES = {
    "CHANGES", "CHANGES.rst", "CHANGES.md", "README", "README.rst", "README.md",
    "AUTHORS", "AUTHORS.rst", "LICENSE", "LICENSE.txt", "NEWS", "NEWS.rst",
}
CLEAN_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def git(*args, input=None):
    return subprocess.run(["git", *args], input=input, capture_output=True)


def is_excluded(path):
    for e in exclude:
        if path == e or path.startswith(e + "/"):
            return True
    return False


def is_test(path):
    parts = path.split("/")
    name = parts[-1]
    if any(p in ("tests", "testing") for p in parts[:-1]):
        return True
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def is_junk(path):
    parts = path.split("/")
    if any(p in JUNK_DIRS or p.endswith(".egg-info") or p.startswith("tmp_") for p in parts[:-1]):
        return True
    if parts[0].startswith(("tmp_", "_scratch")):
        return True
    name = parts[-1]
    if name in JUNK_NAMES or name.endswith(JUNK_SUFFIXES):
        return True
    return bool(JUNK_NAME_RE.match(name))


def is_doc(path):
    parts = path.split("/")
    name = parts[-1]
    if any(p in ("doc", "docs", "changelog", "changes") for p in parts[:-1]):
        return True
    if name in DOC_NAMES or name.endswith(DOC_SUFFIXES):
        return True
    return bool(re.match(r"^(?:changelog|news|release[-_]?notes)", name, re.I))


def clean_artifacts():
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d != ".git"]
        for d in list(dirs):
            if d in CLEAN_DIRS:
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                dirs.remove(d)
        for f in files:
            if f.endswith((".pyc", ".pyo")):
                try:
                    os.remove(os.path.join(root, f))
                except OSError:
                    pass


def split_segments(diff):
    starts = [m.start() for m in re.finditer(r"^diff --git ", diff, re.M)]
    if not starts:
        return []
    starts.append(len(diff))
    return [diff[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]


def seg_path(seg):
    m = re.match(r"diff --git a/(\S+) b/(\S+)", seg)
    return m.group(2) if m else ""


clean_artifacts()
git("add", "-A")

names = [
    p for p in git("diff", "--cached", "HEAD", "--name-only", "-z")
    .stdout.decode("utf-8", "replace").split("\0") if p
]
kept = [p for p in names if not is_excluded(p) and not is_test(p) and not is_junk(p) and not is_doc(p)]

# 用索引里的 blob 大小剔除超大文件；删除的文件查不到，按 0 处理。
size_paths = [p for p in kept if "\n" not in p]
sizes = {}
if size_paths:
    payload = "".join(":%s\n" % p for p in size_paths).encode("utf-8", "replace")
    lines = git("cat-file", "--batch-check", input=payload).stdout.decode("utf-8", "replace").splitlines()
    for p, line in zip(size_paths, lines):
        fields = line.split()
        if len(fields) == 3 and fields[1] == "blob":
            try:
                sizes[p] = int(fields[2])
            except ValueError:
                pass

dropped = []
for p in list(kept):
    if sizes.get(p, 0) > max_file:
        dropped.append("%s (file %dB)" % (p, sizes[p]))
kept = [p for p in kept if sizes.get(p, 0) <= max_file]

diff_args = ["-c", "core.fileMode=false", "diff", "--cached", "HEAD"]
if kept and len(kept) <= 500 and sum(len(p) for p in kept) <= 60000:
    diff_args += ["--", *kept]
raw = git(*diff_args).stdout.decode("utf-8", "replace")

final = []
for seg in split_segments(raw):
    p = seg_path(seg)
    if not p or is_excluded(p) or is_test(p) or is_junk(p) or is_doc(p):
        continue
    if "GIT binary patch" in seg or re.search(r"^Binary files .* differ$", seg, re.M):
        dropped.append(p + " (binary)")
        continue
    if "\ufffd" in seg:
        # 原文含非 UTF-8 字节：解码已被替换，补丁无法可靠 apply，整段丢弃。
        dropped.append(p + " (non-utf8)")
        continue
    seg_size = len(seg.encode("utf-8", "replace"))
    if seg_size > max_file:
        dropped.append("%s (file %dB)" % (p, seg_size))
        continue
    final.append((seg_size, seg))

total = sum(s for s, _ in final)
if total > max_total:
    order = sorted(range(len(final)), key=lambda i: final[i][0], reverse=True)
    keep_idx = set(range(len(final)))
    for i in order:
        if total <= max_total:
            break
        keep_idx.discard(i)
        total -= final[i][0]
        dropped.append(seg_path(final[i][1]) + " (over total budget)")
    final = [final[i] for i in sorted(keep_idx)]

sys.stdout.buffer.write("".join(seg for _, seg in final).encode("utf-8", "replace"))
sys.stderr.write(
    "patch-filter: kept=%d dropped=%d skipped=%d tests=%d excluded=%d\n"
    % (len(final), len(dropped), len(names) - len(kept), sum(1 for p in names if is_test(p)),
       sum(1 for p in names if is_excluded(p)))
)
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
        network: str = DEFAULT_NETWORK,
        name: Optional[str] = None,
        workdir: str = CONTAINER_WORKDIR,
        env: Optional[dict] = None,
    ) -> None:
        self.image = image
        self.instance_id = instance_id
        self.activation = activation or "cd /testbed"
        self.python_bin = python_bin
        self.network = network
        self.workdir = workdir
        self.env = {**CONTAINER_ENV, **(env or {})}
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
                # 仓库里可能存在非 UTF-8 文件（diff 输出会带上原始字节），
                # 解码失败时替换而不是抛 UnicodeDecodeError。
                errors="replace",
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
        args = [
            "run", "-d", "--name", self.name,
            "--network", self.network,
            # 禁止进程通过 setuid 等方式提权
            "--security-opt", "no-new-privileges:true",
            "-w", self.workdir,
        ]
        for key, value in self.env.items():
            args += ["-e", f"{key}={value}"]
        args += [self.image, "sleep", "infinity"]
        res = self._run(args, timeout=300)
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
    def model_patch(
        self,
        exclude_paths: Optional[Sequence[str]] = None,
        max_bytes: int = PATCH_MAX_BYTES,
        max_file_bytes: int = PATCH_MAX_FILE_BYTES,
        timeout: int = 300,
    ) -> ExecResult:
        """清洗产物并提取相对 HEAD（base_commit）的改动，过滤脏文件。

        exclude_paths: 需要排除的路径（通常是 instance 的测试文件）。
        max_bytes/max_file_bytes: 整份/单文件补丁大小上限，超出则裁掉。
        """
        import json

        cfg = {
            "exclude_paths": list(exclude_paths or []),
            "max_bytes": max_bytes,
            "max_file_bytes": max_file_bytes,
        }
        return self.exec(
            [self.python_bin, "-c", _PATCH_SCRIPT, self.workdir, json.dumps(cfg)],
            timeout=timeout,
        )

    def git_head(self) -> str:
        res = self.exec(["git", "rev-parse", "HEAD"], timeout=60)
        return res.out.strip()
