"""Gazebo navigation skills through the existing safety, journal and GVF service."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import replace

import numpy as np

from .backend import RobotBackend, capability
from .gazebo_runtime import leveled_base_pose
from .grounded.model import canonical
from .rgbd import RgbdPerception
from .rgbd_images import encode_depth_preview, encode_rgb_png
from .runtime import Observation, Result, RuntimeInfo


class GazeboSkillBackend(RobotBackend):
    def __init__(self, node):
        self.node = node
        self.cancel_event = threading.Event()
        node.enable_bounded_motion()
        self.perception = RgbdPerception(lambda frame: [])

    def capabilities(self):
        names = ["navigation.navigate", "verify_arrival", "observe_scene", "arm.move", "recover_to_safe_pose", "emergency_stop"]
        from .arm_kinematics import all_links
        from .contracts import RobotProfile

        runtime = self.node.runtime
        profile = RobotProfile.model_validate(
            {
                "schema_version": "robot.profile.v1",
                "robot_id": runtime.robot_id,
                "adapter_id": "gazebo",
                "adapter_version": "gazebo-0.7.0",
                "model_id": "tangying_" + getattr(self.node, "scene", "home"),
                "embodiment": "mobile_manipulator",
                "joints": [{"name": link.motor, "kind": "revolute", "unit": "rad",
                            "lower": link.range_min, "upper": link.range_max} for link in all_links()],
                "end_effectors": [{"id": side+"_gripper", "kind": "gripper", "joint_names": [side+"_arm_gripper"]} for side in ("left", "right")],
                "sensors": [
                    {
                        "source_id": runtime.robot_id + "/" + camera,
                        "source_type": "rgbd_camera",
                        "frame_id": camera + "_optical",
                        "transform_revision": runtime.calibration_revision,
                        "max_age_ms": 1000,
                    }
                    for camera in getattr(runtime, "cameras", {"base-rgbd": ""})
                ],
                "action_limits": {
                    **{link.motor+".pos": {"min": link.range_min, "max": link.range_max, "unit": "rad"} for link in all_links()},
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
            adapter_version="gazebo-0.7.0",
            protocol_version="1.0",
            software_version="0.7.0",
            robot_profile=profile.to_wire(),
            capabilities=[
                capability(
                    name,
                    "Gazebo ROS2 导航、关节控制与传感器验证",
                    available=True,
                    safety_level="physical_motion"
                    if name in {"navigation.navigate", "arm.move", "recover_to_safe_pose", "emergency_stop"}
                    else "read_only",
                    cancellable=True,
                    mutates_world=name in {"navigation.navigate", "arm.move", "recover_to_safe_pose", "emergency_stop"},
                )
                for name in names
            ],
        )

    def observe(self, request):
        from .plugin_backend import project_entities

        runtime = self.node.runtime
        source_id = request.source_id or runtime.robot_id + "/base-rgbd"
        cameras = [name for name in runtime.cameras if source_id == runtime.robot_id + "/" + name]
        if not cameras:
            raise ValueError("UNKNOWN_CAMERA_SOURCE")
        camera = cameras[0]
        # Snapshot all inputs once: a later capture must never relabel this image.
        with self.node._lock:
            sample = runtime._samples.get(camera)
            if sample is None:
                raise ValueError("NO_CAPTURE")
            frame = runtime._frame_for(camera, sample)
            frame = replace(frame, sequence=max(1, sample.sensor_stamp_ns),
                            world_from_camera=sample.base_pose_at_capture @ frame.world_from_camera)
        reconstruction = self.perception.reconstruct(frame)
        requested = request.streams or ("rgb", "depth", "reconstruction", "robot_state")
        rgb = encode_rgb_png(frame.rgb) if "rgb" in requested else b""
        depth = encode_depth_preview(frame.depth_m) if "depth" in requested else b""
        return Observation(
            observation_id=reconstruction.observation_id,
            wall_time_unix_ms=frame.captured_at_unix_ms,
            monotonic_time_ns=time.monotonic_ns(),
            robot_state={
                "base_pose": leveled_base_pose(sample.base_pose_at_capture),
                "joint_positions": dict(getattr(self.node, "joint_positions", {})),
                "perception": {"source_id": source_id, "camera": camera,
                               "scene": getattr(self.node, "scene", "home"),
                               "calibration_revision": runtime.calibration_revision,
                               "calibration_source": "simulation",
                               "workcell_revision": runtime.calibration_revision,
                               "ground_truth_fallback": False},
            },
            entities=project_entities(reconstruction),
            reconstruction=reconstruction.to_wire(),
            compressed_image=rgb, image_media_type="image/png" if rgb else "",
            compressed_depth_image=depth, depth_image_media_type="image/png" if depth else "",
        )

    def execute(self, command):
        if command.capability in {"arm.move", "recover_to_safe_pose"}:
            from .gazebo_actuation import execute_chunk
            self.cancel_event.clear()
            return execute_chunk(self.node, command.parameters.get("action_chunk"), self.cancel_event)
        if command.capability == "observe_scene":
            from .runtime import ObservationRequest
            self.observe(ObservationRequest())
            return Result(True)
        if command.capability not in {"navigation.navigate", "verify_arrival"}:
            return Result(False, "CAPABILITY_UNAVAILABLE")
        goal = command.parameters.get("goalPose")
        if not isinstance(goal, list) or len(goal) != 7:
            return Result(False, "TOOL_PARAMETERS_INVALID", "goalPose 必须为七维位姿")
        try:
            pose = np.asarray(goal, dtype=float)
        except (TypeError, ValueError):
            return Result(False, "TOOL_PARAMETERS_INVALID")
        if not np.isfinite(pose).all() or abs(np.linalg.norm(pose[3:]) - 1) > 1e-3:
            return Result(False, "TOOL_PARAMETERS_INVALID")
        with self.node._lock:
            sample = self.node.runtime._samples.get("base-rgbd")
        if sample is None or not 0 <= time.monotonic_ns() - sample.received_monotonic_ns <= 1_000_000_000:
            return Result(False, "SENSOR_STALE", "到位检查和导航需要新鲜的 RGB-D 与里程计")
        if sample.odometry_stamp_ns <= 0 or abs(sample.odometry_stamp_ns - sample.sensor_stamp_ns) > 200_000_000:
            return Result(False, "ODOMETRY_STALE")
        current = leveled_base_pose(sample.base_pose_at_capture)
        if command.capability == "verify_arrival":
            from .gazebo_workflow import yaw_from_quaternion
            error = float(np.linalg.norm(pose[:2] - np.asarray(current[:2])))
            dyaw = yaw_from_quaternion(pose) - yaw_from_quaternion(current)
            arrived = error <= 0.08 and abs(math.atan2(math.sin(dyaw), math.cos(dyaw))) <= 0.15
            return Result(arrived, "OK" if arrived else "NOT_AT_DESTINATION",
                          f"position_error_m={error:.4f}")
        self.cancel_event.clear()
        outcome = self.node.navigate(goal, command.command_id, self.cancel_event)
        return Result(bool(outcome["ok"]), outcome.get("code", ""), outcome.get("message", ""))

    def stop(self, reason):
        self.cancel_event.set()
        if hasattr(self.node, "stop_navigation"):
            self.node.stop_navigation()
        if getattr(self.node, "workflow", None) is not None:
            self.node.workflow.cancel({})
        if hasattr(self.node, "hold_joints"):
            self.node.hold_joints()
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
