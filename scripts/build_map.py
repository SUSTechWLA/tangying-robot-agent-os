#!/usr/bin/env python3
"""Build a map directory from a real RTAB-Map database, or from synthetic data.

    scripts/build_map.py --database /path/rtabmap.db --output artifacts/maps/home \\
        --map-id home --robot-id xlerobot-01 --calibration artifacts/calibration/xlerobot-01.json

    scripts/build_map.py --synthetic 1000000 --output artifacts/maps/loadtest \\
        --map-id loadtest --robot-id loadtest

The synthetic mode exists because the performance numbers in the plan can only be
measured on a map built for the purpose; waiting for a real survey to happen to be
the right size is not a test strategy.

The database mode refuses a database that is not one clean survey, and says why.
That is not caution for its own sake: the sample in the navigation volume holds two
maps, fifty hours of sessions and 1235 deleted nodes out of 1237, and exporting it
would merge unrelated runs into one plausible-looking map.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "robot/gateway"))

from tangying_robot_gateway.calibration import (
    CalibrationError,
    calibration_revision,
    load_calibration,
)
from tangying_robot_gateway.map_pipeline import (
    build_map,
    synthetic_home_cloud,
    verify_map,
)
from tangying_robot_gateway.rtabmap_export import (
    CameraIntrinsics,
    RtabmapExportError,
    database_summary,
    open_database,
    read_trajectory,
    reconstruct_cloud,
)

DEFAULT_CAMERA = "head-rgbd"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", type=Path, help="an RTAB-Map database")
    source.add_argument("--synthetic", type=int, metavar="POINTS",
                        help="generate a synthetic cloud of this many points")

    parser.add_argument("--output", type=Path, required=True, help="map directory to write")
    parser.add_argument("--map-id", required=True)
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--calibration", type=Path,
                        help="robot.calibration.v1 document, for the camera intrinsics")
    parser.add_argument("--camera", default=DEFAULT_CAMERA, help="which camera the depth came from")
    parser.add_argument("--lod-levels", type=int, default=5)
    parser.add_argument("--base-voxel-m", type=float, default=0.04)
    parser.add_argument("--stride", type=int, default=2, help="depth subsampling when reconstructing")
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--allow-unhealthy", action="store_true",
                        help="build even when the database is not one clean survey")
    return parser.parse_args()


def intrinsics_from(calibration_path: Path | None, camera: str) -> CameraIntrinsics:
    if calibration_path is None:
        raise RtabmapExportError(
            "building from a database needs --calibration: the intrinsics come from the "
            "robot's own calibration document, not from RTAB-Map's internal serialisation"
        )
    try:
        document = load_calibration(calibration_path)
    except CalibrationError as error:
        raise RtabmapExportError(f"{calibration_path}: {error.message}") from None
    cameras = document.get("cameras") or {}
    if camera not in cameras:
        raise RtabmapExportError(
            f"{calibration_path} has no {camera} camera; it has {', '.join(sorted(cameras)) or 'none'}"
        )
    return CameraIntrinsics.from_calibration(cameras[camera])


def main() -> int:
    args = parse_args()
    calib_revision = None

    try:
        if args.database:
            connection = open_database(args.database)
            summary = database_summary(connection)
            print(f"数据库: {summary['nodes']} 个节点，{summary['depthFrames']} 帧深度，"
                  f"版本 {summary['databaseVersion']}")
            if summary["health"]:
                for reason in summary["health"]:
                    print(f"  ⚠ {reason}", file=sys.stderr)
                if not args.allow_unhealthy:
                    print("\n拒绝导出：这不是一次干净的建图，导出的地图会合并无关的会话。"
                          "\n如果确实要导出，加 --allow-unhealthy。", file=sys.stderr)
                    return 2
            if summary["hasOptimizedCloud"]:
                print("注意：数据库里有拼装好的点云，本工具仍然用深度帧重建。", file=sys.stderr)
            intrinsics = intrinsics_from(args.calibration, args.camera)
            cloud = reconstruct_cloud(connection, intrinsics=intrinsics, stride=args.stride,
                                      max_frames=args.max_frames)
            poses = read_trajectory(connection)
            print(f"重建点云: {cloud.count:,} 点，有颜色={cloud.rgb is not None}，{len(poses)} 个位姿")
            if args.calibration:
                # Record which calibration produced this map, so a map and the task
                # evidence drawn on it can be checked against each other.
                calib_revision = calibration_revision(load_calibration(args.calibration))
        else:
            cloud = synthetic_home_cloud(args.synthetic)
            poses = [(-2.0 + index * 0.2, -1.0 + (index % 7) * 0.4, 0.0)
                     for index in range(60)]
            print(f"合成点云: {cloud.count:,} 点（用于压测；不是真实扫描）")

        manifest = build_map(
            args.output, map_id=args.map_id, robot_id=args.robot_id, cloud=cloud,
            poses=poses, lod_levels=args.lod_levels, base_voxel_m=args.base_voxel_m,
            calibration_revision=calib_revision,
        )
    except (RtabmapExportError, OSError, ValueError) as error:
        print(f"导出失败：{error}", file=sys.stderr)
        return 1

    print(f"\n地图 {manifest['mapId']} 已写入 {args.output}")
    print(f"  点数 {manifest['pointCount']:,}，LOD {manifest['lodLevels']} 层，"
          f"坐标系 {manifest['frameId']}")
    for role, entry in manifest["artifacts"].items():
        print(f"  {role:<11} {entry['bytes'] / 1e6:>8.2f} MB  {entry['href']}")
    checks = verify_map(args.output)
    failed = [check for check in checks if not check.ok]
    print("校验：" + ("全部通过" if not failed else
                      "；".join(f"{check.role} {check.reason}" for check in failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
