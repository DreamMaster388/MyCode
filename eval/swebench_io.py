"""SWE-bench-Lite 数据集加载与字段适配。

只做"读 + 规范化"，不碰 Docker/评测执行。所有实例字段统一为 snake_case，
便于上层 container_runner / run_predictions 消费。

数据来源：HuggingFace `SWE-bench/SWE-bench_Lite`（v5 已含 image/eval_script/
log_parser/eval_type 等评测字段）。网络受限时用 HF 镜像：
    export HF_ENDPOINT=https://hf-mirror.com
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional, Sequence

DEFAULT_DATASET = "SWE-bench/SWE-bench_Lite"
DEFAULT_SPLIT = "test"

# 12 个 Lite 仓库的均衡 dev 子集（15 例），用于快速迭代。
# 优先纳入官方标注 "<15 min fix" 的实例，降低冷启动失败率。
DEV_INSTANCE_IDS: Sequence[str] = (
    "astropy__astropy-14995",
    "django__django-10914",
    "django__django-11001",
    "matplotlib__matplotlib-22711",
    "matplotlib__matplotlib-23299",
    "mwaskom__seaborn-3010",
    "pallets__flask-4992",
    "psf__requests-2317",
    "pydata__xarray-4094",
    "pylint-dev__pylint-5859",
    "pytest-dev__pytest-5103",
    "scikit-learn__scikit-learn-10297",
    "sphinx-doc__sphinx-7686",
    "sympy__sympy-11400",
    "sympy__sympy-20590",
)


def _loads_list(value) -> List[str]:
    """FAIL_TO_PASS / PASS_TO_PASS 是 JSON 字符串，统一解析为 list。"""
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        return list(parsed) if isinstance(parsed, list) else [parsed]
    return [value]


def normalize(instance: Dict) -> Dict:
    """补齐 snake_case 别名，保留原始字段。"""
    inst = dict(instance)
    inst["fail_to_pass"] = _loads_list(inst.get("FAIL_TO_PASS"))
    inst["pass_to_pass"] = _loads_list(inst.get("PASS_TO_PASS"))
    inst["repo_url"] = f"https://github.com/{inst['repo']}.git" if inst.get("repo") else None
    return inst


def load_instances(
    dataset_name: str = DEFAULT_DATASET,
    split: str = DEFAULT_SPLIT,
    instance_ids: Optional[Iterable[str]] = None,
) -> List[Dict]:
    """从 HF（或本地 parquet/json）加载并规范化实例。

    支持本地 parquet/jsonl（便于离线/缓存）：
    - 以 .parquet / .json / .jsonl 结尾 -> 本地文件
    - 其余 -> HF datasets（受 HF_ENDPOINT 影响）
    """
    from pathlib import Path

    wanted = set(instance_ids) if instance_ids else None

    if dataset_name.endswith(".parquet"):
        from datasets import load_dataset

        raw = [dict(x) for x in load_dataset("parquet", data_files=dataset_name, split="train")]
    elif dataset_name.endswith(".json"):
        raw = json.loads(Path(dataset_name).read_text(encoding="utf-8"))
    elif dataset_name.endswith(".jsonl"):
        raw = [json.loads(line) for line in Path(dataset_name).read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        from datasets import load_dataset

        raw = [dict(x) for x in load_dataset(dataset_name, split=split)]

    instances = [normalize(x) for x in raw]
    if wanted is not None:
        instances = [x for x in instances if x["instance_id"] in wanted]
    instances.sort(key=lambda x: x["instance_id"])
    return instances


def select_instances(
    instances: List[Dict],
    instance_ids: Optional[Iterable[str]] = None,
    limit: int = 0,
) -> List[Dict]:
    """按实例 id 过滤，可选截断。顺序保持输入顺序。"""
    if instance_ids:
        wanted = set(instance_ids)
        instances = [x for x in instances if x["instance_id"] in wanted]
    if limit and limit > 0:
        instances = instances[:limit]
    return instances


def image_for(instance: Dict) -> str:
    """官方评测镜像名（v5 数据集自带 image 字段）。"""
    image = instance.get("image")
    if not image:
        raise KeyError(f"instance {instance.get('instance_id')} 缺少 image 字段")
    return image


def eval_activation_prefix(instance: Dict) -> str:
    """从 eval_script 提取激活仓库 conda 环境的前缀命令。

    典型 eval_script 开头：
        #!/bin/bash
        set -uxo pipefail
        source /opt/miniconda3/bin/activate
        conda activate testbed
        cd /testbed
    返回可直接拼在命令前的 "source ... && conda activate ... && cd /testbed"。
    """
    import re

    script = instance.get("eval_script") or ""
    source_line = None
    conda_line = None
    for line in script.splitlines():
        stripped = line.strip()
        if source_line is None and re.match(r"(source|\.)\s+\S*activate", stripped):
            source_line = stripped
        if conda_line is None and re.match(r"conda\s+activate\s+\S+", stripped):
            conda_line = stripped
        if source_line and conda_line:
            break

    parts = []
    if source_line:
        parts.append(source_line)
    if conda_line:
        parts.append(conda_line)
    parts.append("cd /testbed")
    return " && ".join(parts)


def conda_env_name(instance: Dict) -> str:
    """从 eval_script 解析 conda 环境名，默认 testbed。"""
    import re

    script = instance.get("eval_script") or ""
    match = re.search(r"conda\s+activate\s+(\S+)", script)
    return match.group(1) if match else "testbed"


if __name__ == "__main__":
    # 测试加载与规范化
    instances = load_instances(instance_ids=DEV_INSTANCE_IDS)
    print(instances[0]['eval_script'])