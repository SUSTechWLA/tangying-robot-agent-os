"""Reproduce descriptive, geometry-clustered analysis of a frozen voxel study.

The percentile intervals describe resampling this study's physical layouts.
They are not multiplicity-adjusted significance tests, population guarantees,
proof of universal optimality, or evidence of physical robot performance.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pathlib
import platform
import random
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import spatial_exploration_benchmark as benchmark

DEFAULT_SEED = 20261018
DEFAULT_REPLICATES = 5000
BASELINE = "baseline"
METRICS = ("completed", "coverageFraction", "pathLengthM", "planningMsTotal", "messageBytes")
SELECTION_CRITERION = [
    "completedFraction descending",
    "meanCoverage descending",
    "meanSuccessfulPathLengthM ascending",
    "meanObservationJSONBytes ascending",
    "variant lexicographic ascending",
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} requires a measured finite nonnegative number")
    return float(value)


def _value(row, metric):
    if metric == "completed":
        if not isinstance(row.get("status"), str) or not row["status"]:
            raise ValueError("episode status is required; failures must not be dropped")
        return float(row["status"] == "completed")
    value = _number(row.get(metric), metric)
    if metric == "coverageFraction" and value > 1:
        raise ValueError("coverageFraction exceeds one")
    return value


def _mean(values):
    values = list(values)
    return math.fsum(values) / len(values) if values else None


def summarize_variants(rows):
    """All episodes, including partial/failure episodes, enter each mean.

    pathLengthM already sums only succeeded geometric legs. We do not condition
    on completed episodes or divide by the number of legs. messageBytes is the
    recorded observation/coverage JSON payload total, not complete network use.
    meanObservationJSONBytes retains its original field name for compatibility;
    it includes occupancy relays and coverage-ID delta/snapshot payloads too.
    Missing required measurements are rejected rather than replaced with zero.
    """
    grouped = defaultdict(list)
    for row in rows:
        variant = row.get("variant")
        if not isinstance(variant, str) or not variant:
            raise ValueError("each row requires a variant")
        for metric in METRICS:
            _value(row, metric)
        grouped[variant].append(row)
    if not grouped:
        raise ValueError("cannot summarize an empty study")
    result = []
    for variant, episodes in sorted(grouped.items()):
        counts = dict(sorted(Counter(row["status"] for row in episodes).items()))
        geometry = {row["occupancyGeometryHash"] for row in episodes if row.get("occupancyGeometryHash")}
        result.append({
            "variant": variant, "N": len(episodes), "statusCounts": counts,
            "completedN": counts.get("completed", 0),
            "completedFraction": _mean(_value(row, "completed") for row in episodes),
            "meanCoverage": _mean(_value(row, "coverageFraction") for row in episodes),
            "meanSuccessfulPathLengthM": _mean(_value(row, "pathLengthM") for row in episodes),
            "meanPlanningMsTotal": _mean(_value(row, "planningMsTotal") for row in episodes),
            "meanObservationJSONBytes": _mean(_value(row, "messageBytes") for row in episodes),
            "geometryClusterN": len(geometry) if len(geometry) and all(row.get("occupancyGeometryHash") for row in episodes) else None,
        })
    return result


def select_variant(rows):
    """Apply the preregistered order exactly; return one variant name.

    No planning-time weight, partial-episode filter, leg normalization, tolerance
    band, or retrospectively chosen utility score is introduced here.
    """
    summaries = summarize_variants(rows)
    return min(summaries, key=lambda item: (
        -item["completedFraction"], -item["meanCoverage"],
        item["meanSuccessfulPathLengthM"], item["meanObservationJSONBytes"], item["variant"],
    ))["variant"]


def _block(row):
    if not isinstance(row.get("sceneId"), str) or type(row.get("seed")) is not int:
        raise ValueError("paired rows require sceneId and integer seed")
    return row["sceneId"], row["seed"]


def paired_differences(rows):
    """Return all variant-minus-baseline deltas, never silently drop a pair."""
    indexed = {}
    for row in rows:
        key = row["variant"], *_block(row)
        if key in indexed:
            raise ValueError("duplicate variant/scene/seed episode")
        if not isinstance(row.get("occupancyGeometryHash"), str) or not row["occupancyGeometryHash"]:
            raise ValueError("paired geometry-cluster analysis requires occupancyGeometryHash")
        indexed[key] = row
    variants = sorted({row["variant"] for row in rows})
    if BASELINE not in variants:
        raise ValueError("paired comparisons require the baseline arm")
    baseline = {_block(row): row for row in rows if row["variant"] == BASELINE}
    differences = []
    for variant in variants:
        blocks = {_block(row) for row in rows if row["variant"] == variant}
        if blocks != set(baseline):
            raise ValueError("unbalanced paired design: variant and baseline blocks differ")
        if variant == BASELINE:
            continue
        for block, reference in sorted(baseline.items()):
            row = indexed[(variant, *block)]
            if row["occupancyGeometryHash"] != reference["occupancyGeometryHash"]:
                raise ValueError("paired occupancy geometries differ")
            if row.get("pairedInputHash") != reference.get("pairedInputHash"):
                raise ValueError("complete frozen inputs differ within a pair")
            for metric in METRICS:
                before, after = _value(reference, metric), _value(row, metric)
                differences.append({
                    "sceneId": block[0], "seed": block[1], "variant": variant,
                    "relativeTo": BASELINE, "occupancyGeometryHash": row["occupancyGeometryHash"],
                    "metric": metric, "baselineValue": before, "variantValue": after,
                    "delta": after - before,
                    "relativeDelta": (after - before) / before if before != 0 else None,
                })
    return differences


def _percentile(values, quantile):
    ordered = sorted(values)
    at = (len(ordered) - 1) * quantile
    low, high = math.floor(at), math.ceil(at)
    return ordered[low] + (ordered[high] - ordered[low]) * (at - low)


def cluster_bootstrap(differences, *, seed=DEFAULT_SEED, replicates=DEFAULT_REPLICATES):
    """Equal-weight geometry clusters, with conditions averaged inside a cluster.

    All metrics/arms use the same resampled geometry indices. Fault conditions
    sharing a layout remain together; a layout with two conditions does not gain
    twice the weight of a layout with one condition. The paired episode mean is
    reported separately from this cluster-normalized estimand.
    """
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("bootstrap seed must be a bounded nonnegative integer")
    if type(replicates) is not int or not 100 <= replicates <= 100_000:
        raise ValueError("bootstrap replicates must be an integer in 100..100000")
    grouped = defaultdict(lambda: defaultdict(list))
    for row in differences:
        grouped[(row["variant"], row["metric"])][row["occupancyGeometryHash"]].append(row["delta"])
    if not grouped:
        return []
    cluster_ids = sorted(next(iter(grouped.values())))
    if any(sorted(values) != cluster_ids for values in grouped.values()):
        raise ValueError("all paired metrics/arms must have the same physical geometry clusters")
    means = {key: [_mean(values[cluster]) for cluster in cluster_ids] for key, values in grouped.items()}
    samples = {key: [] for key in grouped}
    if len(cluster_ids) >= 2:
        generator = random.Random(seed)
        for _ in range(replicates):
            indices = [generator.randrange(len(cluster_ids)) for _ in cluster_ids]
            for key, vector in means.items():
                samples[key].append(_mean(vector[index] for index in indices))
    effects = []
    for key, clusters in sorted(grouped.items()):
        values = samples[key]
        effects.append({
            "variant": key[0], "relativeTo": BASELINE, "metric": key[1],
            "pairedEpisodeN": sum(len(items) for items in clusters.values()),
            "geometryClusterN": len(cluster_ids),
            "episodeWeightedMeanDelta": _mean(value for items in clusters.values() for value in items),
            "clusterNormalizedMeanDelta": _mean(means[key]),
            "clusterSizes": {cluster: len(clusters[cluster]) for cluster in cluster_ids},
            "descriptive95PercentileCI": [_percentile(values, 0.025), _percentile(values, 0.975)] if values else None,
            "effectiveBootstrapReplicates": replicates if values else 0,
            "intervalLimitation": None if values else "Fewer than two distinct geometry clusters; a degenerate interval would imply unsupported precision.",
        })
    return effects


def _write_csv(path, rows):
    with path.open("x", newline="") as stream:
        if not rows:
            stream.write("variant,metric\n")
            return
        columns = [key for key in rows[0] if not isinstance(rows[0][key], (dict, list))]
        writer = csv.DictWriter(stream, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def analyze(campaign, output, *, seed=DEFAULT_SEED, replicates=DEFAULT_REPLICATES):
    campaign, output = pathlib.Path(campaign), pathlib.Path(output)
    manifest_bytes = (campaign / "manifest.json").read_bytes()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    verification = benchmark.replay(campaign)
    if (campaign / "manifest.json").read_bytes() != manifest_bytes:
        raise ValueError("campaign manifest changed during verification")
    manifest = json.loads(manifest_bytes)
    summary_bytes = (campaign / "summary.json").read_bytes()
    if hashlib.sha256(summary_bytes).hexdigest() != manifest["files"]["summary.json"]["sha256"]:
        raise ValueError("campaign summary changed after replay")
    rows = json.loads(summary_bytes)["episodes"]
    variants = summarize_variants(rows)
    differences = paired_differences(rows)
    effects = cluster_bootstrap(differences, seed=seed, replicates=replicates)
    source_path = pathlib.Path(__file__).resolve()
    verifier_path = pathlib.Path(benchmark.__file__).resolve()
    sources = {source_path.name: source_path.read_bytes(), verifier_path.name: verifier_path.read_bytes()}
    source_hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in sources.items()}
    method = {
        "schemaVersion": "spatial.analysis-method.v1", "selectionCriterion": SELECTION_CRITERION,
        "metricDirections": {"completed": "maximize", "coverageFraction": "maximize",
                             "pathLengthM": "minimize", "planningMsTotal": "minimize", "messageBytes": "minimize"},
        "episodeMeans": "All episodes, including partial/failure, have equal weight. pathLengthM sums succeeded geometry legs; do not divide by legs or retain only completed episodes.",
        "pairedDifference": "variant minus baseline within the exact same sceneId/seed and frozen input; relativeDelta is null when baseline is zero.",
        "cluster": "occupancyGeometryHash; average paired condition deltas inside each geometry, then give each geometry equal weight.",
        "bootstrap": {"seed": seed, "replicates": replicates, "sampling": "Geometry clusters with replacement, preserving all paired fault conditions and all metrics/arms together.",
                      "interval": "Descriptive 2.5th and 97.5th percentiles, linearly interpolated at (replicates-1)*q.",
                      "minimumDistinctGeometryClusters": 2},
        "communication": (
            "meanObservationJSONBytes is the arithmetic mean of episode.messageBytes over all episodes; "
            "the historical field name is retained for compatibility and is not limited to raw observations. "
            "Count all actual recorded communication channels, including observation_upload, occupancy_relay, "
            "coverage_delta (coverage IDs), coverage_reconnect_snapshot (coverage IDs), reconnect_snapshot "
            "(occupancy), and the legacy combined observation_upload_and_relay channel when present. "
            "Delayed maps count only when sent; unsent queued packets are not transmitted bytes. "
            "These are canonical JSON payload bytes, excluding network framing, TLS, task leases, "
            "controller traffic and other unmeasured transport overhead."
        ),
        "inputCommunicationDescription": manifest.get("communicationBytesDefinition"),
        "limitations": ["No multiple-comparison adjustment or significance decision.",
                        "Intervals describe this fixed study's empirical layouts; no universal optimality or population generalization claim.",
                        "Planning wall time depends on host load; this is not physical robot or network validation.",
                        "Configured seeds/paired task conditions are not necessarily independent physical maps; cluster normalization prevents duplicate geometry from increasing its weight."],
    }
    result = {"schemaVersion": "spatial.study-analysis.v1", "variants": variants,
              "selectedVariant": select_variant(rows), "selectionCriterion": SELECTION_CRITERION,
              "pairedEffects": effects, "pairedEpisodeBlockN": len({_block(row) for row in rows}),
              "geometryClusterN": len({row["occupancyGeometryHash"] for row in rows}),
              "split": manifest.get("split", "unspecified"), "studyId": manifest.get("studyId"),
              "selectionScope": ("development_selection" if manifest.get("split") == "development"
                                 else "descriptive_ranking_only_do_not_replace_a_frozen_development_choice"),
              "inputManifestHash": manifest_hash, "methodHash": digest(method),
              "sourceHash": source_hashes[source_path.name],
              "claims": {"universalOptimality": False, "physicalRobotValidation": False,
                         "multiplicityAdjustedInference": False}}
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "source"
    snapshot.mkdir()
    for name, payload in sources.items():
        (snapshot / name).write_bytes(payload)
    write_json(output / "method.json", method)
    write_json(output / "summary.json", result)
    write_json(output / "paired-differences.json", differences)
    _write_csv(output / "variants.csv", variants)
    _write_csv(output / "paired-differences.csv", differences)
    effect_rows = [{**effect,
                    "ci95Lower": effect["descriptive95PercentileCI"][0] if effect["descriptive95PercentileCI"] else None,
                    "ci95Upper": effect["descriptive95PercentileCI"][1] if effect["descriptive95PercentileCI"] else None}
                   for effect in effects]
    _write_csv(output / "paired-effects.csv", effect_rows)
    provenance = {"schemaVersion": "spatial.analysis-provenance.v1", "state": "finalized",
                  "inputCampaign": str(campaign.resolve()), "inputManifestHash": manifest_hash,
                  "inputSummaryHash": hashlib.sha256(summary_bytes).hexdigest(),
                  "inputConfigHash": manifest["configHash"], "inputStudyImplementationHash": manifest.get("studyImplementationHash"),
                  "campaignSourceHashes": manifest["sourceHashes"], "analysisSourceHashes": source_hashes,
                  "methodHash": digest(method), "verification": verification,
                  "runtime": {"python": sys.version, "platform": platform.platform()},
                  "files": {str(path.relative_to(output)): {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
                            for path in sorted(output.rglob("*")) if path.is_file()}}
    write_json(output / "provenance.json", provenance)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campaign", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    args = parser.parse_args(argv)
    result = analyze(args.campaign, args.output, seed=args.seed, replicates=args.replicates)
    print(json.dumps({"output": str(args.output), "selectedVariant": result["selectedVariant"],
                      "geometryClusterN": result["geometryClusterN"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
