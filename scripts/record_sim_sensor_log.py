"""Record a replayable sensor log by driving the simulator along a real trajectory.

The point of this script is to produce the thing every later comparison needs and
that the repository did not have: the *input* to the mapping stack, at full
resolution, with the poses it was taken from.

It uses a trajectory a real survey actually drove, so the resulting log is not a
toy route around a toy room - it is the path that produced the map under
discussion, re-rendered through the same camera model the runtime uses.

    python scripts/record_sim_sensor_log.py --scan artifacts/maps/furnished-home/scan-6974b8f937e9 \\
        --output artifacts/replay/furnished-home

Then compare anything offline::

    python scripts/replay_sensor_log.py artifacts/replay/furnished-home
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot" / "gateway"))
sys.path.insert(0, str(ROOT / "sim" / "mujoco"))

from tangying_robot_gateway.rgbd import RgbdFrame
from tangying_robot_gateway.sensor_log import SensorLogWriter


def trajectory_points(scan: pathlib.Path, spacing_m: float) -> list[tuple[float, float]]:
    """The recorded path, resampled to a fixed spacing along its length.

    Resampled rather than taken as recorded: the recorded points are keyframe
    positions, so their spacing varies with how fast the robot happened to be
    going, and a log whose sampling depends on speed cannot be compared with one
    recorded at a different speed.
    """
    document = json.loads((scan / "trajectory.geojson").read_text(encoding="utf-8"))
    features = document["features"] if isinstance(document, dict) else document
    points: list[list[float]] = []
    for feature in features:
        geometry = feature.get("geometry", {})
        if geometry.get("type") == "LineString":
            points.extend([list(c[:2]) for c in geometry["coordinates"]])
        elif geometry.get("type") == "Point":
            points.append(list(geometry["coordinates"][:2]))
    if len(points) < 2:
        raise SystemExit(f"{scan}/trajectory.geojson holds fewer than two points")
    path = np.asarray(points, dtype=float)
    steps = np.linalg.norm(np.diff(path, axis=0), axis=1)
    travelled = np.concatenate([[0.0], np.cumsum(steps)])
    count = max(2, int(travelled[-1] / spacing_m) + 1)
    sample = np.linspace(0.0, travelled[-1], count)
    resampled = np.stack([np.interp(sample, travelled, path[:, 0]),
                          np.interp(sample, travelled, path[:, 1])], axis=1)
    return [tuple(float(v) for v in point) for point in resampled]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scan", type=pathlib.Path,
                        default=pathlib.Path("artifacts/maps/furnished-home/scan-6974b8f937e9"),
                        help="a recorded scan whose trajectory and calibration are reused")
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("artifacts/replay/furnished-home"))
    parser.add_argument("--calibration", type=pathlib.Path,
                        default=pathlib.Path("artifacts/calibration/furnished-home"))
    parser.add_argument("--spacing-m", type=float, default=0.10)
    parser.add_argument("--max-frames", type=int, default=0, help="0 means the whole route")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    args = parser.parse_args()

    if not (args.scan / "trajectory.geojson").is_file():
        print(f"没有找到轨迹：{args.scan}", file=sys.stderr)
        return 2
    if not (args.calibration / "calibration.json").is_file():
        print(f"没有找到标定：{args.calibration}", file=sys.stderr)
        return 2

    os.environ.setdefault("TANGYING_HOME_ASSET_PACK",
                          str(ROOT / "artifacts" / "sim-assets" / "furnished-home"))
    import mujoco
    from tangying_sim.calibration import SimulationCalibration
    from tangying_sim.rendering import SceneRenderer
    from tangying_sim.rgbd_navigation import load_navigation_model

    session = json.loads((args.scan / "slam-session.json").read_text(encoding="utf-8"))
    robot_id = str(session.get("robotId") or "xlerobot-mujoco-tabletop")
    points = trajectory_points(args.scan, args.spacing_m)
    if args.max_frames:
        points = points[:args.max_frames]

    model = load_navigation_model(scene="home_task")
    data = mujoco.MjData(model)
    calibration = SimulationCalibration(model=model, root=args.calibration, robot_id=robot_id)
    revision = calibration.revision
    renderer = SceneRenderer(camera="base_depth", width=args.width, height=args.height,
                             calibration=calibration, calibration_camera="base-rgbd")
    addresses = [int(model.jnt_qposadr[model.joint(name).id])
                 for name in ("slide_joint_x", "slide_joint_y", "hinge_joint_z")]
    chassis = model.body("chassis").id

    writer = SensorLogWriter(args.output, robot_id=robot_id, calibration_revision=revision,
                             source="mujoco-home_task",
                             metadata={"scan": str(args.scan), "scene": "home_task",
                                       "spacingM": args.spacing_m,
                                       "renderer": f"{args.width}x{args.height}"})
    started = time.perf_counter()
    previous = None
    for index, (x, y) in enumerate(points):
        heading = (0.0 if previous is None
                   else math.atan2(y - previous[1], x - previous[0]))
        previous = (x, y)
        data.qpos[addresses[0]] = x
        data.qpos[addresses[1]] = y
        data.qpos[addresses[2]] = heading
        mujoco.mj_forward(model, data)
        pixels = renderer.render_rgbd(model, data)
        pose = [float(v) for v in data.xpos[chassis]] + [float(v) for v in data.xquat[chassis]]
        writer.append(
            RgbdFrame(
                robot_id=robot_id, source_id=f"{robot_id}/base-rgbd",
                frame_id="base_depth_optical", transform_revision=revision,
                # The capture time is the sample index, not the wall clock: a log
                # that recorded when it was written would replay differently every
                # time it was read.
                captured_at_unix_ms=1_700_000_000_000 + index * 100,
                sequence=index + 1,
                rgb=np.ascontiguousarray(pixels.rgb, dtype=np.uint8),
                depth_m=np.ascontiguousarray(pixels.depth_m, dtype=np.float64),
                intrinsics=np.asarray(pixels.intrinsics, dtype=np.float64),
                world_from_camera=np.asarray(pixels.world_from_camera, dtype=np.float64),
            ),
            joints={"sample_index": float(index)},
            base_pose=pose,
        )
        if (index + 1) % 25 == 0:
            print(f"  {index + 1}/{len(points)} 帧  {time.perf_counter() - started:.0f}s")
    renderer.close()
    manifest = writer.close()
    print(f"\n日志已写入 {args.output}")
    print(f"  帧数 {manifest['frames']}  标定 {revision[:12]}  机器人 {robot_id}")
    print(f"  RGB {manifest['bytes']['rgb'] / 1e6:.1f} MB  深度 {manifest['bytes']['depth'] / 1e6:.1f} MB")
    print(f"  日志摘要 {manifest['logDigest'][:16]}  用时 {time.perf_counter() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
