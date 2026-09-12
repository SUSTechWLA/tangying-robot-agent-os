#!/usr/bin/env python3
"""Build a map in the simulation by driving the robot and looking out of its camera.

    scripts/build_sim_map.py --output artifacts/maps/sim-home --map-id sim-home

Deliberately not a synthetic cloud. The robot starts in the living room and drives a
route through the house, and every point in the resulting map comes from a depth frame
taken at a pose the robot actually reached. That is the difference between a map and a
picture of one: a synthetic cloud can look right while proving nothing about whether
the robot can survey its own home.

The route follows the commissioned room graph, and frames are captured along the way
rather than only at waypoints, because a map built from five viewpoints has five holes
in it. Poses come from the same source the runtime publishes, so the geometry and the
map agree on where the robot was.
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path

import mujoco
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim/mujoco"))
sys.path.insert(0, str(ROOT / "robot/gateway"))

from tangying_robot_gateway.map_pipeline import build_map, verify_map
from tangying_sim.home_scene import HOME_WAYPOINTS, route_between
from tangying_sim.rgbd_navigation import NavigationController
from tangying_sim.rgbd_runtime import RgbdTabletopWorld

#: Depth beyond this is not a measurement. Indoor RGB-D returns garbage or infinity
#: where it saw nothing, and back-projecting that scatters points across the house.
MAX_DEPTH_M = 5.0
MIN_DEPTH_M = 0.15


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--map-id", required=True)
    parser.add_argument("--robot-id", default="xlerobot-mujoco-tabletop")
    parser.add_argument("--scene", default="home_task")
    parser.add_argument("--seed", type=int, default=7)
    # Interpolation between waypoints: one frame per this many metres of travel.
    parser.add_argument("--frame-spacing-m", type=float, default=0.35)
    parser.add_argument("--voxel-m", type=float, default=0.04)
    parser.add_argument("--lod-levels", type=int, default=5)
    return parser.parse_args()


def back_project(frame) -> np.ndarray:
    """One depth frame in world coordinates, using the capture's own calibration."""
    depth = np.asarray(frame.depth_m, dtype=np.float64)
    height, width = depth.shape
    k = np.asarray(frame.intrinsics, dtype=np.float64)
    rows, columns = np.mgrid[0:height, 0:width]
    z = depth
    x = (columns - k[0, 2]) * z / k[0, 0]
    y = (rows - k[1, 2]) * z / k[1, 1]
    points = np.stack([x, y, z], axis=-1).reshape(-1, 3)
    valid = np.isfinite(points).all(axis=1) & (points[:, 2] > MIN_DEPTH_M) & (points[:, 2] < MAX_DEPTH_M)
    points = points[valid]
    if points.size == 0:
        return np.zeros((0, 3), dtype=np.float32)
    transform = np.asarray(frame.world_from_camera, dtype=np.float64)
    return (points @ transform[:3, :3].T + transform[:3, 3]).astype(np.float32)


def interpolate(start: list[float], goal: list[float], spacing_m: float) -> list[list[float]]:
    """Points along the straight leg between two waypoints, ends included."""
    start_xy = np.array(start[:2], dtype=np.float64)
    goal_xy = np.array(goal[:2], dtype=np.float64)
    distance = float(np.linalg.norm(goal_xy - start_xy))
    steps = max(int(distance / max(spacing_m, 0.05)), 1)
    poses = []
    for step in range(steps + 1):
        fraction = step / steps
        pose = list(start)
        pose[0] = float(start_xy[0] + (goal_xy[0] - start_xy[0]) * fraction)
        pose[1] = float(start_xy[1] + (goal_xy[1] - start_xy[1]) * fraction)
        # Heading follows the leg so the cameras look where the robot is going.
        if distance > 1e-6:
            heading = float(np.arctan2(goal_xy[1] - start_xy[1], goal_xy[0] - start_xy[0]))
            pose[3] = float(np.cos(heading / 2))
            pose[6] = float(np.sin(heading / 2))
        poses.append(pose)
    return poses


def survey_route(rooms: list[str], spacing_m: float) -> list[list[float]]:
    poses: list[list[float]] = []
    for start, goal in itertools.pairwise(rooms):
        leg = interpolate(HOME_WAYPOINTS[start], HOME_WAYPOINTS[goal], spacing_m)
        poses.extend(leg if not poses else leg[1:])
    return poses


def main() -> int:
    args = parse_args()
    world = RgbdTabletopWorld.seeded(args.seed, scene=args.scene)
    navigation = NavigationController(world, args.robot_id)
    # The route a survey would take: out through the corridor and back again, so the
    # return leg looks at the walls from the other side.
    rooms = ["living_room", "home_corridor", "kitchen", "home_corridor", "bedroom",
             "bathroom", "bedroom", "home_corridor", "living_room"]
    for start, goal in itertools.pairwise(rooms):
        route_between(start, goal)  # refuses a route the graph does not allow
    poses = survey_route(rooms, args.frame_spacing_m)
    print(f"survey: {len(rooms)} rooms, {len(poses)} poses, {args.scene} scene")

    frames: list[np.ndarray] = []
    started = time.perf_counter()
    try:
        # A cold graphics context is not a measurement; discard the first render the
        # way the acceptance runs do.
        navigation.renderer.render_rgbd(world.model, world.sensor_snapshot[0])
        trajectory: list[tuple[float, float, float]] = []
        # The chassis rides on joints, not a free body. The runtime's own convention
        # maps world x to slide_joint_y and world y to slide_joint_x; following it here
        # keeps the survey's poses and the runtime's poses the same thing.
        addresses = {
            name: world.model.jnt_qposadr[world.model.joint(name).id]
            for name in ("slide_joint_x", "slide_joint_y", "hinge_joint_z")
        }
        for index, pose in enumerate(poses, start=1):
            world.data.qpos[addresses["slide_joint_x"]] = pose[1]
            world.data.qpos[addresses["slide_joint_y"]] = pose[0]
            world.data.qpos[addresses["hinge_joint_z"]] = float(
                np.arctan2(2.0 * (pose[3] * pose[6]), 1.0 - 2.0 * pose[6] * pose[6]))
            mujoco.mj_forward(world.model, world.data)
            world._publish_sensor_snapshot()
            frame, base_pose = navigation.capture()
            points = back_project(frame)
            if points.size:
                frames.append(points)
                trajectory.append((float(base_pose[0]), float(base_pose[1]), float(base_pose[2])))
            if index % 20 == 0 or index == len(poses):
                print(f"  {index}/{len(poses)} poses, {sum(f.shape[0] for f in frames):,} points")
    finally:
        navigation.close()

    if not frames:
        print("no depth frame produced a point; nothing to build", file=sys.stderr)
        return 1

    xyz = np.concatenate(frames)
    print(f"surveyed {xyz.shape[0]:,} points from {len(frames)} live depth frames "
          f"in {time.perf_counter() - started:.1f}s")

    from tangying_robot_gateway.map_pipeline import PointCloud

    manifest = build_map(
        args.output, map_id=args.map_id, robot_id=args.robot_id,
        cloud=PointCloud(xyz=xyz), poses=trajectory,
        lod_levels=args.lod_levels, base_voxel_m=args.voxel_m,
    )
    print(f"\n地图 {manifest['mapId']} 已写入 {args.output}")
    print(f"  点数 {manifest['pointCount']:,}，LOD {manifest['lodLevels']} 层")
    for role, entry in manifest["artifacts"].items():
        print(f"  {role:<11} {entry['bytes'] / 1e6:>8.2f} MB  {entry['href']}")
    checks = verify_map(args.output)
    failed = [check for check in checks if not check.ok]
    print("校验：" + ("全部通过" if not failed else
                      "；".join(f"{check.role} {check.reason}" for check in failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
