#!/usr/bin/env python3
"""Measure how well the RGB-D SLAM holds a trajectory, with and without odometry drift.

The simulator is the one place where the robot's true pose is known exactly, so
it is the one place where a SLAM estimate can be scored instead of eyeballed.
Two numbers come out of a run:

* **damage** - with exact odometry, how far the optimized poses move away from
  the truth. A SLAM that cannot beat odometry must at least not corrupt it, and
  this is what a map built in simulation silently suffers from.
* **recovery** - with a deliberate scale and yaw error injected into the
  odometry channel, how much of that error the depth registration removes. This
  is the property that matters on hardware, and it cannot be measured by looking
  at a map.

Both are reported against the *driver's* pose, which in simulation is the
model's own state rather than an encoder estimate.

Usage::

    .venv/bin/python scripts/evaluate_slam.py --route house --output artifacts/slam-eval/run-1
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "robot" / "gateway"))
sys.path.insert(0, str(ROOT / "sim" / "mujoco"))

#: The routes are driven in the commissioned home, which is the scene the
#: mapping service is actually used in.
ROUTES = {
    # A loop through every public room, ending where it started, so a pose graph
    # has something to close against.
    "house": [
        (0.0, -1.25), (0.0, 1.85), (0.0, 3.35), (2.0, 3.35), (3.2, 3.35),
        (3.2, 2.0), (2.0, 2.0), (0.0, 2.0), (0.0, 1.85), (0.0, 0.0),
        (0.0, -1.25),
    ],
    "room": [(0.0, -1.25), (0.0, 0.4), (2.0, 0.4), (3.2, 0.4), (3.2, -1.0),
             (1.5, -1.0), (0.0, -1.25)],
}


def _place_base(world, x, y, yaw):
    """Teleport the commissioned base; the harness drives the model directly.

    Routing the evaluation through the safety controller would make the score
    depend on clearance decisions instead of on the SLAM, and the odometry this
    scores against is the model's own state either way.
    """
    import mujoco

    world.data.qpos[world.model.jnt_qposadr[world.model.joint("slide_joint_x").id]] = y
    world.data.qpos[world.model.jnt_qposadr[world.model.joint("slide_joint_y").id]] = -x
    world.data.qpos[world.model.jnt_qposadr[world.model.joint("hinge_joint_z").id]] = yaw - math.pi / 2
    mujoco.mj_forward(world.model, world.data)
    world._publish_sensor_snapshot()


def _drive(world, service, x, y, speed=0.30):
    """Turn toward a point, then advance; the caller captures between steps.

    Turning before advancing keeps the camera pointed where the robot is going,
    which is what the depth stream a real survey produces looks like. A base that
    translates while still facing a wall it just left measures almost nothing
    there, and the evaluation would then be scoring the harness.
    """
    while True:
        pose = np.array(world.robot_state()["base_pose"], dtype=float)
        dx, dy = x - pose[0], y - pose[1]
        distance = math.hypot(dx, dy)
        if distance <= 0.05:
            return
        heading = math.atan2(dy, dx)
        yaw = 2 * math.atan2(pose[6], pose[3])
        error = math.atan2(math.sin(heading - yaw), math.cos(heading - yaw))
        if abs(error) > 0.08:
            yaw += max(-.5, min(.5, error))
            _place_base(world, pose[0], pose[1], yaw)
        else:
            step = min(speed, distance)
            _place_base(world, pose[0] + step * math.cos(yaw), pose[1] + step * math.sin(yaw), yaw)
        yield


def _observation_with_drift(observation, drift):
    """Re-stamp an observation's odometry with a scale and yaw error.

    The rendered frame and the true pose are untouched: only the channel that a
    real base reports through is corrupted, which is exactly what a SLAM has to
    be robust to and exactly what a simulator otherwise never tests.
    """
    if drift is None:
        return observation
    pose = np.array(list(observation.robot_state["base_pose"]), dtype=float)
    yaw = (2 * math.atan2(pose[6], pose[3])) * drift["yaw_scale"] + drift["yaw_bias"]
    pose[0] *= drift["scale"]
    pose[1] *= drift["scale"]
    pose[3], pose[6] = math.cos(yaw / 2), math.sin(yaw / 2)
    # It is a protobuf map field, not a dict: mutate it in place.
    observation.robot_state["base_pose"] = pose.tolist()
    return observation


def _capture(service, drift, truth):
    observation = service._sensor_observation(service._robot_id + "/base-rgbd")
    true_pose = np.array(observation.robot_state["base_pose"], dtype=float)
    truth.append(true_pose.copy())
    return _observation_with_drift(observation, drift), true_pose


def run(route, *, route_name="route", drift=None, output=None, spacing_m=0.35, seed=7):
    from tangying_robot_gateway.dense_slam import DenseSLAM
    from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld

    world = RgbdTabletopWorld.seeded(seed, scene="home_task")
    service = RgbdRuntimeService(world, robot_id="slam-eval")
    service.navigation.sleep_scale = 0.0
    slam = DenseSLAM()
    truth: list[np.ndarray] = []
    accepted: list[int] = []
    started = time.monotonic()
    try:
        observation, _ = _capture(service, drift, truth)
        slam.add(observation)
        accepted.append(0)
        for index in range(len(route) - 1):
            for _ in _drive(world, service, *route[index + 1]):
                observation, _ = _capture(service, drift, truth)
                before = len(slam.frames)
                slam.add(observation)
                if len(slam.frames) > before:
                    accepted.append(len(truth) - 1)
    finally:
        service.close()
    if len(slam.frames) < 3:
        raise RuntimeError("the route produced too few keyframes to score")

    # Keyframe poses are already planar (x, y, yaw); re-deriving them from a
    # quaternion here would silently score a trajectory whose heading is zero.
    estimated = np.array([f.pose for f in slam.frames])
    true_poses = np.array([truth[i] for i in accepted])
    true_se2 = np.array([[p[0], p[1], 2 * math.atan2(p[6], p[3])] for p in true_poses])
    # Score in the first keyframe's frame so the two trajectories are comparable
    # even when the drift model moved the odometry's origin.
    def to_local(poses):
        origin = poses[0]
        c, s = math.cos(-origin[2]), math.sin(-origin[2])
        local = poses.copy()
        local[:, 0] = c * (poses[:, 0] - origin[0]) - s * (poses[:, 1] - origin[1])
        local[:, 1] = s * (poses[:, 0] - origin[0]) + c * (poses[:, 1] - origin[1])
        local[:, 2] = np.arctan2(np.sin(poses[:, 2] - origin[2]), np.cos(poses[:, 2] - origin[2]))
        return local

    odometry = np.array([f.odometry for f in slam.frames])
    estimate_local, truth_local = to_local(estimated), to_local(true_se2)
    odometry_local = to_local(odometry)
    metrics = {
        "route": route_name,
        "keyframes": len(slam.frames),
        "loopClosures": len(slam.loops),
        "registrations": len(slam.registrations),
        "registrationAttempts": len(slam.registration_attempts),
        "drift": drift,
        "seconds": round(time.monotonic() - started, 1),
        # Displacement error: the whole-trajectory error, the number a floor plan
        # actually cares about.
        "ateEstimateM": round(float(np.mean(np.linalg.norm(estimate_local[:, :2] - truth_local[:, :2], axis=1))), 4),
        "ateOdometryM": round(float(np.mean(np.linalg.norm(odometry_local[:, :2] - truth_local[:, :2], axis=1))), 4),
        "maxEstimateM": round(float(np.max(np.linalg.norm(estimate_local[:, :2] - truth_local[:, :2], axis=1))), 4),
        "maxOdometryM": round(float(np.max(np.linalg.norm(odometry_local[:, :2] - truth_local[:, :2], axis=1))), 4),
        # Heading error is what turns walls into diagonals.
        "ateYawRad": round(float(np.mean(np.abs(np.arctan2(
            np.sin(estimate_local[:, 2] - truth_local[:, 2]),
            np.cos(estimate_local[:, 2] - truth_local[:, 2]))))), 4),
        "ateOdometryYawRad": round(float(np.mean(np.abs(np.arctan2(
            np.sin(odometry_local[:, 2] - truth_local[:, 2]),
            np.cos(odometry_local[:, 2] - truth_local[:, 2]))))), 4),
    }
    if output is not None:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
        (output / "trajectory.json").write_text(json.dumps({
            "estimated": estimate_local.tolist(), "odometry": odometry_local.tolist(),
            "truth": truth_local.tolist()}, indent=2) + "\n")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--route", default="house", choices=sorted(ROUTES))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--drift", action="store_true",
                        help="inject a 4%% range scale and a slow yaw bias into odometry")
    parser.add_argument("--spacing", type=float, default=0.35)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    drift = {"scale": 1.04, "yaw_scale": 1.0, "yaw_bias": 0.0} if args.drift else None
    result = run(ROUTES[args.route], route_name=args.route, drift=drift, output=args.output,
                 spacing_m=args.spacing, seed=args.seed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
