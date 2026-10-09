"""B1 预测生成 CLI：在 SWE-bench 实例容器内运行 CodeAgent，产出官方格式 predictions。

用法：
    export HF_ENDPOINT=https://hf-mirror.com
    python -m eval.run_predictions --dev --out /mnt/data/lyd/swe/preds --workers 2

产物（<out>/<run_id>/）：
    predictions.jsonl   # {instance_id, model_name_or_path, model_patch} → 交给官方 harness 判分
    diagnostics.jsonl   # 每实例步数/tokens/工具错误/是否跑测试等
"""
from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .docker_sandbox import DockerSandbox
from .container_runner import DEFAULT_MODEL_NAME, run_instance
from .swebench_io import (
    DEFAULT_DATASET,
    DEFAULT_SPLIT,
    DEV_INSTANCE_IDS,
    image_for,
    load_instances,
    select_instances,
)


def prepull_images(instances: list[dict], pull_timeout: int = 7200) -> None:
    """顺序预拉镜像（并发拉取会被镜像源限流），带重试。"""
    for i, inst in enumerate(instances, 1):
        image = image_for(inst)
        sandbox = DockerSandbox(image=image, instance_id=inst["instance_id"])
        res = sandbox.ensure_image(pull_timeout=pull_timeout)
        print(f"[prepull {i}/{len(instances)}] {inst['instance_id']}: {'OK' if res.ok else 'FAIL: ' + (res.err or res.out).strip()[:200]}", flush=True)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CodeAgent inside SWE-bench instance containers.")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--dev", action="store_true", help="使用内置 15 例 dev 子集")
    parser.add_argument("--instance-ids", nargs="+", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="/mnt/data/lyd/swe/preds")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--pull-timeout", type=int, default=7200)
    parser.add_argument("--keep-container", action="store_true")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--prepull", action="store_true", help="运行前顺序预拉全部镜像（避免并发限流）")
    args = parser.parse_args()

    ids = args.instance_ids or (list(DEV_INSTANCE_IDS) if args.dev else None)
    instances = load_instances(args.dataset, args.split, ids)
    instances = select_instances(instances, ids, args.limit)

    if args.prepull:
        prepull_images(instances, pull_timeout=args.pull_timeout)

    run_id = args.run_id or datetime.now().strftime("dev-%Y%m%d-%H%M%S")
    out_dir = Path(args.out) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"数据集: {args.dataset} ({args.split})")
    print(f"实例数: {len(instances)}  workers={args.workers}  max_steps={args.max_steps}")
    print(f"输出目录: {out_dir}\n")

    results: list[dict] = []
    lock = threading.Lock()
    done = 0

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {
                pool.submit(
                    run_instance, inst, args.max_steps, args.pull_timeout, args.keep_container
                ): inst["instance_id"]
                for inst in instances
            }
            for fut in as_completed(futs):
                rec = fut.result()
                results.append(rec)
                done += 1
                with lock:
                    flag = "OK" if rec.get("status") == "ok" and rec.get("patch_bytes") else "EMPTY/ERR"
                    print(f"[{done}/{len(instances)}] {rec['instance_id']} {flag} "
                          f"steps={rec.get('steps')} patch={rec.get('patch_bytes')}B "
                          f"time={rec.get('duration_seconds')}s {rec.get('error', '')[:80]}")
    else:
        for inst in instances:
            rec = run_instance(inst, args.max_steps, args.pull_timeout, args.keep_container)
            results.append(rec)
            done += 1
            flag = "OK" if rec.get("status") == "ok" and rec.get("patch_bytes") else "EMPTY/ERR"
            print(f"[{done}/{len(instances)}] {rec['instance_id']} {flag} "
                  f"steps={rec.get('steps')} patch={rec.get('patch_bytes')}B "
                  f"time={rec.get('duration_seconds')}s {rec.get('error', '')[:80]}")

    results.sort(key=lambda r: r["instance_id"])

    predictions = [
        {
            "instance_id": r["instance_id"],
            "model_name_or_path": args.model_name,
            "model_patch": r.get("model_patch", ""),
        }
        for r in results
    ]
    _write_jsonl(out_dir / "predictions.jsonl", predictions)
    _write_jsonl(out_dir / "diagnostics.jsonl", results)

    ok = sum(1 for r in results if r.get("status") == "ok")
    nonempty = sum(1 for r in results if r.get("patch_bytes"))
    avg_steps = sum(r.get("steps", 0) for r in results) / max(len(results), 1)
    avg_tokens = sum(r.get("tokens", 0) for r in results) / max(len(results), 1)
    total_time = sum(r.get("duration_seconds", 0) for r in results)
    test_runs = sum(r.get("diagnostics", {}).get("test_runs", 0) for r in results)

    print("\n===== 汇总 =====")
    print(f"predictions: {out_dir / 'predictions.jsonl'}")
    print(f"diagnostics: {out_dir / 'diagnostics.jsonl'}")
    print(f"成功: {ok}/{len(results)}  非空补丁: {nonempty}/{len(results)}")
    print(f"平均步数: {avg_steps:.1f}  平均 tokens: {avg_tokens:.0f}  累计耗时: {total_time:.0f}s")
    print(f"tests 执行次数合计: {test_runs}")


if __name__ == "__main__":
    main()
