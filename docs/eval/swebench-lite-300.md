# SWE-bench Lite 300 实例评测

日期：2026-11-08  
模型：`deepseek-v4-flash`  
配置：300 例、`--workers 3`、`--max-steps 50`、`network=none`、`path_guard=True`

## 结果

| 指标 | 数值 |
|---|---:|
| 生成完成 | 300/300 |
| 非空补丁 | 284/300 |
| 官方判分完成 | 279/300 |
| resolved | 210/300（70.0%） |
| unresolved | 69 |
| ambiguous | 6 |
| 判分 error | 5 |
| 空补丁 | 16 |

## 用量

| 指标 | 数值 |
|---|---:|
| 输入 tokens | 234,610,703 |
| 输出 tokens | 4,469,233 |
| 缓存命中 tokens | 224,640,896 |
| 主循环请求 | 8,626 |
| 实际 API 请求 | 8,868 |
| 费用 | 64.68 元 |
| 生成耗时 | 约 4h37m |
| 判分耗时 | 约 48min |

请求数按 `stream_run` 主循环统计，不含摘要、收尾与 SDK 重试，较实际低约 2.7%。

## 已知限制

- 5 个 `psf__requests-*` 实例判分超时（1800s）。
- 16 个实例为空补丁。

## 复现

```bash
python -m eval.run_predictions \
  --dataset "$SWE_DATA/swe-bench_lite-test.parquet" --split test \
  --out "$OUT" --run-id swebench-lite-300 \
  --workers 3 --max-steps 50

conda run --no-capture-output -n swebench \
  python -m swebench.harness.run_evaluation \
  -d "$SWE_DATA/swe-bench_lite-test.parquet" -s test \
  -p "$OUT/swebench-lite-300/predictions.jsonl" \
  -id sb-lite-300-grade --max_workers 8 -t 1800 \
  --report_dir "$OUT"
```

官方报告：[`eval/reports/swebench-lite-300/report.json`](../../eval/reports/swebench-lite-300/report.json)
