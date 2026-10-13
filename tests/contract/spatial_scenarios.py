"""Synthetic bounded maps for repeatable ROS2 protocol experiments, not sensor data."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def write_scenario(kind: str, directory: Path) -> Path:
    profile = json.loads((ROOT / f"examples/robots/{kind}.commissioning.profile.json").read_text())
    directory.mkdir(parents=True, exist_ok=False)
    nx, ny, nz = 16, 16, 12
    cells = [0] * (nx * ny * nz)
    # A measured synthetic barrier separates start and kitchen. UAV must climb
    # above it; ground robots must travel around its end, on explicit support.
    for z in range(6):
        for y in range(5, 11):
            cells[(z * ny + y) * nx + 10] = 100
    volume = {"schemaVersion": "navigation.volume.v1", "robotId": profile["robotId"],
        "mapId": "synthetic-spatial-fixture", "mapRevision": "pending",
        "frameId": "world", "worldFrameRevision": "synthetic-world-v1",
        "calibrationRevision": profile["sensors"][0]["transformRevision"],
        "resolution": .25, "origin": [-2., -2., -.5], "shape": [nx, ny, nz], "cells": cells}
    policy = {"mode": "volume3d" if kind == "uav" else "terrain25d",
        "bodyHalfExtents": [.04, .04, .04], "margin": .01,
        "maxExpanded": 10000, "maxWaypoints": 1024}
    if kind != "uav":
        volume["terrain"] = {"schemaVersion": "navigation.terrain.v1", "heights": [-.25] * (nx * ny),
            "supported": [True] * (nx * ny), "slopeRadians": [0.] * (nx * ny),
            "sourceId": "synthetic-survey", "observedAtUnixMs": int(time.time() * 1000)}
        policy.update(maxSlopeRad=.3 if kind == "ugv" else .6,
                      maxStepM=.05 if kind == "ugv" else .2, referenceHeightM=.25)
    volume["mapRevision"] = hashlib.sha256(json.dumps(volume, sort_keys=True).encode()).hexdigest()
    sensor = profile["sensors"][0]
    config = {"schemaVersion": "spatial.deployment.v1", "volumePath": "volume.json", "policy": policy,
        "localization": {"sourceId": sensor["sourceId"], "sourceType": sensor["sourceType"],
            "sourceFrameId": sensor["frameId"], "maxAgeMs": 500,
            "maxPositionVarianceM2": .1, "maxOrientationVarianceRad2": .1}}
    (directory / "volume.json").write_text(json.dumps(volume, indent=2) + "\n")
    (directory / "deployment.json").write_text(json.dumps(config, indent=2) + "\n")
    return directory / "deployment.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("ugv", "legged", "uav"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(write_scenario(args.kind, args.output))
