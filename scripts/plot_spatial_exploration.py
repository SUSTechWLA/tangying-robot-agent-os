#!/usr/bin/env python3
"""Plot recorded paired voxel experiments without modifying campaign evidence.

Every dot is an actual episode, with thin lines joining its paired scene/seed
block across variants. No interpolation, synthetic observations, confidence
intervals, significance tests or physical-success interpretation are added.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

METRICS = (
    ("coverageFraction", "Observed ROI voxel coverage", "Coverage (%)", 100.),
    ("pathLengthM", "Successful route distance", "Distance (m)", 1.),
    ("messageBytes", "Observation payload exchange", "Payload (kB; decimal)", .001),
)
VARIANTS = {
    "baseline": ("Baseline", "#1b4965"),
    "nearest_frontier": ("Nearest frontier", "#c66129"),
    "no_global_reposition": ("No global reposition", "#527d50"),
    "no_visibility_gain": ("No visibility gain", "#865a91"),
    "no_coordination": ("No coordination", "#a7474e"),
}
MARKERS = ("o", "s", "^", "D", "v", "P", "X", "h")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a nonnegative finite recorded number")
    return float(value)


def load_records(directory):
    """Read and cross-check the two published numeric summaries.

    This does not replace the benchmark's event replay or constitute independent
    sensor/path validation. The figure records source hashes for traceability.
    """
    summary_path, csv_path = directory / "summary.json", directory / "episodes.csv"
    summary = json.loads(summary_path.read_text())
    if summary.get("schemaVersion") != "spatial.campaign-summary.v1":
        raise ValueError("input requires spatial.campaign-summary.v1")
    records = summary.get("episodes")
    if type(records) is not list or not records:
        raise ValueError("campaign has no recorded episodes")
    manifest_path = directory / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("state") != "finalized":
            raise ValueError("refusing to plot an unfinished or invalid campaign")
        for name in ("summary.json", "episodes.csv"):
            expected = manifest.get("files", {}).get(name)
            path = directory / name
            if expected is None or expected.get("sha256") != sha(path) or expected.get("bytes") != path.stat().st_size:
                raise ValueError(f"campaign manifest does not authenticate {name}")
    with csv_path.open(newline="") as stream:
        csv_records = list(csv.DictReader(stream))
    by_key = {}
    for record in records:
        if (type(record.get("sceneId")) is not str or not record["sceneId"]
                or type(record.get("seed")) is not int
                or record.get("variant") not in VARIANTS
                or record.get("physicalExecution") is not False):
            raise ValueError("episode requires scene, integer seed, known variant and physicalExecution=false")
        key = (record["sceneId"], record["seed"], record["variant"])
        if key in by_key:
            raise ValueError(f"duplicate paired episode: {key}")
        for metric, _, _, _ in METRICS:
            value = _number(record.get(metric), metric)
            if metric == "coverageFraction" and value > 1:
                raise ValueError("coverageFraction must be in [0,1]")
        by_key[key] = record
    if len(csv_records) != len(records):
        raise ValueError("summary and episodes.csv have different episode counts")
    seen = set()
    for record in csv_records:
        key = (record["sceneId"], int(record["seed"]), record["variant"])
        if key not in by_key or key in seen:
            raise ValueError("CSV episode identities differ from summary")
        seen.add(key)
        for metric, _, _, _ in METRICS:
            if float(record[metric]) != float(by_key[key][metric]):
                raise ValueError(f"CSV disagrees with summary for {key}, {metric}")
    scenes = list(dict.fromkeys(record["sceneId"] for record in records))
    variants = [variant for variant in VARIANTS if any(record["variant"] == variant for record in records)]
    seeds = {scene: sorted({record["seed"] for record in records if record["sceneId"] == scene}) for scene in scenes}
    for scene in scenes:
        if any((scene, seed, variant) not in by_key for seed in seeds[scene] for variant in variants):
            raise ValueError("figure requires complete paired scene/seed blocks across variants")
    return records, by_key, scenes, variants, seeds


def plot_campaign(directory, output):
    directory, output = directory.resolve(), output.resolve()
    if directory == output or directory in output.parents:
        raise ValueError("derived figures must be outside the immutable input campaign")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite figure output: {output}")
    records, by_key, scenes, variants, seeds = load_records(directory)
    inputs = {name: sha(directory / name) for name in ("summary.json", "episodes.csv")}
    if (directory / "manifest.json").exists():
        inputs["manifest.json"] = sha(directory / "manifest.json")
    output.mkdir(parents=True, exist_ok=False)
    matplotlib.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                                "axes.titlesize": 11, "axes.labelsize": 10,
                                "svg.fonttype": "none", "savefig.facecolor": "white"})
    figure, axes = plt.subplots(1, 3, figsize=(14.5, 5.2))
    offsets = [0.] if len(variants) == 1 else [-.32 + .64*i/(len(variants)-1) for i in range(len(variants))]
    numeric_rows = []
    for panel, (metric, title, unit, scale) in enumerate(METRICS):
        axis = axes[panel]
        for scene_index, scene in enumerate(scenes):
            if scene_index % 2 == 0:
                axis.axvspan(scene_index-.46, scene_index+.46, color="#f1f4f5", zorder=0)
            scene_seeds = seeds[scene]
            for seed_index, seed in enumerate(scene_seeds):
                # This offset separates measured samples; it never changes Y.
                jitter = 0. if len(scene_seeds) == 1 else -.016 + .032*seed_index/(len(scene_seeds)-1)
                xs = [scene_index+offset+jitter for offset in offsets]
                ys = [by_key[(scene, seed, variant)][metric]*scale for variant in variants]
                axis.plot(xs, ys, color="#a3a9ad", linewidth=.65, alpha=.65, zorder=1)
                for x, y, variant in zip(xs, ys, variants, strict=True):
                    axis.scatter(x, y, color=VARIANTS[variant][1], marker=MARKERS[seed_index % len(MARKERS)],
                                 s=38, edgecolor="white", linewidth=.55, zorder=3)
                    numeric_rows.append({"sceneId": scene, "seed": seed, "variant": variant,
                                         "metric": metric, "rawValue": by_key[(scene, seed, variant)][metric],
                                         "plottedValue": y, "scale": scale})
        axis.set(title=f"{chr(97+panel)}) {title}", ylabel=unit, xlim=(-.55, len(scenes)-.45))
        axis.set_xticks(range(len(scenes)), [scene.replace("_", "\n") for scene in scenes])
        axis.grid(axis="y", alpha=.22, linewidth=.7)
        axis.spines[["top", "right"]].set_visible(False)
        axis.set_axisbelow(True)
        if metric == "coverageFraction":
            axis.set_ylim(0, 103)
        else:
            axis.set_ylim(bottom=0)
    counts = "; ".join(f"{scene.replace('_', ' ')}: n={len(seeds[scene])}" for scene in scenes)
    figure.suptitle("Heterogeneous exploration — synthetic voxel software experiments", y=.97,
                   fontsize=13, fontweight="semibold")
    figure.text(.5, .905, counts + " paired scene/seed blocks", ha="center", fontsize=8.5)
    legend = [Line2D([0], [0], marker="o", linestyle="none", color=VARIANTS[variant][1],
                     label=VARIANTS[variant][0], markersize=6) for variant in variants]
    figure.legend(handles=legend, loc="lower center", bbox_to_anchor=(.5, .095),
                  ncol=min(5, len(variants)), frameon=False, fontsize=8.5)
    figure.text(.5, .062, "Each dot is one recorded episode; gray lines connect paired seeds. "
                "No confidence intervals or significance claims.", ha="center", fontsize=8)
    figure.text(.5, .025, "Static ideal-sensor fixtures; no physical robot completion or energy claim. "
                "Payload excludes network framing and control messages.", ha="center", fontsize=8,
                color="#555b60")
    figure.subplots_adjust(left=.065, right=.985, bottom=.265, top=.83, wspace=.31)
    figure.savefig(output / "paired-exploration.png", dpi=240)
    figure.savefig(output / "paired-exploration.svg")
    plt.close(figure)
    with (output / "plotted-values.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=numeric_rows[0])
        writer.writeheader()
        writer.writerows(numeric_rows)
    if any(sha(directory / name) != expected for name, expected in inputs.items()):
        raise RuntimeError("input campaign changed while rendering; retain but do not cite this output")
    metadata = {"schemaVersion": "research.spatial-exploration-figure.v1", "input": str(directory),
                "inputs": inputs, "scriptSha256": sha(Path(__file__)),
                "matplotlibVersion": matplotlib.__version__, "episodeN": len(records),
                "pairedSeeds": seeds, "metrics": [item[0] for item in METRICS],
                "sampleInterpretation": "Descriptive paired samples; no statistical or physical optimality claim.",
                "validation": "Cross-checked summary/CSV and manifest hashes, not a new sensor or path replay.",
                "files": {path.name: sha(path) for path in sorted(output.iterdir()) if path.is_file()}}
    (output / "provenance.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="finalized campaign directory")
    parser.add_argument("--output", type=Path, required=True, help="new derived-figure directory")
    args = parser.parse_args(argv)
    print(plot_campaign(args.input, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
