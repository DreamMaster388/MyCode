"""测试结果解析与 SWE-bench 风格的 pass/fail 判定。

与 SWE-bench 一致：仅当所有 FAIL_TO_PASS 测试通过且所有 PASS_TO_PASS 测试未回归时，
该实例判定为 RESOLVED。
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

# pytest 逐条结果行形如:
#   tests/test_x.py::test_y PASSED
#   tests/test_x.py::test_y FAILED
#   tests/test_x.py::test_y ERROR
_TEST_LINE = re.compile(r"^\s*(\S+?::\S+?)\s+(PASSED|FAILED|ERROR)\b")


def parse_test_results(output: str) -> Dict[str, str]:
    """从 pytest 输出解析每个测试节点 id 及其状态（lowercased）。

    Returns:
        {node_id: "passed" | "failed" | "error", ...}
    """
    results: Dict[str, str] = {}
    for line in output.splitlines():
        match = _TEST_LINE.match(line.rstrip("\n\r"))
        if match:
            results[match.group(1)] = match.group(2).lower()
    return results


def judge(instance: Dict, results: Dict[str, str]) -> Tuple[bool, List[str], List[str]]:
    """按 F2P / P2P 规则判定实例是否解决。

    Args:
        instance: 实例配置（含 fail_to_pass / pass_to_pass）。
        results: parse_test_results 的输出。

    Returns:
        (resolved, unresolved_f2p, regressed_p2p)

    规则：
        - FAIL_TO_PASS：测试必须为 "passed"，缺失或失败都算未通过。
        - PASS_TO_PASS：不得为 "failed"/"error"（缺失/跳过宽恕，避免误判）。
    """
    f2p = instance.get("fail_to_pass") or []
    p2p = instance.get("pass_to_pass") or []

    unresolved_f2p = [t for t in f2p if results.get(t) != "passed"]
    regressed_p2p = [t for t in p2p if results.get(t) in ("failed", "error")]

    resolved = (not unresolved_f2p) and (not regressed_p2p)
    return resolved, unresolved_f2p, regressed_p2p
