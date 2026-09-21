"""Gazebo navigation skills through the existing safety, journal and GVF service."""

from __future__ import annotations

import math
import threading
import time

import numpy as np

from .backend import RobotBackend, capability
from .gazebo_runtime import leveled_base_pose
from .grounded.model import canonical
from .runtime import Observation, Result, RuntimeInfo


class GazeboSkillBackend(RobotBackend):
    def __init__(self, node):
        self.node = node
        self.cancel_event = threading.Event()
        node.enable_bounded_motion()

    def capabilities(self):
        names = ["navigation.navigate", "verify_arrival", "observe_scene", "emergency_stop"]
        from .contracts import RobotProfile

        runtime = self.node.runtime
        profile = RobotProfile.model_validate(
            {
                "schema_version": "robot.profile.v1",
                "robot_id": runtime.robot_id,
                "adapter_id": "gazebo",
                "adapter_version": "grounded-1.0.0",
                "model_id": "tangying_home",
                "embodiment": "mobile_base",
                "joints": [],
                "end_effectors": [],
                "sensors": [
                    {
                        "source_id": runtime.robot_id + "/base-rgbd",
                        "source_type": "rgbd_camera",
                        "frame_id": "base-rgbd_optical",
                        "transform_revision": runtime.calibration_revision,
                        "max_age_ms": 1000,
                    }
                ],
                "action_limits": {
                    "navigation.x": {"min": -6.0, "max": 6.0, "unit": "m"},
                    "navigation.y": {"min": -3.0, "max": 7.0, "unit": "m"},
                    "navigation.z": {"min": 0.0, "max": 1.0, "unit": "m"},
                },
                "tools": names,
            }
        )
        return RuntimeInfo(
            robot_id=self.node.runtime.robot_id,
            adapter="gazebo",
            manipulation_ready=True,
            blockers=[],
            adapter_version="grounded-1.0.0",
            protocol_version="1.0",
            software_version="0.7.0",
            robot_profile=profile.to_wire(),
            capabilities=[
                capability(
                    name,
                    "Gazebo 有界导航与独立取证",
                    available=True,
                    safety_level="physical_motion"
                    if name in {"navigation.navigate", "emergency_stop"}
                    else "read_only",
                    cancellable=True,
                    mutates_world=name in {"navigation.navigate", "emergency_stop"},
                )
                for name in names
            ],
        )

    def observe(self, request):
        from .contracts import Reconstruction

        sample = self.node.runtime._samples.get("base-rgbd")
        if sample is None:
            raise ValueError("NO_CAPTURE")
        identity = f"gz-{sample.sensor_stamp_ns}"
        reconstruction = Reconstruction(
            schema_version="scene.reconstruction.v1",
            robot_id=self.node.runtime.robot_id,
            observation_id=identity,
            source_id=self.node.runtime.robot_id + "/base-rgbd",
            source_type="rgbd_camera",
            source_frame_id="base-rgbd_optical",
            frame_id="world",
            transform_revision=self.node.runtime.calibration_revision,
            observed_at_unix_ms=sample.captured_at_unix_ms,
            sequence=max(1, sample.sensor_stamp_ns),
            units="m",
        )
        return Observation(
            observation_id=identity,
            wall_time_unix_ms=sample.captured_at_unix_ms,
            monotonic_time_ns=time.monotonic_ns(),
            robot_state={"base_pose": leveled_base_pose(self.node.runtime.base_pose)},
            reconstruction=reconstruction.to_wire(),
        )

    def execute(self, command):
        if command.capability in {"verify_arrival", "observe_scene"}:
            return Result(True)
        if command.capability != "navigation.navigate":
            return Result(False, "CAPABILITY_UNAVAILABLE")
        goal = command.parameters.get("goalPose")
        if not isinstance(goal, list) or len(goal) != 7:
            return Result(False, "TOOL_PARAMETERS_INVALID", "goalPose 必须为七维位姿")
        pose = np.asarray(goal, dtype=float)
        current = leveled_base_pose(self.node.runtime.base_pose)
        if (
            not np.isfinite(pose).all()
            or not current
            or abs(np.linalg.norm(pose[3:]) - 1) > 1e-3
            or np.linalg.norm(pose[:2] - np.asarray(current[:2])) > 0.75
        ):
            return Result(
                False, "NAV_WORKSPACE_LIMIT", "单次导航限制为 0.75 米，长路线需要任务分解"
            )
        self.cancel_event.clear()
        outcome = self.node.bounded_step(goal, self.cancel_event)
        return Result(bool(outcome["ok"]), outcome.get("code", ""), outcome.get("message", ""))

    def stop(self, reason):
        self.cancel_event.set()
        self.node._publish_velocity(0.0, 0.0)

    def collect_grounded_evidence(
        self, *, command, action_id, start_ns, edge_boot_id, store, phase
    ):
        goal = command.parameters.get("goalPose")
        if not isinstance(goal, list) or len(goal) != 7:
            return []
        frames, seen = [], set()
        deadline = time.monotonic() + 0.48
        while time.monotonic() < deadline and len(frames) < 3:
            with self.node._lock:
                sample = self.node.runtime._samples.get("base-rgbd")
                pose = (
                    leveled_base_pose(sample.base_pose_at_capture) if sample is not None else None
                )
            if (
                sample is None
                or not pose
                or sample.sensor_stamp_ns in seen
                or sample.received_monotonic_ns <= start_ns
                or sample.odometry_stamp_ns <= 0
                or abs(sample.sensor_stamp_ns - sample.odometry_stamp_ns) > 200_000_000
            ):
                time.sleep(0.015)
                continue
            seen.add(sample.sensor_stamp_ns)
            yaw = 2 * math.atan2(pose[6], pose[3])
            target_yaw = 2 * math.atan2(goal[6], goal[3])
            values = {
                "position_error_m": float(
                    np.linalg.norm(np.asarray(pose[:2]) - np.asarray(goal[:2]))
                ),
                "yaw_error_rad": abs(
                    math.atan2(math.sin(yaw - target_yaw), math.cos(yaw - target_yaw))
                ),
            }
            refs = [
                store.put(
                    sample.rgb.tobytes(), "rgb", {"width": sample.width, "height": sample.height}
                ),
                store.put(np.asarray(sample.depth_metres, dtype="<f4").tobytes(), "depth"),
                store.put(
                    canonical(
                        {
                            "pose": pose,
                            "goal": goal,
                            "sensor_stamp_ns": sample.sensor_stamp_ns,
                            "odometry_stamp_ns": sample.odometry_stamp_ns,
                        }
                    ).encode(),
                    "pose",
                ),
            ]
            frames.append(
                store.record_sample(
                    sample_id=str(sample.sensor_stamp_ns),
                    source_id="gazebo/odometry",
                    edge_boot_id=edge_boot_id,
                    edge_monotonic_ts_ns=sample.received_monotonic_ns,
                    action_id=action_id,
                    confidence=0.95,
                    values=values,
                    evidence_refs=refs,
                )
            )
        return frames


def grounded_service_requires_contract(name: str, mutates_world: bool) -> bool:
    """The service RPC must not bypass the skill evidence boundary."""
    return mutates_world and name not in {"mapping.stop_motion", "mapping.cancel", "mapping.finish"}
