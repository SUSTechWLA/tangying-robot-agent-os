"""A/B: how much of the map can the explorer actually reach, old rule vs new.

The question this answers is specific, because "coverage" on its own invites a
number that cannot be checked. A survey fails to finish when frontier cells remain
that *some reachable pose can see*, but the policy has no such pose to plan to. So
the measured quantity is:

    of the frontier cells on a real map, how many does the policy have a plannable
    viewpoint for?

Two rules are compared on the same grid and the same clearance:

* ``near`` - the original rule. A frontier cell is usable only if the robot can
  stand within 0.6 m of it. A cluster where no sample has such a cell is dropped
  entirely, and its unknown space is never targeted.
* ``reach`` - the upgraded rule. The search extends to the sensor's own reach
  (3 m by default), because the question a frontier asks is "from where can I see
  this", not "can I stand on top of it".

Run against a recorded map so the numbers are about this house rather than a
synthetic one::

    python scripts/exploration_coverage_benchmark.py
    python scripts/exploration_coverage_benchmark.py --grid artifacts/maps/sim-home/grid
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "robot" / "gateway"))

from tangying_robot_gateway.exploration import (
    MAX_FRONTIER_CLUSTERS,
    Grid,
    _components,
    _nearest_via_tree,
    _traversable_tree,
    explore_target,
    frontier_mask,
)


def load_grid(directory: pathlib.Path) -> Grid:
    meta = json.loads((directory / "occupancy.json").read_text())
    cells = np.load(directory / "occupancy.npy")
    return Grid(cells=cells, resolution=float(meta["resolution"]),
                origin=(float(meta["origin"][0]), float(meta["origin"][1])))


def audit(grid: Grid, *, clearance_m: float, sensor_radius_m: float,
          min_frontier_cells: int) -> dict:
    """Which frontier clusters the policy can act on, under each rule."""
    from scipy import ndimage

    cells = grid.cells
    frontier = frontier_mask(cells)
    traversable = np.ones_like(cells, dtype=bool) & (cells == 0)
    from tangying_robot_gateway.exploration import clearance_mask

    traversable &= clearance_mask(cells, math.ceil(clearance_m / grid.resolution))
    tree, points, coordinates = _traversable_tree(traversable)
    labels, count = _components(frontier)
    sizes = np.bincount(labels[frontier].ravel(), minlength=count)
    boxes = ndimage.find_objects(labels + 1)
    near_reach = max(1, math.ceil(0.6 / grid.resolution))
    far_reach = max(1, math.ceil(sensor_radius_m / grid.resolution))

    considered = [int(i) for i in np.argsort(sizes)[::-1][:MAX_FRONTIER_CLUSTERS]
                  if sizes[i] >= min_frontier_cells]
    totals = {"clusters": int(count), "considered": len(considered),
              "frontierCells": int(frontier.sum()),
              "nearCells": 0, "reachCells": 0, "nearClusters": 0, "reachClusters": 0}
    for label in np.argsort(sizes)[::-1]:
        if sizes[label] < min_frontier_cells:
            continue
        window = boxes[label]
        if window is None:
            continue
        rows, columns = np.nonzero(labels[window] == label)
        rows = rows + window[0].start
        columns = columns + window[1].start
        sampled = np.linspace(0, len(rows) - 1, num=min(24, len(rows))).astype(int)
        near = reach = False
        for index in sampled:
            row, column = int(rows[index]), int(columns[index])
            if not near and _nearest_via_tree(tree, points, coordinates, row, column,
                                              near_reach) is not None:
                near = True
            if not reach and _nearest_via_tree(tree, points, coordinates, row, column,
                                               far_reach) is not None:
                reach = True
        size = int(sizes[label])
        if near:
            totals["nearCells"] += size
            totals["nearClusters"] += 1
        if reach:
            totals["reachCells"] += size
            totals["reachClusters"] += 1
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--grid", type=pathlib.Path,
                        default=pathlib.Path("artifacts/maps/furnished-home/scan-6974b8f937e9/grid"))
    parser.add_argument("--clearance-m", type=float, default=0.32)
    parser.add_argument("--sensor-radius-m", type=float, default=3.0)
    parser.add_argument("--min-frontier-cells", type=int, default=8)
    args = parser.parse_args()
    if not (args.grid / "occupancy.npy").is_file():
        print(f"没有找到栅格：{args.grid}", file=sys.stderr)
        return 2

    grid = load_grid(args.grid)
    unknown = float((grid.cells < 0).mean())
    print(f"地图 {args.grid}\n  尺寸 {grid.cells.shape} 分辨率 {grid.resolution} m "
          f"未知占比 {unknown:.1%} 规划余量 {args.clearance_m} m 传感器 {args.sensor_radius_m} m")
    result = audit(grid, clearance_m=args.clearance_m, sensor_radius_m=args.sensor_radius_m,
                   min_frontier_cells=args.min_frontier_cells)
    print(f"\nfrontier 簇 {result['clusters']} 个，其中 >= {args.min_frontier_cells} 格的 "
          f"{result['considered']} 个进入候选（上限 {MAX_FRONTIER_CLUSTERS}）")
    print(f"  0.6 m 规则（旧）: {result['nearClusters']:3d} 簇可行动 / "
          f"{result['nearCells']:5d} 格已知边界")
    print(f"  传感器可达（新）: {result['reachClusters']:3d} 簇可行动 / "
          f"{result['reachCells']:5d} 格已知边界")
    gained = result["reachCells"] - result["nearCells"]
    share = 100.0 * gained / max(1, result["frontierCells"])
    print(f"  差异: +{gained} 格，占全部 frontier 的 {share:.1f}%")

    start = time.perf_counter()
    target = explore_target(grid, robot_xy=(0.5, -1.5), sensor_radius_m=args.sensor_radius_m,
                            radius_m=args.clearance_m,
                            min_frontier_cells=args.min_frontier_cells)
    elapsed = (time.perf_counter() - start) * 1000
    print(f"\n一次 explore_target 决策耗时 {elapsed:.0f} ms，"
          f"选中 {'无' if target is None else tuple(round(v, 2) for v in target.viewpoint)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
