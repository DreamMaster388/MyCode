"""build_patch 单测：路径规则、diff 过滤、预算裁剪、临时仓库集成、容器引导协议。

这些用例全部在宿主机跑，不需要 Docker；容器内的 `_PATCH_SCRIPT` 只做 IO，
其正确性由 `test_module_source_exec_contract` 用同样的 exec 方式覆盖。
"""
import json
import shutil
import subprocess
import types

import pytest

from eval import build_patch


# ---------------------------------------------------------------- 辅助构造 --

def _segment(path, body="+x = 1\n", header_extra=""):
    """构造一个最小的 unified diff 段。"""
    return (
        "diff --git a/{p} b/{p}\n"
        "index 1111111..2222222 100644\n"
        "--- a/{p}\n"
        "+++ b/{p}\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "{body}"
        "{extra}"
    ).format(p=path, body=body, extra=header_extra)


def _binary_segment(path):
    return (
        "diff --git a/{p} b/{p}\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/{p} and b/{p} differ\n"
    ).format(p=path)


# ---------------------------------------------------------------- 路径规则 --

@pytest.mark.parametrize("path", [
    "downloads/extracted/pkg/mod.py",
    "build/lib/requests/api.py",
    "dist/pkg-1.0.tar.gz",
    "pkg/__pycache__/mod.cpython-39.pyc",
    "pkg/mod.pyc",
    "vendor/node_modules/left-pad/index.js",
    "assets/logo.png",
    "obj/parser.o",
    "tmp_repro/pkg/core.py",
    "_scratch/settings.py",
    "tmpdl/Django-3.2-py3-none-any.whl",
    "repro.py",
    "repro_2.py",
    "repro_ordering.py",
    "tmp_repro.py",
    "scratch_issue.py",
    "fetch_tmp.py",
    "gettests.py",
    "extract_tests.py",
    "geturl.py",
    "ping.txt",
    ".DS_Store",
])
def test_junk_paths(path):
    assert build_patch.is_junk_path(path) is True


@pytest.mark.parametrize("path", [
    "tmpdir.py",
    "templates.py",
    "debug.py",
    "src/app.py",
    "sympy/core/numbers.py",
    "temperature.py",
    "django/views/debug.py",
])
def test_normal_source_is_not_junk(path):
    """不能把 tmpdir.py / templates.py / debug.py 这类正常源码误判成临时文件。"""
    assert build_patch.is_junk_path(path) is False


@pytest.mark.parametrize("path", [
    "tests/test_app.py",
    "sympy/printing/tests/test_latex.py",
    "testing/test_config.py",
    "testing/logging/test_formatter.py",
    "conftest.py",
    "test_foo.py",
    "foo_test.py",
    "src/bar/tests/__init__.py",
])
def test_test_paths(path):
    assert build_patch.is_test_path(path) is True


@pytest.mark.parametrize("path", [
    "django/test/client.py",   # django 的 test 工具是库代码，不是单测
    "src/app.py",
    "testing_utils.py",
])
def test_non_test_paths(path):
    assert build_patch.is_test_path(path) is False


@pytest.mark.parametrize("path", [
    "docs/ref/checks.txt",
    "doc/whats-new.rst",
    "CHANGES",
    "README.md",
    "changelog/7698.bugfix.rst",
    "docs/index.html",
    "NEWS.rst",
])
def test_doc_paths(path):
    assert build_patch.is_doc_path(path) is True


@pytest.mark.parametrize("path", [
    "src/notes_parser.py",
    "pkg/README.py",
    "setup.cfg",
])
def test_non_doc_paths(path):
    assert build_patch.is_doc_path(path) is False


def test_classify_precedence_and_explicit_exclude():
    assert build_patch.classify_path("src/app.py") is None
    assert build_patch.classify_path("tests/test_app.py") == "test"
    assert build_patch.classify_path("downloads/x/y.py") == "junk"
    assert build_patch.classify_path("docs/x.rst") == "doc"
    assert build_patch.classify_path("/dev/null") == "empty"
    assert build_patch.classify_path("src/app.py", ["src/app.py"]) == "excluded"
    assert build_patch.classify_path("tests/test_app.py", ["tests"]) == "excluded"


# ---------------------------------------------------------------- diff 过滤 --

def test_filter_diff_keeps_only_code():
    diff = (
        _segment("pkg/mod.py")
        + _segment("tests/test_mod.py")
        + _segment("docs/guide.rst")
        + _segment("downloads/pkg/other.py")
        + _binary_segment("assets/logo.png")
        + _segment("CHANGES")
        + _segment("repro.py")
    )
    result = build_patch.filter_diff(diff)

    assert result.kept == ["pkg/mod.py"]
    assert "tests/test_mod.py" not in result.text
    assert "docs/guide.rst" not in result.text
    assert "downloads/pkg/other.py" not in result.text
    assert "assets/logo.png" not in result.text
    assert "CHANGES" not in result.text
    assert "repro.py" not in result.text
    assert result.text.startswith("diff --git a/pkg/mod.py")


def test_filter_diff_drops_non_utf8_segment():
    diff = _segment("pkg/mod.py") + _segment("pkg/latin1.py", body="+caf\ufffd\n")
    result = build_patch.filter_diff(diff)

    assert result.kept == ["pkg/mod.py"]
    assert ("pkg/latin1.py", "non-utf8") in result.dropped


def test_filter_diff_drops_oversized_file():
    big = "+" + "x = 1\n" * 200
    diff = _segment("pkg/mod.py") + _segment("pkg/big.py", body=big)
    result = build_patch.filter_diff(diff, max_file_bytes=1000)

    assert result.kept == ["pkg/mod.py"]
    assert any(path == "pkg/big.py" and reason.startswith("file>") for path, reason in result.dropped)


def test_filter_diff_respects_total_budget():
    diff = "".join(_segment("pkg/mod%d.py" % i, body="+" + "y = 1\n" * 40) for i in range(3))
    full = build_patch.filter_diff(diff)
    assert len(full.kept) == 3

    trimmed = build_patch.filter_diff(diff, max_bytes=len(full.text) // 2)
    assert 0 < len(trimmed.kept) < 3
    assert len(trimmed.text.encode("utf-8")) <= len(full.text) // 2
    assert any(reason == "over-total" for _, reason in trimmed.dropped)


def test_exclude_prefix_covers_directory():
    diff = _segment("tests/unit/test_a.py") + _segment("pkg/mod.py")
    result = build_patch.filter_diff(diff, exclude_paths=["tests"])
    assert result.kept == ["pkg/mod.py"]


def test_split_diff_and_path():
    diff = _segment("a.py") + _segment("b.py")
    segments = build_patch.split_diff(diff)
    assert [build_patch.diff_path(s) for s in segments] == ["a.py", "b.py"]
    assert build_patch.split_diff("") == []


# ---------------------------------------------------------------- git 集成 --

def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "tests" / "test_mod.py").write_text("def test_f():\n    assert True\n", encoding="utf-8")
    (root / "docs" / "guide.rst").write_text("old\n", encoding="utf-8")
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def test_collect_temp_repo_only_keeps_code(repo):
    (repo / "pkg" / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    (repo / "tests" / "test_mod.py").write_text("def test_f():\n    assert 2\n", encoding="utf-8")
    (repo / "docs" / "guide.rst").write_text("new\n", encoding="utf-8")
    (repo / "downloads").mkdir()
    (repo / "downloads" / "pkg.py").write_text("junk = 1\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "out.o").write_bytes(b"\x00\x01")
    (repo / "repro.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "notes.txt").write_text("notes\n", encoding="utf-8")

    result = build_patch.collect(str(repo), exclude_paths=["tests/test_mod.py"])

    assert result.kept == ["pkg/mod.py"]
    assert "return 2" in result.text
    assert "tests/test_mod.py" not in result.text

    # 生成的补丁必须能应用到干净的 base commit 上。
    clone = repo.parent / "clean"
    _git(repo.parent, "clone", "-q", str(repo), str(clone))
    apply = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=clone,
        input=result.text.encode("utf-8"),
        capture_output=True,
    )
    assert apply.returncode == 0, apply.stderr


def test_collect_removes_cache_artifacts(repo):
    cache = repo / "pkg" / "__pycache__"
    cache.mkdir()
    (cache / "mod.cpython-39.pyc").write_bytes(b"\x00")
    (repo / "pkg" / "mod.py").write_text("def f():\n    return 3\n", encoding="utf-8")

    result = build_patch.collect(str(repo))

    assert result.kept == ["pkg/mod.py"]
    assert not cache.exists()
    assert "__pycache__" not in result.text


# ------------------------------------------------------- 容器引导协议 / main --

def _exec_module(source):
    """模拟 docker_sandbox._PATCH_SCRIPT 的 exec 方式。"""
    module = types.ModuleType("build_patch")
    module.__file__ = "build_patch.py"
    exec(compile(source, "build_patch.py", "exec"), module.__dict__)
    return module


def test_module_source_exec_contract():
    source = build_patch.module_source()
    assert source.strip()

    module = _exec_module(source)
    assert module.PATCH_MAX_BYTES == build_patch.PATCH_MAX_BYTES
    result = module.filter_diff(_segment("pkg/mod.py") + _segment("tests/test_x.py"))
    assert result.kept == ["pkg/mod.py"]


def test_main_writes_patch_to_stdout(repo, capsysbinary):
    (repo / "pkg" / "mod.py").write_text("def f():\n    return 4\n", encoding="utf-8")
    (repo / "tests" / "test_mod.py").write_text("def test_f():\n    assert 4\n", encoding="utf-8")

    module = _exec_module(build_patch.module_source())
    rc = module.main(str(repo), json.dumps({"exclude_paths": ["tests/test_mod.py"]}))

    captured = capsysbinary.readouterr()
    assert rc == 0
    assert b"diff --git a/pkg/mod.py" in captured.out
    assert b"tests/test_mod.py" not in captured.out
    assert b"patch-filter: kept=1" in captured.err


# ---------------------------------------------------------- docker 引导接线 --

def test_patch_script_only_does_io():
    """容器内脚本只保留 IO：真正规则必须在 build_patch 模块里。"""
    from eval import docker_sandbox

    script = docker_sandbox._PATCH_SCRIPT
    compile(script, "bootstrap", "exec")
    assert "module.main" in script
    for leaked in ("JUNK_DIRS", "is_test_path", "is_junk_path", "split_diff"):
        assert leaked not in script


def test_model_patch_sends_module_source(monkeypatch):
    from eval import docker_sandbox

    sandbox = docker_sandbox.DockerSandbox(image="dummy", instance_id="dummy__1")
    captured = {}

    def fake_exec(argv, input=None, timeout=120, cwd=None):
        captured["argv"] = argv
        captured["input"] = input
        return docker_sandbox.ExecResult(0, "", "")

    monkeypatch.setattr(sandbox, "exec", fake_exec)
    result = sandbox.model_patch(exclude_paths=["tests/test_x.py"], max_bytes=1234)

    assert result.ok
    assert captured["input"] == build_patch.module_source()
    argv = captured["argv"]
    assert argv[1] == "-c"
    assert argv[2] == docker_sandbox._PATCH_SCRIPT
    assert json.loads(argv[4]) == {
        "exclude_paths": ["tests/test_x.py"],
        "max_bytes": 1234,
        "max_file_bytes": build_patch.PATCH_MAX_FILE_BYTES,
    }
