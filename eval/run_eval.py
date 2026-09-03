"""CLI 入口：批量运行评测实例，写入 JSONL 并打印汇总。

用法：
    python eval/run_eval.py --instances eval/instances --out eval/results --limit 1
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .harness import run_instance, write_jsonl


def load_instances(instances_dir: str) -> list[dict]:
    """读取 instances_dir 下所有 *.json 作为实例配置，按 instance_id 排序。"""
    items = []
    for path in sorted(Path(instances_dir).glob("*.json")):
        with open(path, "r", encoding="utf-8") as fh:
            items.append(json.load(fh))
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="HelloAgents SWE-style eval harness")
    parser.add_argument("--instances", default="eval/instances", help="实例配置目录")
    parser.add_argument("--out", default="eval/results", help="结果输出目录")
    parser.add_argument("--workspace", default=None, help="临时工作目录（默认系统临时目录）")
    parser.add_argument("--limit", type=int, default=0, help="最多运行多少实例（0=全部）")
    parser.add_argument("--max-steps", type=int, default=25, help="agent 最大步数")
    args = parser.parse_args()

    instances = load_instances(args.instances)
    if args.limit > 0:
        instances = instances[: args.limit]

    if not instances:
        print("未发现实例。")
        return

    workspace = args.workspace or tempfile.mkdtemp(prefix="helloagents-eval-")
    print(f"工作目录: {workspace}")
    print(f"实例数: {len(instances)}\n")

    records = []
    for i, inst in enumerate(instances, 1):
        inst_id = inst["instance_id"]
        print(f"[{i}/{len(instances)}] {inst_id} ...", end=" ", flush=True)
        rec = run_instance(inst, workspace, args.out, max_steps=args.max_steps)
        records.append(rec)
        flag = "OK" if rec.get("resolved") else "FAIL"
        print(
            f"{flag} | 状态={rec.get('status')} | 步数={rec.get('steps', '-')} "
            f"| 耗时={rec.get('duration_seconds', '-')}s"
        )

    out_path = str(Path(args.out) / "results.jsonl")
    write_jsonl(out_path, records)

    resolved = sum(1 for r in records if r.get("resolved"))
    avg_steps = sum(r.get("steps", 0) for r in records) / len(records)
    avg_time = sum(r.get("duration_seconds", 0) for r in records) / len(records)
    print("\n===== 汇总 =====")
    print(f"结果文件: {out_path}")
    print(f"Resolved: {resolved}/{len(records)}")
    print(f"平均步数: {avg_steps:.1f}")
    print(f"平均耗时: {avg_time:.2f}s")


if __name__ == "__main__":
    main()
