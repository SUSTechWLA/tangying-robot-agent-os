"""预先固定的因子效应、交互、族级不确定性和发布检查。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .factorial_cases import STAGES
from .factorial_runner import BASELINE, MODELS, mean, sha, write_json


def estimate(values, seed=260921):
    values = np.asarray(values, dtype=float)
    n = len(values)
    rng = np.random.default_rng(seed)
    boot = rng.choice(values, (10000, n), replace=True).mean(axis=1)
    if n <= 16:
        codes = np.arange(2**n)[:, None]
        signs = 2 * ((codes >> np.arange(n)) & 1) - 1
        p = float(np.mean(np.abs((signs * values).mean(axis=1)) >= abs(values.mean()) - 1e-12))
    else:
        signs = rng.choice([-1.0, 1.0], size=(50000, n))
        p = (
            1 + int(np.sum(np.abs((signs * values).mean(axis=1)) >= abs(values.mean()) - 1e-12))
        ) / 50001
    return {
        "estimate": float(values.mean()),
        "ci95": [float(x) for x in np.quantile(boot, [0.025, 0.975])],
        "p": p,
        "families": n,
        "family_deltas": values.tolist(),
    }


def holm(items):
    running = 0.0
    for i, item in enumerate(sorted(items, key=lambda x: x["p"])):
        running = max(running, min(1.0, (len(items) - i) * item["p"]))
        item["p_holm"] = running
    return items


def effect(rows, factors, metric="decision_correct", full=True):
    if full:
        rows = [r for r in rows if r["factors"]["complete"]]
    cases = {}
    for r in rows:
        cases.setdefault(r["case_id"], []).append(r)
    grouped = {}
    for part in cases.values():
        values = []
        for r in part:
            sign = 1
            for f in factors:
                v = r["factors"][f]
                positive = (
                    v == "cnl" if f == "syntax" else v == "decision" if f == "order" else bool(v)
                )
                sign *= 1 if positive else -1
            values.append(sign * float(r[metric]))
        grouped.setdefault(part[0]["family"], []).append(
            (2 ** len(factors)) * float(np.mean(values))
        )
    return estimate([np.mean(v) for _, v in sorted(grouped.items())])


def compare(rows, first, second, metric="decision_correct"):
    a = {r["case_id"]: r for r in rows if r["format"] == first}
    b = {r["case_id"]: r for r in rows if r["format"] == second}
    assert a.keys() == b.keys()
    fam = {}
    for k in a:
        fam.setdefault(a[k]["family"], []).append(float(a[k][metric]) - float(b[k][metric]))
    return estimate([np.mean(v) for _, v in sorted(fam.items())])


def summarize(rows):
    return {
        k: mean(rows, k)
        for k in [
            "decision_correct",
            "supported_correct",
            "schema_ok",
            "abstain",
            "unsafe",
            "unsupported_assertion",
            "total_tokens",
            "latency_s",
        ]
    }


def analyze(root):
    rows = list(map(json.loads, (root / "test-results.jsonl").read_text().splitlines()))
    selection = json.loads((root / "selection.json").read_text())
    primary = []
    interactions = []
    stage_effects = []
    models = {}
    for model in MODELS:
        part = [r for r in rows if r["model"] == model]
        assert len(part) == 320 * 16 and len({(r["case_id"], r["format"]) for r in part}) == len(
            part
        ), "incomplete factorial design"
        for factor in ["syntax", "order", "annotation", "complete"]:
            primary.append(
                {
                    "model": model,
                    "factor": factor,
                    **effect(part, [factor], full=factor != "complete"),
                }
            )
        for factors in [
            ("syntax", "order"),
            ("syntax", "annotation"),
            ("order", "annotation"),
            ("syntax", "order", "annotation"),
        ]:
            interactions.append({"model": model, "factors": list(factors), **effect(part, factors)})
        for stage in STAGES:
            for factor in ["syntax", "order", "annotation"]:
                stage_effects.append(
                    {
                        "model": model,
                        "stage": stage,
                        "factor": factor,
                        **effect([r for r in part if r["stage"] == stage], [factor]),
                    }
                )
        formats = sorted({r["format"] for r in part})
        format_stats = {f: summarize([r for r in part if r["format"] == f]) for f in formats}
        pair_scores = {}
        for fmt in formats:
            pairs = {}
            for r in part:
                if r["format"] == fmt:
                    pairs.setdefault(r["pair_id"], []).append(r)
            assert all(len(v) == 2 for v in pairs.values())
            pair_scores[fmt] = {
                "both_decisions_correct": float(
                    np.mean([all(r["decision_correct"] for r in p) for p in pairs.values()])
                ),
                "both_supported_correct": float(
                    np.mean([all(r["supported_correct"] for r in p) for p in pairs.values()])
                ),
                "pairs": len(pairs),
            }
        selected = [
            {**r, "format": "stage_policy"}
            for r in part
            if r["format"] == selection["models"][model]["stages"][r["stage"]]
        ]
        uniform = selection["models"][model]["uniform"]
        gates = {}
        for stage in STAGES:
            b = [r for r in part if r["stage"] == stage and r["format"] == BASELINE]
            c = [r for r in selected if r["stage"] == stage]
            checks = {
                "unsafe_not_worse": mean(c, "unsafe") <= mean(b, "unsafe"),
                "accuracy_not_worse": mean(c, "decision_correct") >= mean(b, "decision_correct"),
            }
            gates[stage] = {
                "passed": all(checks.values()),
                "checks": checks,
                "baseline": summarize(b),
                "candidate": summarize(c),
                "selected": selection["models"][model]["stages"][stage],
            }
        comparisons = holm(
            [
                {"baseline": b, **compare(part + selected, "stage_policy", b)}
                for b in [BASELINE, uniform]
            ]
        )
        models[model] = {
            "formats": format_stats,
            "counterfactual_pairs": pair_scores,
            "selected_policy": summarize(selected),
            "baseline": format_stats[BASELINE],
            "uniform": uniform,
            "gates": gates,
            "policy_comparisons": comparisons,
            "returned_models": sorted({r["response_model"] for r in part if r["response_model"]}),
            "full_by_stage": {
                s: {
                    f: summarize([r for r in part if r["stage"] == s and r["format"] == f])
                    for f in formats
                    if f.endswith("-full")
                }
                for s in STAGES
            },
        }
    result = {
        "version": "factorial-analysis.v1",
        "rows": len(rows),
        "cases": 320,
        "primary": holm(primary),
        "interactions_exploratory": holm(interactions),
        "stage_effects_exploratory": holm(stage_effects),
        "models": models,
        "test_results_sha256": sha(root / "test-results.jsonl"),
        "selection_sha256": sha(root / "selection.json"),
        "analysis_source_sha256": sha(Path(__file__)),
        "protocol_errors": sum(bool(r["protocol_error"]) for r in rows),
        "interpretation": "所有效应都是指定渲染干预的总效应；family bootstrap 假设任务族可视为抽样单位，模板相关性限制外推；未执行中介分析",
    }
    write_json(root / "summary.json", result)
    return result
