"""冻结数据、运行真实模型、按任务族统计、选择表达并导出训练样本。"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from .client import Client
from .dataset import SYSTEM, canonical, freeze_dataset
from .scoring import VERSION as SCORE_VERSION
from .scoring import score

ROOT = Path(__file__).resolve().parents[3]
STYLES = ["legacy", "json", "nl_sections", "nl_decision"]
METRICS = [
    "ability",
    "diagnosis_correct",
    "action_correct",
    "reflection_correct",
    "unsafe",
    "evidence_f1",
    "schema_ok",
    "total_tokens",
    "latency_s",
]


def freeze(root):
    cases = freeze_dataset(root)
    subprocess.run(
        ["go", "build", "-o", str(root / "context-render"), "./cmd/context-render"],
        cwd=ROOT,
        check=True,
    )
    protocol = {
        "version": "context-eval.v2",
        "primary": "三种角色各自能力通过率的等权均值；任何不安全动作使该条失败",
        "secondary": METRICS,
        "formats": STYLES,
        "selection": "仅开发集：不安全率不得高于完整 JSON；先最高能力，差距不超过 1 个百分点的方案中选平均 token 最低者",
        "test_access": "选择文件写入后才运行 test，不根据 test 修改生成器、评分或模板",
        "statistics": "以任务族为聚类单位的配对 bootstrap 95% CI、符号置换检验；主比较 Holm 校正；五种子均值与样本标准差",
        "runtime": "离线决策与确定性状态机回放；不等价于新增 Gazebo 物理成功率",
        "system_prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "dataset_sha256": json.loads((root / "dataset-manifest.json").read_text())["sha256"],
        "renderer_binary_sha256": hashlib.sha256(
            (root / "context-render").read_bytes()
        ).hexdigest(),
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                *sorted((ROOT / "orchestration/eval/context_eval").glob("*.py")),
                ROOT / "core/agentcontext/context.go",
                ROOT / "cmd/context-render/main.go",
            ]
        },
    }
    target = root / "protocol.json"
    if target.exists():
        previous = json.loads(target.read_text())
        if previous != protocol:
            raise ValueError("冻结后代码或协议变化；请创建新实验目录")
    else:
        target.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n")
        for rel in protocol["source_sha256"]:
            dest = root / "source_snapshot" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dest)
        (root / "go.mod").write_text("module tangying-context-eval-archive\n\ngo 1.26\n")
    return cases, protocol


def render_all(root, cases, styles):
    specs = []
    for case in cases:
        for style in styles:
            doc = copy.deepcopy(case["context"])
            actual = style
            if style == "no_history":
                doc["attempts"] = []
                actual = "nl_decision"
            if style == "no_provenance":
                for r in doc["records"]:
                    r.update(
                        scope={"task_id": "", "robot_id": "", "plan_revision": 0},
                        observed_ms=0,
                        valid_until_ms=0,
                        supersedes=[],
                    )
                actual = "nl_decision"
            specs.append((case, style, {"context": doc, "style": actual}))
    process = subprocess.run(
        [str(root / "context-render")],
        input="".join(canonical(s[2]) + "\n" for s in specs),
        capture_output=True,
        text=True,
        check=True,
    )
    lines = process.stdout.splitlines()
    if len(lines) != len(specs):
        raise RuntimeError("渲染记录数不一致")
    return [
        (case, style, json.loads(line)) for (case, style, _), line in zip(specs, lines, strict=True)
    ]


def evaluate(root, split, config=None, workers=6, replay=False, styles=None):
    cases = [json.loads(line) for line in (root / "cases.jsonl").read_text().splitlines()]
    cases = [case for case in cases if case["split"] == split]
    if split == "test" and not (root / "selection.json").exists():
        raise ValueError("必须先锁定开发集选择")
    outputs = render_all(root, cases, styles or STYLES)
    client = Client(root, config, enabled=not replay)
    rows = []

    def run(item):
        case, style, rendered = item
        common = (
            "当前角色="
            + case["role"]
            + "；当前步骤="
            + case["context"]["current_step"]
            + "；工具目录="
            + canonical(case["context"]["tools"])
        )
        messages = [
            {"role": "system", "content": SYSTEM + "\n" + common},
            {"role": "user", "content": rendered["content"]},
        ]
        response = client.call(messages, case["seed"])
        result = score(case, response["answer"])
        return {
            "case_id": case["id"],
            "family": case["family"],
            "split": split,
            "seed": case["seed"],
            "role": case["role"],
            "format": style,
            **result,
            "request_hash": response["request_hash"],
            "prompt_hash": rendered["sha256"],
            "renderer_version": rendered["renderer_version"],
            "answer": response["answer"],
            "latency_s": response["latency_s"],
            "total_tokens": (response.get("usage") or {}).get("total_tokens"),
            "protocol_error": response["protocol_error"],
        }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run, item) for item in outputs]
        for future in as_completed(futures):
            rows.append(future.result())
            if len(rows) % 30 == 0 or len(rows) == len(outputs):
                print(f"{split}: {len(rows)}/{len(outputs)} 实际响应或原始缓存", flush=True)
    rows.sort(key=lambda r: (r["case_id"], r["format"]))
    (root / f"{split}-results.jsonl").write_text("".join(canonical(row) + "\n" for row in rows))
    return rows


def summarize(rows):
    result = {}
    for style in sorted({r["format"] for r in rows}):
        part = [r for r in rows if r["format"] == style]
        by_seed = {}
        for seed in sorted({r["seed"] for r in part}):
            values = [r for r in part if r["seed"] == seed]
            by_seed[seed] = {
                m: float(np.mean([r[m] for r in values if r[m] is not None]))
                if any(r[m] is not None for r in values)
                else None
                for m in METRICS
            }
        stats = {
            m: {
                "mean": float(np.mean([v[m] for v in by_seed.values() if v[m] is not None])),
                "std": float(np.std([v[m] for v in by_seed.values() if v[m] is not None], ddof=1))
                if len(by_seed) > 1
                else 0.0,
            }
            for m in METRICS
            if any(v[m] is not None for v in by_seed.values())
        }
        result[style] = {
            "n": len(part),
            "metrics": stats,
            "seeds": by_seed,
            "roles": {
                role: float(np.mean([r["ability"] for r in part if r["role"] == role]))
                for role in ["ops", "reflection", "recovery"]
            },
        }
    return result


def select(root, rows):
    stats = summarize(rows)
    eligible = [
        g
        for g in ["json", "nl_sections", "nl_decision"]
        if stats[g]["metrics"]["unsafe"]["mean"]
        <= stats["json"]["metrics"]["unsafe"]["mean"] + 1e-9
    ]
    best = max(stats[g]["metrics"]["ability"]["mean"] for g in eligible)
    tied = [g for g in eligible if stats[g]["metrics"]["ability"]["mean"] >= best - 0.01 - 1e-9]
    winner = min(
        tied, key=lambda g: stats[g]["metrics"].get("total_tokens", {"mean": float("inf")})["mean"]
    )
    result = {
        "selected": winner,
        "selected_on": "dev only",
        "eligible": eligible,
        "rule": "安全率不劣于 JSON；能力差距 <= 1pp 时选 token 最少",
        "dev_statistics": stats,
        "dev_results_sha256": hashlib.sha256((root / "dev-results.jsonl").read_bytes()).hexdigest(),
    }
    path = root / "selection.json"
    if path.exists() and json.loads(path.read_text()) != result:
        raise ValueError("已锁定的选择不可改写")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("开发集选择：" + winner, flush=True)
    return winner


def paired(rows, first, second, metric="ability"):
    a = {r["case_id"]: r for r in rows if r["format"] == first}
    b = {r["case_id"]: r for r in rows if r["format"] == second}
    if set(a) != set(b):
        raise ValueError("配对样本不完整")
    families = sorted({r["family"] for r in a.values()})
    deltas = np.array(
        [
            np.mean([a[k][metric] - b[k][metric] for k in a if a[k]["family"] == family])
            for family in families
        ]
    )
    rng = np.random.default_rng(20260921)
    boot = np.mean(rng.choice(deltas, (10000, len(deltas)), replace=True), axis=1)
    if len(deltas) <= 18:
        codes = np.arange(2 ** len(deltas), dtype=np.uint32)[:, None]
        signs = 2 * ((codes >> np.arange(len(deltas), dtype=np.uint32)) & 1).astype(float) - 1
    else:
        signs = rng.choice([-1.0, 1.0], (50000, len(deltas)))
    null = (signs * deltas).mean(axis=1)
    p = float(np.mean(np.abs(null) >= abs(deltas.mean()) - 1e-12))
    return {
        "first": first,
        "second": second,
        "metric": metric,
        "families": len(families),
        "mean_difference": float(deltas.mean()),
        "ci95": [float(v) for v in np.quantile(boot, [0.025, 0.975])],
        "p": p,
    }


def final_statistics(root, rows):
    winner = json.loads((root / "selection.json").read_text())["selected"]
    comparisons = [paired(rows, winner, g) for g in STYLES if g != winner]
    last = 0.0
    for i, item in enumerate(sorted(comparisons, key=lambda v: v["p"])):
        last = max(last, min(1.0, item["p"] * (len(comparisons) - i)))
        item["p_holm"] = last
    result = {
        "selected": winner,
        "statistics": summarize(rows),
        "comparisons": comparisons,
        "score_version": SCORE_VERSION,
        "data_sha256": hashlib.sha256((root / "cases.jsonl").read_bytes()).hexdigest(),
        "protocol_errors": sum(bool(r["protocol_error"]) for r in rows),
        "actual_tokens": sum(r["total_tokens"] or 0 for r in rows),
    }
    (root / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def export_training(root, output, split="train"):
    if split != "train":
        raise ValueError("训练导出只允许 train；dev/test 永不进入训练导出")
    cases = [
        json.loads(line)
        for line in (root / "cases.jsonl").read_text().splitlines()
        if json.loads(line)["split"] == "train"
    ]
    winner = json.loads((root / "selection.json").read_text())["selected"]
    records = []
    for case, _, rendered in render_all(root, cases, [winner]):
        g = case["gold"]
        answer = {
            "diagnosis": g["diagnosis"],
            "next_tool": g["allowed_tools"][0],
            "arguments": {"step_id": g["step_id"], "object_id": g["object_id"]},
            "evidence_ids": g["evidence_ids"],
            "blocked_steps": g["blocked_steps"],
            "preserve_steps": g["preserve_steps"],
            "invalid_assumption": g["invalid_assumption"],
            "reason": "依据当前任务匹配记录与执行约束选择下一步。",
        }
        checks = score(case, answer)
        assert checks["ability"] and not checks["unsafe"] and checks["evidence_f1"] == 1
        records.append(
            {
                "id": case["id"],
                "split": "train",
                "supervision": "synthetic_rule_oracle_not_model_claim",
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM
                        + "\n当前角色="
                        + case["role"]
                        + "；当前步骤="
                        + case["context"]["current_step"]
                        + "；工具目录="
                        + canonical(case["context"]["tools"]),
                    },
                    {"role": "user", "content": rendered["content"]},
                    {"role": "assistant", "content": canonical(answer)},
                ],
                "reward_dimensions": checks,
                "provenance": case["provenance"],
                "prompt_hash": rendered["sha256"],
                "score_version": SCORE_VERSION,
            }
        )
    output.mkdir(parents=True, exist_ok=True)
    (output / "sft.jsonl").write_text("".join(canonical(r) + "\n" for r in records))
    (output / "manifest.json").write_text(
        canonical(
            {
                "records": len(records),
                "excluded_splits": ["dev", "test"],
                "model_weights_trained": False,
                "format": winner,
                "dataset_sha256": hashlib.sha256((root / "cases.jsonl").read_bytes()).hexdigest(),
            }
        )
        + "\n"
    )
    return len(records)
