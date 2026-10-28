"""从实例工作区提取可判分的 model_patch（只依赖标准库）。

为什么要独立成文件
------------------
补丁提取以前是内嵌在 `docker_sandbox._PATCH_SCRIPT` 里的一大段脚本，无法单测。
现在规则与流程都在本模块，宿主机可直接 `import eval.build_patch` 覆盖纯函数，
也可以用临时 git 仓库对 `collect()` 做集成测试。

容器内怎么用
------------
官方 SWE-bench 镜像里没有本仓库的包，不能 `import eval.build_patch`。
`docker_sandbox._PATCH_SCRIPT` 只做 IO：把本文件的源码经 stdin 送进容器，
exec 成模块后调用 `main(workdir, cfg_json)`，stdout 写补丁、stderr 写统计。

独立运行（调试用）
------------------
    python build_patch.py /testbed '{"exclude_paths": ["tests/test_x.py"]}'

过滤规则（对齐 300 例全量跑出来的脏补丁）
----------------------------------------
- 测试文件：instance.test_patch / test_files 中的路径，tests、testing 目录，
  test_*.py、*_test.py、conftest.py（判分会重置测试，改了也白改）；
- 二进制：git 判定的 binary、图片/字体/压缩包/数据库等后缀，以及含非 UTF-8
  字节的段（解码后带 U+FFFD 的补丁无法可靠 apply）；
- 构建产物：__pycache__、*.pyc/.pyo/.so/.o/.a、build/、dist/、*.egg-info、
  node_modules、各类缓存目录；
- 临时文件：downloads/、tmp*/tmpdl/_scratch/、repro*.py、scratch_* 等；
- 文档：doc/、docs/、changelog/、*.rst/.md/.txt、CHANGES/README 等；
- 超大补丁：单文件或整份补丁超过上限（默认 1MB），从最大的文件开始丢弃。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# 补丁过滤阈值：单个文件 / 整份补丁超过 1MB 都裁掉，避免超大 patch 拖垮判分。
PATCH_MAX_BYTES = 1_000_000
PATCH_MAX_FILE_BYTES = 1_000_000

# ---------------------------------------------------------------- 路径规则 --

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
# 只匹配“完整临时脚本名”（repro.py / repro_2.py / tmp_repro.py ...），
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
DOC_NAME_RE = re.compile(r"^(?:changelog|news|release[-_]?notes)", re.I)
CLEAN_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def is_excluded_path(path: str, exclude_paths: Iterable[str] = ()) -> bool:
    """path 是否命中显式排除列表（文件本身或它的父目录）。"""
    for item in exclude_paths:
        if path == item or path.startswith(item + "/"):
            return True
    return False


def is_test_path(path: str) -> bool:
    """测试文件：tests/testing 目录、test_*.py、*_test.py、conftest.py。"""
    parts = path.split("/")
    name = parts[-1]
    if any(p in ("tests", "testing") for p in parts[:-1]):
        return True
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def is_junk_path(path: str) -> bool:
    """构建产物 / 临时文件 / 二进制资源。"""
    parts = path.split("/")
    if any(p in JUNK_DIRS or p.endswith(".egg-info") or p.startswith("tmp_") for p in parts[:-1]):
        return True
    if parts[0].startswith(("tmp_", "_scratch")):
        return True
    name = parts[-1]
    if name in JUNK_NAMES or name.endswith(JUNK_SUFFIXES):
        return True
    return bool(JUNK_NAME_RE.match(name))


def is_doc_path(path: str) -> bool:
    """文档：docs/changelog 目录、常见文档后缀、CHANGES/README 等。"""
    parts = path.split("/")
    name = parts[-1]
    if any(p in ("doc", "docs", "changelog", "changes") for p in parts[:-1]):
        return True
    if name in DOC_NAMES or name.endswith(DOC_SUFFIXES):
        return True
    return bool(DOC_NAME_RE.match(name))


def classify_path(path: str, exclude_paths: Iterable[str] = ()) -> Optional[str]:
    """返回该路径的排除原因，None 表示应保留。"""
    if not path or path == "/dev/null":
        return "empty"
    if is_excluded_path(path, exclude_paths):
        return "excluded"
    if is_test_path(path):
        return "test"
    if is_junk_path(path):
        return "junk"
    if is_doc_path(path):
        return "doc"
    return None


# ---------------------------------------------------------------- diff 处理 --

class FilterResult:
    """一次过滤的结果。"""

    def __init__(
        self,
        text: str,
        kept: List[str],
        dropped: List[Tuple[str, str]],
        skipped: List[Tuple[str, str]],
    ) -> None:
        self.text = text          # 可直接 git apply 的补丁文本
        self.kept = kept          # 保留下来的文件路径
        self.dropped = dropped    # [(path, reason)] 内容层面丢弃
        self.skipped = skipped    # [(path, reason)] 路径规则跳过

    def stats(self) -> Dict[str, int]:
        out = {"kept": len(self.kept), "dropped": len(self.dropped), "skipped": len(self.skipped)}
        for _, reason in self.skipped:
            out["skipped_" + reason] = out.get("skipped_" + reason, 0) + 1
        return out


def split_diff(diff: str) -> List[str]:
    """按 `diff --git` 切分，每个元素是一个文件的完整 diff。"""
    starts = [m.start() for m in re.finditer(r"^diff --git ", diff, re.M)]
    if not starts:
        return []
    starts.append(len(diff))
    return [diff[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]


def diff_path(segment: str) -> str:
    """从 diff 段头取 b 侧路径。"""
    match = re.match(r"diff --git a/(\S+) b/(\S+)", segment)
    return match.group(2) if match else ""


def is_binary_segment(segment: str) -> bool:
    """git 对二进制文件输出 Binary files ... differ 或 GIT binary patch。"""
    if "GIT binary patch" in segment:
        return True
    return bool(re.search(r"^Binary files .* differ$", segment, re.M))


def filter_diff(
    diff: str,
    exclude_paths: Sequence[str] = (),
    max_bytes: int = PATCH_MAX_BYTES,
    max_file_bytes: int = PATCH_MAX_FILE_BYTES,
) -> FilterResult:
    """纯函数：对 unified diff 文本做逐文件过滤与总量裁剪。

    `collect()` 已经按路径挑过一遍，这里再做一次是因为：
    - 路径过多时会退化成“全量 diff + 文本过滤”，需要规则兜底；
    - 二进制 / 非 UTF-8 / 超大文件只能看内容才知道。
    """
    kept: List[Tuple[str, int, str]] = []
    dropped: List[Tuple[str, str]] = []
    skipped: List[Tuple[str, str]] = []

    for segment in split_diff(diff):
        path = diff_path(segment)
        reason = classify_path(path, exclude_paths)
        if reason is not None:
            skipped.append((path, reason))
            continue
        if is_binary_segment(segment):
            dropped.append((path, "binary"))
            continue
        if "\ufffd" in segment:
            # 原文含非 UTF-8 字节：解码时已被替换，补丁无法可靠 apply，整段丢弃。
            dropped.append((path, "non-utf8"))
            continue
        size = len(segment.encode("utf-8", "replace"))
        if size > max_file_bytes:
            dropped.append((path, "file>%d" % max_file_bytes))
            continue
        kept.append((path, size, segment))

    total = sum(size for _, size, _ in kept)
    if total > max_bytes:
        # 从最大的文件开始丢，直到放进预算。
        order = sorted(range(len(kept)), key=lambda i: kept[i][1], reverse=True)
        keep_idx = set(range(len(kept)))
        for i in order:
            if total <= max_bytes:
                break
            keep_idx.discard(i)
            total -= kept[i][1]
            dropped.append((kept[i][0], "over-total"))
        kept = [kept[i] for i in sorted(keep_idx)]

    return FilterResult(
        text="".join(segment for _, _, segment in kept),
        kept=[path for path, _, _ in kept],
        dropped=dropped,
        skipped=skipped,
    )


# ---------------------------------------------------------------- git / IO --

def _git(workdir: str, *args: str, input: Optional[bytes] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=workdir,
        input=input,
        capture_output=True,
    )


def clean_artifacts(workdir: str) -> None:
    """删掉缓存目录与字节码，避免污染补丁。"""
    for root, dirs, files in os.walk(workdir):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in list(dirs):
            if name in CLEAN_DIRS:
                shutil.rmtree(os.path.join(root, name), ignore_errors=True)
                dirs.remove(name)
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                try:
                    os.remove(os.path.join(root, name))
                except OSError:
                    pass


def staged_changed_paths(workdir: str) -> List[str]:
    """git add -A 后返回全部变更路径（-z 避免非 ASCII 路径被转义）。"""
    _git(workdir, "add", "-A")
    out = _git(workdir, "diff", "--cached", "HEAD", "--name-only", "-z").stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def staged_blob_sizes(workdir: str, paths: Sequence[str]) -> Dict[str, int]:
    """读取索引里每个路径的 blob 大小；删除的文件查不到，不在返回结果中。"""
    wanted = [p for p in paths if "\n" not in p]
    sizes: Dict[str, int] = {}
    if not wanted:
        return sizes
    payload = "".join(":%s\n" % p for p in wanted).encode("utf-8", "replace")
    lines = _git(workdir, "cat-file", "--batch-check", input=payload).stdout.decode(
        "utf-8", "replace"
    ).splitlines()
    for path, line in zip(wanted, lines):
        fields = line.split()
        if len(fields) == 3 and fields[1] == "blob":
            try:
                sizes[path] = int(fields[2])
            except ValueError:
                pass
    return sizes


def git_diff(workdir: str, paths: Optional[Sequence[str]] = None) -> str:
    """相对 HEAD 的已暂存 diff；paths 为空表示全部。"""
    args = ["-c", "core.fileMode=false", "diff", "--cached", "HEAD"]
    if paths:
        args += ["--", *paths]
    return _git(workdir, *args).stdout.decode("utf-8", "replace")


def collect(
    workdir: str,
    exclude_paths: Sequence[str] = (),
    max_bytes: int = PATCH_MAX_BYTES,
    max_file_bytes: int = PATCH_MAX_FILE_BYTES,
) -> FilterResult:
    """完整流程：清缓存 → git add -A → 路径筛选 → 生成 diff → 内容过滤。"""
    exclude = [p.strip("/") for p in exclude_paths if p and p.strip("/")]
    clean_artifacts(workdir)
    names = staged_changed_paths(workdir)

    selected: List[str] = []
    skipped: List[Tuple[str, str]] = []
    for path in names:
        reason = classify_path(path, exclude)
        if reason is None:
            selected.append(path)
        else:
            skipped.append((path, reason))

    sizes = staged_blob_sizes(workdir, selected)
    dropped: List[Tuple[str, str]] = []
    within_limit = []
    for path in selected:
        if sizes.get(path, 0) > max_file_bytes:
            dropped.append((path, "file>%d" % max_file_bytes))
        else:
            within_limit.append(path)

    # 路径太多时 -- 参数会超长，退化成全量 diff，交给 filter_diff 兜底过滤。
    if within_limit and len(within_limit) <= 500 and sum(len(p) for p in within_limit) <= 60000:
        raw = git_diff(workdir, within_limit)
    else:
        raw = git_diff(workdir, None)

    result = filter_diff(raw, exclude, max_bytes, max_file_bytes)

    # 兜底用全量 diff 时，filter_diff 看不到索引里的 blob 大小，可能把按大小
    # 淘汰的文件又放回来；这里再裁掉一次。
    if dropped:
        oversized = {path for path, _ in dropped}
        if oversized & set(result.kept):
            result.text = "".join(
                segment for segment in split_diff(result.text)
                if diff_path(segment) not in oversized
            )
            result.kept = [path for path in result.kept if path not in oversized]

    # 全量 diff 兜底时 filter_diff 会再判一次路径，去重避免统计重复。
    result.skipped = _dedupe(skipped + result.skipped)
    result.dropped = _dedupe(dropped + result.dropped)
    return result


def _dedupe(items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def main(workdir: str, cfg_json: str) -> int:
    """容器内入口：stdout 输出补丁，stderr 输出一行统计。"""
    cfg = json.loads(cfg_json) if cfg_json else {}
    result = collect(
        workdir,
        exclude_paths=cfg.get("exclude_paths") or [],
        max_bytes=int(cfg.get("max_bytes") or PATCH_MAX_BYTES),
        max_file_bytes=int(cfg.get("max_file_bytes") or PATCH_MAX_FILE_BYTES),
    )
    sys.stdout.buffer.write(result.text.encode("utf-8", "replace"))
    sys.stdout.buffer.flush()
    stats = result.stats()
    sys.stderr.write(
        "patch-filter: kept=%d dropped=%d skipped=%d tests=%d excluded=%d\n"
        % (
            stats.get("kept", 0),
            stats.get("dropped", 0),
            stats.get("skipped", 0),
            stats.get("skipped_test", 0),
            stats.get("skipped_excluded", 0),
        )
    )
    return 0


def module_source() -> str:
    """本文件源码：宿主机读取后经 stdin 送进容器 exec。"""
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        return fh.read()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.stderr.write("usage: build_patch.py <workdir> <config-json>\n")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
