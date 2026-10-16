#!/usr/bin/env python3
"""Plot measured keyframe yaw for frozen first-scan experiments, not coverage."""
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
import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True,
                        help="completed experiment roots containing evidence/attempt-001")
    parser.add_argument("--output", type=Path, required=True, help="new derived-figure directory")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    inputs, rows = {}, []
    figure, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    palette = ("#b6602d", "#7566a5", "#176f83", "#39703e")
    for index, run in enumerate(args.runs):
        evidence = run / "evidence/attempt-001"
        status_path = evidence / "final-state.json"
        session_path = evidence / "map-package/slam-session.json"
        status, session = json.loads(status_path.read_text()), json.loads(session_path.read_text())
        inputs[str(status_path)] = sha(status_path)
        inputs[str(session_path)] = sha(session_path)
        scan = next(look for look in status["scanEvidence"]["looks"] if look["phase"] == "initial_scan")
        observations = session["observations"]
        stamps = np.array([item["stamp"] for item in observations], dtype=float)
        odometry = np.array([item["odometry"] for item in observations], dtype=float)
        elapsed = (stamps - stamps[0]) / 1000.
        # These are registered keyframe samples; continuous control/observation
        # yaw may include motion beyond the last accepted keyframe.
        advance = np.unwrap(odometry[:, 2]) - odometry[0, 2]
        during = elapsed <= scan["elapsedSeconds"] + .25
        label = f"{index+1:03d} ({scan['status']})"
        colour = palette[index % len(palette)]
        axes[0].plot(elapsed[during], np.degrees(advance[during]), "o-", markersize=2.5,
                     linewidth=1.5, color=colour, label=label)
        axes[1].plot(odometry[:, 0], odometry[:, 1], color=colour, linewidth=1.5,
                     label=f"{index+1:03d}: {status['operationTravelledM']:.3f} m")
        axes[1].scatter(*odometry[0, :2], marker="o", color=colour, s=22)
        axes[1].scatter(*odometry[-1, :2], marker="x", color=colour, s=30)
        for item, seconds, yaw in zip(observations, elapsed, advance, strict=True):
            rows.append({"run": run.name, "frame": item["index"], "stampUnixMs": item["stamp"],
                         "elapsedS": float(seconds), "xM": item["odometry"][0],
                         "yM": item["odometry"][1], "yawRad": item["odometry"][2],
                         "unwrappedYawAdvanceRad": float(yaw)})
    axes[0].axhline(360, color="#59616a", linestyle="--", linewidth=1)
    axes[0].set(xlabel="Time from first registered keyframe (s)",
                ylabel="Measured yaw advance (degrees)", title="Initial scan: keyframe evidence")
    axes[0].legend(fontsize=7, loc="lower right")
    axes[1].set(xlabel="Driver-world x (m)", ylabel="Driver-world y (m)",
                title="Exploration: registered odometry")
    axes[1].set_aspect("equal", adjustable="datalim")
    axes[1].legend(fontsize=8, loc="best")
    for axis in axes:
        axis.grid(alpha=.18)
        axis.spines[["top", "right"]].set_visible(False)
    figure.savefig(args.output / "first-scan.png", dpi=180)
    figure.savefig(args.output / "first-scan.svg")
    plt.close(figure)
    with (args.output / "registered-odometry.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    metadata = {"schemaVersion": "research.firstscan-figure.v1", "inputs": inputs,
                "scriptSha256": sha(Path(__file__)), "matplotlibVersion": matplotlib.__version__,
                "numpyVersion": np.__version__, "units": {"distance": "metres", "yaw": "radians"},
                "scope": "registered keyframe odometry, not scene coverage or continuous ground truth",
                "yawThresholdRad": 2*math.pi,
                "files": {path.name: sha(path) for path in sorted(args.output.iterdir()) if path.is_file()}}
    (args.output / "provenance.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
