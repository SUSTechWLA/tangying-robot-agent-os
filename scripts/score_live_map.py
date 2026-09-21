#!/usr/bin/env python3
"""Score a *live* survey's published map against a simulator's ground truth.

The two numbers this repository quotes for coverage are not the same number, and
until they are put on one denominator the comparison is not a comparison:

* a running survey reports ``1 - unknownFraction``, and that fraction is taken over
  **every cell of its own grid** - including the space outside the house, the
  inside of walls, and floor sealed behind furniture that no pose can see;
* the closed-loop benchmark reports ``mapped / coverable``, where ``coverable`` is
  the floor some legal reachable pose can actually see.

On the Gazebo house the first reads about 73% and the second about 99.5%, and it is
tempting to call the 26-point difference a mapping-layer defect. It is mostly the
denominator. This script settles it by projecting the live map into the truth frame
and counting it the way the benchmark counts - so "the live robot maps less" becomes
a claim with a number behind it, or is retired.

Usage::

    python scripts/score_live_map.py --map /path/to/scan-xxxx --truth gazebo
    python scripts/score_live_map.py --run artifacts/.../summary.json --truth gazebo
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "robot" / "gateway"))

import exploration_survey_benchmark as bench
from tangying_robot_gateway.exploration import OCCUPIED


def truth_for(name: str):
    cache = ROOT / "artifacts" / "benchmark" / (
        "survey-truth.npz" if name == "mujoco" else "survey-truth-gazebo.npz")
    if not cache.is_file():
        raise SystemExit(
            f"no cached truth at {cache}; build it with\n"
            f"  python scripts/exploration_survey_benchmark.py --truth {name}")
    saved = np.load(cache)
    occupied = saved["occupied"]
    origin = tuple(float(v) for v in saved["origin"])
    start = tuple(float(v) for v in saved["start"]) if "start" in saved else (0.0, 0.0)
    start_rc = (int((start[1] - origin[1]) / bench.RESOLUTION),
                int((start[0] - origin[0]) / bench.RESOLUTION))
    reachable = bench.reachable_free(occupied, *start_rc)
    coverable = bench.coverable_cells(occupied, reachable) & (reachable | occupied)
    return occupied, origin, reachable, coverable


def load_live_map(directory: pathlib.Path):
    """The published navigation grid, plus the anchor that puts it in the world.

    Both come out of the same verified package the planner reads, so this scores
    the map the robot would actually navigate on rather than an artefact kept
    beside it.
    """
    from tangying_robot_gateway.map_catalog import MapCatalog

    manifest = json.loads((directory / "manifest.json").read_text())
    grid = MapCatalog.navigation_grid(directory, manifest)
    session = json.loads((directory / manifest["artifacts"]["slam_session"]["href"]).read_text())
    anchor = np.asarray(session["mapFromWorld"], dtype=float)
    if anchor.shape != (3,) or not np.isfinite(anchor).all():
        raise SystemExit("the package's mapFromWorld anchor is not a finite planar pose")
    return grid, anchor, session


def score(directory: pathlib.Path, truth: str) -> dict:
    occupied, origin, reachable, coverable = truth_for(truth)
    grid, anchor, session = load_live_map(directory)
    cells = np.asarray(grid["cells"])
    resolution = float(grid["resolution"])
    grid_origin = np.asarray(grid["origin"], dtype=float)[:2]

    # Cell centres in the map frame, then in the truth frame. The anchor is the
    # map origin's pose in the world, so undoing it is a rotation and a translation.
    rows, columns = np.mgrid[0:cells.shape[0], 0:cells.shape[1]]
    map_x = grid_origin[0] + (columns + 0.5) * resolution
    map_y = grid_origin[1] + (rows + 0.5) * resolution
    cos, sin = np.cos(-anchor[2]), np.sin(-anchor[2])
    world_x = cos * (map_x - anchor[0]) - sin * (map_y - anchor[1])
    world_y = sin * (map_x - anchor[0]) + cos * (map_y - anchor[1])

    truth_columns = np.floor((world_x - origin[0]) / bench.RESOLUTION).astype(int)
    truth_rows = np.floor((world_y - origin[1]) / bench.RESOLUTION).astype(int)
    inside = ((truth_rows >= 0) & (truth_rows < occupied.shape[0])
              & (truth_columns >= 0) & (truth_columns < occupied.shape[1]))

    known = np.zeros(occupied.shape, dtype=bool)
    seen_free = np.zeros(occupied.shape, dtype=bool)
    seen_occupied = np.zeros(occupied.shape, dtype=bool)
    known[truth_rows[inside], truth_columns[inside]] = cells[inside] >= 0
    seen_free[truth_rows[inside], truth_columns[inside]] = cells[inside] == 0
    seen_occupied[truth_rows[inside], truth_columns[inside]] = cells[inside] >= OCCUPIED

    denominator = int(coverable.sum())
    mapped = int((coverable & known).sum())
    total = int(cells.size)
    unknown = int((cells < 0).sum())
    return {
        "truth": truth,
        "mapDirectory": str(directory),
        "mapId": session.get("mapId"),
        "gridCells": total,
        "gridResolution": resolution,
        # What a running survey reports, and the number it is easy to misread.
        "surveyUnknownFraction": round(unknown / total, 6) if total else 1.0,
        "surveyExploredFraction": round(1.0 - (unknown / total if total else 1.0), 6),
        # The benchmark's denominator, applied to the live map.
        "coverable": denominator,
        "coverableMapped": mapped,
        "liveCoverableCoverage": round(mapped / max(1, denominator), 6),
        # Sanity: the live map's own occupancy against the truth's.
        "liveFreeOnTruthObstacle": int((seen_free & occupied).sum()),
        "liveOccupiedOnTruthFree": int((seen_occupied & ~occupied & reachable).sum()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--map", type=pathlib.Path, required=True,
                        help="a published map package directory")
    parser.add_argument("--truth", choices=("mujoco", "gazebo"), default="gazebo")
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args()
    report = score(args.map, args.truth)
    print(f"真值来源            : {report['truth']}")
    print(f"地图包              : {report['mapId']}  ({report['gridCells']} 格 @ "
          f"{report['gridResolution']} m)")
    print(f"调查自己报的已探明  : {report['surveyExploredFraction']:.1%}"
          f"   (分母 = 自己栅格的全部 {report['gridCells']} 格)")
    print(f"与评测台同分母的覆盖: {report['liveCoverableCoverage']:.1%}"
          f"   ({report['coverableMapped']} / {report['coverable']} coverable)")
    print(f"  自证：把真值障碍当自由 {report['liveFreeOnTruthObstacle']} 格；"
          f"把可达空地当障碍 {report['liveOccupiedOnTruthFree']} 格")
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
