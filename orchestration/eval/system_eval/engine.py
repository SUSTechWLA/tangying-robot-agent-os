"""Offline capability scorecards. A report never grants robot execution permission."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import jsonschema
import numpy as np
from scipy.stats import beta

PACKAGE = Path(__file__).parent


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analysis_identity():
    return {
        p.name: file_hash(p)
        for p in [Path(__file__), PACKAGE / "catalog.json", PACKAGE / "run.schema.json"]
    }


@lru_cache(maxsize=1)
def catalog():
    return json.loads((PACKAGE / "catalog.json").read_text())


def write(path, value, immutable=False):
    path = Path(path)
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable:
        try:
            with path.open("x") as stream:
                stream.write(content)
        except FileExistsError:
            if path.read_text() != content:
                raise ValueError(f"immutable artifact differs: {path}") from None
    else:
        path.write_text(content)


def validate(run):
    schema = json.loads((PACKAGE / "run.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(run)
    metrics = catalog()["metrics"]
    for key in ["primary_metric", "risk_metric"]:
        metric = run["benchmark"][key]
        if metric not in metrics or metrics[metric]["kind"] != "binary":
            raise ValueError(f"benchmark {key} must be a registered binary metric")
    if metrics[run["benchmark"]["primary_metric"]]["direction"] != "max":
        raise ValueError("primary metric must maximize quality")
    if metrics[run["benchmark"]["risk_metric"]]["direction"] != "min":
        raise ValueError("risk metric must minimize failures")
    ids, draws, repeats = set(), set(), set()
    for row in run["units"]:
        if row["unit_id"] in ids:
            raise ValueError("duplicate evaluation unit")
        ids.add(row["unit_id"])
        repeat = (row["case_id"], row["repeat_id"])
        if repeat in repeats:
            raise ValueError("duplicate case repeat")
        repeats.add(repeat)
        for metric in row["not_applicable"]:
            if metric not in metrics or row["measurements"].get(metric) is not None:
                raise ValueError("inapplicable metrics must be registered and unmeasured")
        if run["benchmark"]["primary_metric"] in row["not_applicable"]:
            raise ValueError("every planned unit must be eligible for the primary metric")
        if row["fresh_draw"]:
            if not row["draw_id"] or row["draw_id"] in draws:
                raise ValueError("fresh draws require unique execution identities")
            draws.add(row["draw_id"])
        for metric, value in row["measurements"].items():
            if metric not in metrics:
                raise ValueError(f"unregistered metric: {metric}")
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("measurements must be finite numbers, not booleans or strings")
            if value < metrics[metric].get("minimum", -math.inf) or value > metrics[metric].get(
                "maximum", math.inf
            ):
                raise ValueError(f"measurement outside declared range: {metric}")
            if metrics[metric]["kind"] == "binary" and value not in (0, 1):
                raise ValueError("binary observations must be 0/1")
    if (
        len(run["planned_units"]) != len(set(run["planned_units"]))
        or set(run["planned_units"]) != ids
    ):
        raise ValueError(
            "planned units must be represented exactly once, including skipped/timeouts"
        )
    if not run["units"]:
        raise ValueError("empty run cannot establish capability")
    return run


def load(path):
    return validate(json.loads(Path(path).read_text()))


def value(row, metric):
    definition = catalog()["metrics"][metric]
    # Failure policy is part of the metric definition. A timeout is not a fast
    # latency observation, and absence of a safety assessment is not zero risk.
    if metric in row["not_applicable"]:
        return None
    if row["execution_status"] != "completed":
        if (
            definition["kind"] == "binary"
            and definition["direction"] == "min"
            and row["measurements"].get(metric) == 1
        ):
            return 1  # An observed harmful event remains evidence even if the run later times out.
        return definition.get("failure_value")
    return row["measurements"].get(metric)


def metric_summary(rows, metric):
    eligible = [r for r in rows if metric not in r["not_applicable"]]
    values = [(r, value(r, metric)) for r in eligible]
    observed = [(r, v) for r, v in values if v is not None]
    groups = defaultdict(list)
    for r, v in observed:
        groups[r["cluster_id"]].append(v)
    vals = [v for _, v in observed]
    return {
        "planned": len(rows),
        "eligible": len(eligible),
        "not_applicable": len(rows) - len(eligible),
        "measured": len(vals),
        "missing": len(eligible) - len(vals),
        "coverage": len(vals) / len(eligible) if eligible else 0,
        "micro_mean": float(np.mean(vals)) if vals else None,
        "cluster_macro_mean": float(np.mean([np.mean(v) for v in groups.values()]))
        if groups
        else None,
        "clusters": len(groups),
        "p50": float(np.quantile(vals, 0.5)) if vals else None,
        "p95": float(np.quantile(vals, 0.95)) if len(vals) >= 20 else None,
    }


def scorecard(run, axis="stage"):
    validate(run)
    if axis not in catalog()["slice_axes"]:
        raise ValueError("unknown slice axis")
    metrics = sorted(
        {m for r in run["units"] for m in r["measurements"]}
        | {run["benchmark"]["primary_metric"], run["benchmark"]["risk_metric"]}
    )
    groups = defaultdict(list)
    for r in run["units"]:
        groups[r["dimensions"].get(axis, "unknown")].append(r)
    return {
        "run_id": run["run_id"],
        "run_sha256": digest(run),
        "analysis_sources": analysis_identity(),
        "benchmark": run["benchmark"],
        "axis": axis,
        "overall": {m: metric_summary(run["units"], m) for m in metrics},
        "slices": {
            name: {m: metric_summary(rows, m) for m in metrics}
            for name, rows in sorted(groups.items())
        },
        "execution_status": {
            s: sum(r["execution_status"] == s for r in run["units"])
            for s in ["completed", "error", "timeout", "skipped"]
        },
        "interpretation": "Descriptive metrics only; missing metrics are not successes; different benchmarks are not pooled.",
    }


def matched(baseline, candidate, changed_components):
    validate(baseline)
    validate(candidate)
    for key in ["benchmark", "controls"]:
        if baseline[key] != candidate[key]:
            raise ValueError(
                f"incomparable {key}: data, grader, semantics, environment and budget must match"
            )
    if not changed_components:
        raise ValueError("declare the intervention component before comparison")
    before, after = baseline["components"], candidate["components"]
    if before.keys() != after.keys():
        raise ValueError("component inventories differ")
    changed = {k for k in before if before[k] != after[k]}
    if changed != set(changed_components):
        raise ValueError(
            f"actual changed components {sorted(changed)} differ from declared intervention"
        )
    a = {r["unit_id"]: r for r in baseline["units"]}
    b = {r["unit_id"]: r for r in candidate["units"]}
    if a.keys() != b.keys():
        raise ValueError("unpaired units; refusing an intersection-only comparison")
    for k in a:
        for field in [
            "case_id",
            "repeat_id",
            "family_id",
            "cluster_id",
            "dimensions",
            "not_applicable",
        ]:
            if a[k][field] != b[k][field]:
                raise ValueError(f"paired unit identity changed: {k}/{field}")
    return a, b


def paired_effect(a, b, metric, alpha=0.05):
    groups = defaultdict(list)
    for key in a:
        av, bv = value(a[key], metric), value(b[key], metric)
        if av is not None and bv is not None:
            groups[a[key]["cluster_id"]].append(bv - av)
    deltas = np.array([np.mean(v) for _, v in sorted(groups.items())])
    if not len(deltas):
        return {"estimate": None, "ci": None, "clusters": 0, "paired": 0}
    # Bootstrap is descriptive; the gate below uses a conservative bounded-loss
    # bound, avoiding zero-width empirical intervals as proof of perfection.
    rng = np.random.default_rng(20260921)
    # Bound the temporary allocation rather than materializing 5000 x G.
    batch_size = max(1, min(5000, 1_000_000 // len(deltas)))
    samples = np.concatenate(
        [
            rng.choice(deltas, (min(batch_size, 5000 - start), len(deltas)), replace=True).mean(
                axis=1
            )
            for start in range(0, 5000, batch_size)
        ]
    )
    return {
        "estimate": float(deltas.mean()),
        "ci": [float(x) for x in np.quantile(samples, [alpha / 2, 1 - alpha / 2])],
        "clusters": len(deltas),
        "paired": sum(map(len, groups.values())),
        "alpha": alpha,
        "assumption": "declared clusters are independent sampling units; bootstrap interval is approximate",
    }


def validate_profile(profile):
    required = {
        "version",
        "purpose",
        "alpha",
        "quality_floor",
        "noninferiority_margin",
        "risk_cluster_ceiling",
        "min_clusters",
        "critical_slices",
    }
    if set(profile) != required or profile["version"] != "system-gate.v1":
        raise ValueError("invalid gate profile contract")
    for key in ["alpha", "quality_floor", "noninferiority_margin", "risk_cluster_ceiling"]:
        if (
            type(profile[key]) not in (int, float)
            or not math.isfinite(profile[key])
            or not 0 <= profile[key] <= 1
        ):
            raise ValueError("invalid gate threshold")
    if (
        not 0 < profile["alpha"] < 1
        or type(profile["min_clusters"]) is not int
        or profile["min_clusters"] < 2
    ):
        raise ValueError("invalid confidence level or sample minimum")
    if not isinstance(profile["critical_slices"], list):
        raise TypeError("critical_slices must be a list")
    for item in profile["critical_slices"]:
        if not item or any(
            k not in catalog()["slice_axes"] or not isinstance(v, str) for k, v in item.items()
        ):
            raise ValueError("invalid critical slice")


def compare(baseline, candidate, changed_components, profile):
    validate_profile(profile)
    a, b = matched(baseline, candidate, changed_components)
    primary, risk = [baseline["benchmark"][k] for k in ["primary_metric", "risk_metric"]]
    selectors = [{}] + profile["critical_slices"]
    tail = profile["alpha"] / (3 * len(selectors))
    outcomes = []
    for selector in selectors:
        keys = [k for k in a if all(a[k]["dimensions"].get(d) == v for d, v in selector.items())]
        left, right = {k: a[k] for k in keys}, {k: b[k] for k in keys}
        effect = paired_effect(left, right, primary)
        q = metric_summary(list(right.values()), primary)
        danger = metric_summary(list(right.values()), risk)
        fail, unknown = [], []
        if baseline["benchmark"]["split"] not in {"test", "confirmation"}:
            unknown.append("not_a_locked_test_or_confirmation_split")
        if not keys or q["coverage"] < 1 or danger["coverage"] < 1 or effect["paired"] != len(keys):
            unknown.append("missing_units_or_measurements")
        if effect["clusters"] < profile["min_clusters"]:
            unknown.append("insufficient_independent_clusters")
        if (
            q["cluster_macro_mean"] is not None
            and q["cluster_macro_mean"] < profile["quality_floor"]
        ):
            fail.append("absolute_quality_floor")
        if danger["micro_mean"] is not None and danger["micro_mean"] > 0:
            fail.append("observed_risk_event")
        n = effect["clusters"]
        quality_lower, delta_lower, risk_upper = None, None, None
        if n and q["coverage"] == 1 and effect["paired"] == len(keys):
            quality_lower = max(
                0.0, q["cluster_macro_mean"] - math.sqrt(math.log(1 / tail) / (2 * n))
            )
            delta_lower = max(-1.0, effect["estimate"] - math.sqrt(2 * math.log(1 / tail) / n))
            if (
                quality_lower < profile["quality_floor"]
                or delta_lower < -profile["noninferiority_margin"]
            ):
                unknown.append("quality_or_noninferiority_not_established")
        clusters = defaultdict(list)
        for row in right.values():
            if risk not in row["not_applicable"]:
                clusters[row["cluster_id"]].append(value(row, risk))
        if clusters and danger["coverage"] == 1:
            n_risk = len(clusters)
            k_risk = sum(any(v > 0 for v in vals) for vals in clusters.values())
            if n_risk < profile["min_clusters"]:
                unknown.append("insufficient_risk_clusters")
            risk_upper = (
                1.0 if k_risk == n_risk else float(beta.ppf(1 - tail, k_risk + 1, n_risk - k_risk))
            )
            if risk_upper > profile["risk_cluster_ceiling"]:
                unknown.append("cluster_risk_ceiling_not_established")
        outcomes.append(
            {
                "selector": selector,
                "status": "FAIL" if fail else "INCONCLUSIVE" if unknown else "PASS",
                "failures": fail,
                "uncertainties": unknown,
                "primary": q,
                "risk": danger,
                "paired_effect": effect,
                "gate_bounds": {
                    "quality_lower": quality_lower,
                    "delta_lower": delta_lower,
                    "cluster_any_risk_upper": risk_upper,
                    "per_bound_alpha": tail,
                },
            }
        )
    status = (
        "FAIL"
        if any(x["status"] == "FAIL" for x in outcomes)
        else "INCONCLUSIVE"
        if any(x["status"] == "INCONCLUSIVE" for x in outcomes)
        else "PASS"
    )
    return {
        "version": "system-comparison.v1",
        "analysis_sources": analysis_identity(),
        "status": status,
        "evaluation_split": baseline["benchmark"]["split"],
        "primary_metric": primary,
        "risk_metric": risk,
        "baseline_sha256": digest(baseline),
        "candidate_sha256": digest(candidate),
        "profile": profile,
        "profile_sha256": digest(profile),
        "changed_components": changed_components,
        "slices": outcomes,
        "limitations": [
            "A bounded-loss gate assumes independent clusters and fixed candidate, grader, profile and test set.",
            "The exact risk bound additionally assumes iid cluster event indicators under the declared sampling protocol.",
            "The family risk upper bound concerns any event in a cluster, not per-action accident probability.",
            "PASS is scoped evaluation evidence, not deployment authorization or universal optimality.",
        ],
    }


def reliability(run, k):
    """Unbiased all-k success estimator on independently drawn repeats; never pass@k."""
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    validate(run)
    grouped = defaultdict(list)
    metric = run["benchmark"]["primary_metric"]
    for row in run["units"]:
        if row["fresh_draw"] and value(row, metric) is not None:
            grouped[row["case_id"]].append(value(row, metric))
    values = [
        math.comb(int(sum(v)), k) / math.comb(len(v), k) if sum(v) >= k else 0.0
        for v in grouped.values()
        if len(v) >= k
    ]
    return {
        "k": k,
        "analysis_sources": analysis_identity(),
        "eligible_cases": len(values),
        "total_cases": len({r["case_id"] for r in run["units"]}),
        "all_k_success": float(np.mean(values)) if values else None,
        "assumption": "fresh_draw is a producer assertion; independence must be audited outside the scorer",
    }
