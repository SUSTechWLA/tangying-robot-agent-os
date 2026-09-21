"""Replay a sensor log through the mapping stack, offline and repeatably.

This is the other half of the frozen log: a log nobody replays is just a large
file. The contract is narrow on purpose - read frames in order, hand them to a
builder, report what came out - so that a change to any part of the mapping or
localization stack can be evaluated against the same input.

What it reports is chosen so two runs are comparable:

* ``frames``    - how many were fed in, and how many the builder accepted
* ``covered``   - truth cells the replay settled, over the reference map's known floor
* ``occupied``  - cells it called obstacles
* ``logDigest`` - the identity of the input, so a comparison can name what it used

The reference floor comes from a recorded scan when one is given, and the replay is
graded against it. Without a reference the numbers are still self-comparable run to
run, which is what an A/B between two implementations needs.

    python scripts/replay_sensor_log.py artifacts/replay/furnished-home
    python scripts/replay_sensor_log.py artifacts/replay/furnished-home --reference artifacts/maps/furnished-home/scan-6974b8f937e9
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot" / "gateway"))

from tangying_robot_gateway.map_pipeline import PointCloud, occupancy_from_points
from tangying_robot_gateway.rgbd import deproject
from tangying_robot_gateway.sensor_log import SensorLogReader


def build_grid(frames, *, resolution: float, use_origins: bool):
    """Accumulate every replayed frame into one occupancy grid.

    ``use_origins`` is the switch this whole exercise was built to be able to
    throw: with it, the space a measured return crossed counts as measured free;
    without it, only the cells a point landed in are known. Everything else about
    the two runs is identical, which is what makes the comparison mean something.
    """
    points: list[np.ndarray] = []
    colours: list[np.ndarray] = []
    sources: list[np.ndarray] = []
    origins: dict[int, tuple[float, float, float]] = {}
    low = np.array([np.inf, np.inf, np.inf])
    high = np.array([-np.inf, -np.inf, -np.inf])
    for index, logged in enumerate(frames):
        world, valid = deproject(logged.frame)
        selected = world[valid]
        if selected.size == 0:
            continue
        points.append(selected.astype(np.float32))
        colours.append(logged.frame.rgb[valid])
        sources.append(np.full(selected.shape[0], index, dtype=np.int64))
        # The camera position in the same frame the points are in: the last row of
        # the capture's world_from_camera is the world origin, so the translation
        # column is where the sensor was.
        transform = logged.frame.world_from_camera
        origins[index] = (float(transform[0, 3]), float(transform[1, 3]),
                          float(transform[2, 3]))
        low = np.minimum(low, selected.min(axis=0))
        high = np.maximum(high, selected.max(axis=0))
    if not points:
        return None
    xyz = np.concatenate(points)
    cloud = PointCloud(xyz=xyz, rgb=np.concatenate(colours))
    kwargs = {"sources": np.concatenate(sources)}
    if use_origins:
        kwargs["sensor_origins"] = origins
    return occupancy_from_points(
        cloud, resolution=resolution,
        bounds={"min": low.tolist(), "max": high.tolist()}, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", type=pathlib.Path)
    parser.add_argument("--reference", type=pathlib.Path, default=None,
                        help="a recorded scan whose occupancy grid grades the replay")
    parser.add_argument("--resolution", type=float, default=0.05)
    parser.add_argument("--compare-origins", action="store_true",
                        help="run both the point-splat and the ray-fill mapping on this log")
    args = parser.parse_args()

    reader = SensorLogReader(args.log)
    manifest = reader.verify()
    print(f"日志 {args.log}")
    print(f"  帧数 {len(reader)}  机器人 {reader.robot_id}  标定 {reader.calibration_revision[:12]}")
    print(f"  来源 {reader.manifest.get('source')}  摘要 {manifest['logDigest'][:16]}")

    frames = list(reader)
    truth = None
    if args.reference is not None:
        meta = json.loads((args.reference / "grid" / "occupancy.json").read_text(encoding="utf-8"))
        truth = np.load(args.reference / "grid" / "occupancy.npy")
        print(f"  参考地图 {truth.shape} 未知 {float((truth < 0).mean()):.1%} "
              f"分辨率 {meta['resolution']}")

    variants = [("points-only", False)]
    if args.compare_origins:
        variants.append(("ray-filled", True))

    print()
    for label, use_origins in variants:
        grid = build_grid(frames, resolution=args.resolution, use_origins=use_origins)
        if grid is None:
            print(f"{label:12s} 没有任何有效点")
            return 2
        cells = grid["cells"]
        known = int((cells >= 0).sum())
        occupied = int((cells >= 65).sum())
        line = (f"{label:12s} 已知 {known:6d} 格  占据 {occupied:5d}  "
                f"未知 {float((cells < 0).mean()):5.1%}")
        if truth is not None:
            # Grade only where the replay's frame can reach the reference: the two
            # grids are built from different clouds and need not share an extent.
            shared = (slice(0, min(cells.shape[0], truth.shape[0])),
                      slice(0, min(cells.shape[1], truth.shape[1])))
            reference = truth[shared]
            mine = cells[shared]
            floor = reference == 0
            if floor.any():
                line += f"  参考地面命中 {float((mine[floor] >= 0).mean()):5.1%}"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
