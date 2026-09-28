"""Gazebo navigation skills through the existing safety, journal and GVF service."""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import replace

import numpy as np

from .backend import RobotBackend, capability
from .gazebo_perception import GazeboWorkcellPerception
from .gazebo_runtime import leveled_base_pose
from .grounded.model import canonical
from .rgbd_images import encode_depth_preview, encode_rgb_png
from .runtime import Observation, Result, RuntimeInfo


def _await_recent_sample(node, camera, *, max_age_ms=450, timeout_s=3.0):
    """Reserve capture freshness budget for structured reconstruction work."""
    deadline = time.monotonic() + timeout_s
    while True:
        with node._lock:
            sample = node.runtime._samples.get(camera)
        if sample is not None:
            age_ms = int(time.time() * 1000) - sample.captured_at_unix_ms
            if 0 <= age_ms <= max_age_ms:
                return sample
        if time.monotonic() >= deadline:
            raise ValueError("FRESH_CAPTURE_TIMEOUT")
        time.sleep(.025)


class GazeboSkillBackend(RobotBackend):
    def __init__(self, node):
        self.node = node
        self.cancel_event = threading.Event()
        node.enable_bounded_motion()
        from .gazebo_commissioning import commissioning
        self.home = commissioning()
        if self.home:
            from .household_perception import HouseholdRgbdPerception
            self.perception = HouseholdRgbdPerception()
            import os
            from pathlib import Path

            from .cad_self_filter import CadSelfFilter
            asset_root = Path(os.environ["TANGYING_HOME_COMMISSIONING"]).parent
            self.self_filter = CadSelfFilter(os.environ["TANGYING_GAZEBO_WORLD"], asset_root,
                                             revision=self.home["resourceRevision"])
        else:
            self.perception = GazeboWorkcellPerception()
        self.perception_lock = threading.Lock()
        self.navigation_receipt = None
        from .gazebo_manipulation import GazeboManipulation
        self.manipulation = GazeboManipulation(self)
        node.prepare_navigation = self.manipulation.stow_for_navigation

    def capabilities(self):
        names = ["navigation.navigate", "navigation.pre_position", "verify_arrival", "observe_scene", "arm.move", "recover_to_safe_pose", "emergency_stop",
                 "resolve_targets", "plan_grasp", "manipulation.pick", "verify_grasp", "manipulation.place", "verify_placement"]
        from .arm_kinematics import all_links
        from .contracts import RobotProfile

        runtime = self.node.runtime
        if self.home and hasattr(self.node, "bindings"):
            self.node.bindings._refresh_camera_calibration()
        blockers = getattr(self.node, "readiness_blockers", list)()
        def tool_blockers(name):
            if name == "emergency_stop":
                return []
            irrelevant = set()
            if name in {"observe_scene", "resolve_targets", "verify_arrival"}:
                irrelevant = {"JOINT_FEEDBACK_STALE", "SUCTION_FEEDBACK_STALE"}
            elif name in {"verify_grasp", "verify_placement"}:
                irrelevant = {"JOINT_FEEDBACK_STALE"}
            elif name == "plan_grasp":
                irrelevant = {"SUCTION_FEEDBACK_STALE"}
            return [code for code in blockers if code not in irrelevant]

        profile = RobotProfile.model_validate(
            {
                "schema_version": "robot.profile.v1",
                "robot_id": runtime.robot_id,
                "adapter_id": "gazebo",
                "adapter_version": "gazebo-0.7.0",
                "model_id": "xlerobot" if self.home else "tangying_" + getattr(self.node, "scene", "home"),
                "embodiment": "mobile_manipulator",
                "joints": [{"name": link.motor, "kind": "revolute", "unit": "rad",
                            "lower": link.range_min, "upper": link.range_max} for link in all_links()],
                "end_effectors": [{"id": "left_suction", "kind": "suction", "joint_names": ["left_arm_gripper"]},
                                  {"id": "right_suction" if self.home else "right_gripper", "kind": "suction" if self.home else "gripper", "joint_names": ["right_arm_gripper"]}],
                "sensors": [
                    {
                        "source_id": runtime.robot_id + "/" + camera,
                        "source_type": "rgbd_camera",
                        "frame_id": getattr(runtime,"cameras",{}).get(camera,camera+"_optical"),
                        "transform_revision": runtime.calibration_revision,
                        "max_age_ms": 1000,
                    }
                    for camera in getattr(runtime, "cameras", {"base-rgbd": ""})
                ],
                "action_limits": {
                    **{link.motor+".pos": {"min": link.range_min, "max": link.range_max, "unit": "rad"} for link in all_links()},
                    "navigation.x": {"min": -6.0, "max": 6.0, "unit": "m"},
                    "navigation.y": {"min": -3.0, "max": 8.5, "unit": "m"},
                    "navigation.z": {"min": 0.0, "max": 1.0, "unit": "m"},
                },
                "tools": names,
                "internally_planned_tools": ["manipulation.pick", "manipulation.place", "recover_to_safe_pose"],
            }
        )
        return RuntimeInfo(
            robot_id=self.node.runtime.robot_id,
            adapter="gazebo",
            manipulation_ready=not blockers,
            blockers=blockers,
            adapter_version="gazebo-0.7.0",
            protocol_version="1.0",
            software_version="0.7.0",
            robot_profile=profile.to_wire(),
            capabilities=[
                capability(
                    name,
                    "RGB-D 测量、房间导航、关节控制、仿真吸附与物理状态验证",
                    available=not tool_blockers(name),
                    blockers=tool_blockers(name),
                    default_timeout_ms=600_000 if self.home and name in {"navigation.navigate", "navigation.pre_position", "manipulation.pick", "manipulation.place"} else 30_000,
                    safety_level="physical_motion"
                    if name in {"navigation.navigate", "navigation.pre_position", "arm.move", "recover_to_safe_pose", "emergency_stop", "manipulation.pick", "manipulation.place"}
                    else "read_only",
                    cancellable=True,
                    mutates_world=name in {"navigation.navigate", "navigation.pre_position", "arm.move", "recover_to_safe_pose", "manipulation.pick", "manipulation.place"},
                )
                for name in names
            ],
        )

    def observe(self, request):
        from .plugin_backend import project_entities

        runtime = self.node.runtime
        if self.home and hasattr(self.node, "bindings"):
            self.node.bindings._refresh_camera_calibration()
        source_id = request.source_id or runtime.robot_id + "/head-rgbd"
        cameras = [name for name in runtime.cameras if source_id == runtime.robot_id + "/" + name]
        if not cameras:
            raise ValueError("UNKNOWN_CAMERA_SOURCE")
        camera = cameras[0]
        if self.home:
            # Reconstructing and transporting a frame consumes part of the
            # sensor's 1 s validity window. Wait for a genuinely newer capture;
            # never stamp an old image with the time at which it was read.
            _await_recent_sample(self.node, camera)
        # Snapshot all inputs once: a later capture must never relabel this image.
        with self.node._lock:
            sample = runtime._samples.get(camera)
            if sample is None:
                raise ValueError("NO_CAPTURE")
            frame = runtime._frame_for(camera, sample)
            frame = replace(frame, sequence=max(1, sample.sensor_stamp_ns),
                            world_from_camera=sample.base_pose_at_capture @ frame.world_from_camera)
        perception_arguments = {}
        if self.home and sample.joint_stamp_ns > 0 and abs(sample.joint_stamp_ns-sample.sensor_stamp_ns) <= 200_000_000:
            from .arm_kinematics import arm_links, chain_poses
            tips = {}
            for side in ("left","right"):
                links = arm_links(side)
                if all(link.motor in sample.joint_positions_at_capture for link in links):
                    from .gazebo_tools import tool_pose
                    tips[side] = tool_pose(chain_poses(links,sample.joint_positions_at_capture,
                                           base=sample.base_pose_at_capture), self.manipulation.tool)[:3,3]
            perception_arguments = {"end_effectors":tips,
                "grippers":{side:"closed" if sample.tool_attached_at_capture and sample.tool_side_at_capture == side else "open" for side in tips}}
        with self.perception_lock:
            if self.home:
                from .home_commissioning import HOME_TASK_WORK_VOLUME
                perception_arguments["robot_mask"] = self.self_filter.filter(
                    frame, sample.joint_positions_at_capture, sample.base_pose_at_capture,
                    joint_stamp_ns=sample.joint_stamp_ns,
                    region=tuple(HOME_TASK_WORK_VOLUME[axis] for axis in "xyz"))
            reconstruction = self.perception.reconstruct(frame,**perception_arguments)
        requested = request.streams or ("rgb", "depth", "reconstruction", "robot_state")
        rgb = encode_rgb_png(frame.rgb) if "rgb" in requested else b""
        depth = encode_depth_preview(frame.depth_m) if "depth" in requested else b""
        observation = Observation(
            observation_id=reconstruction.observation_id,
            wall_time_unix_ms=frame.captured_at_unix_ms,
            monotonic_time_ns=time.monotonic_ns(),
            robot_state={
                "base_pose": leveled_base_pose(sample.base_pose_at_capture),
                "base_pose_frame": "world",
                # Commissioned workcell approach in the startup odometry frame.
                # This stays fixed when the robot drives away from its station.
                "navigation": {"approach_goal_pose": [0., 0., 0., 1., 0., 0., 0.],
                               "frame_id": "odom", "work_area": "workcell"},
                "joint_positions": dict(sample.joint_positions_at_capture),
                "perception": {"source_id": source_id, "camera": camera,
                               "scene": getattr(self.node, "scene", "home"),
                               "calibration_revision": runtime.calibration_revision,
                               "calibration_source": "simulation",
                               "workcell_revision": runtime.calibration_revision,
                               "ground_truth_fallback": False,
                               "capture_clock_source":sample.capture_clock_source,
                               "sensor_stamp_ns":str(sample.sensor_stamp_ns),
                               "detector": "rgbd-household-metric-shape-v1" if self.home else "commissioned_rgbd_colour", "grasp_mode": "sim_suction"},
            },
            entities=project_entities(reconstruction),
            reconstruction=reconstruction.to_wire(),
            compressed_image=rgb, image_media_type="image/png" if rgb else "",
            compressed_depth_image=depth, depth_image_media_type="image/png" if depth else "",
        )
        if sample.pose_fusion_source:
            observation.robot_state["pose_fusion_source"] = sample.pose_fusion_source

        if self.home:
            from .home_commissioning import HOME_WAYPOINTS, HOUSEHOLD_ACTION_CATALOG
            from .semantic_services import build_semantic_services
            active = self.node.workflow.active if self.node.workflow else None
            binding = ({**active, "validated":True, "fromFrame":"commissioning_world", "toFrame":"world",
                        "pose":[0.,0.,0.,1.,0.,0.,0.]} if active else None)
            observation.robot_state.update(build_semantic_services("home_task", robot_id=runtime.robot_id,
                calibration_revision=runtime.calibration_revision, active_map=active, map_to_world=binding,
                object_catalog=HOUSEHOLD_ACTION_CATALOG))
            # The activated, integrity-checked semantic artifact owns goals. Do
            # not relabel a static commissioning pose as a measured-map location.
            measured_navigation = self.node.workflow.semantic_navigation() if self.node.workflow else {}
            if measured_navigation:
                annotated = observation.robot_state.get("semantic_navigation",{}).get("routeEdges",{})
                goals = measured_navigation["goals"]
                measured_navigation["routeEdges"] = {name:[other for other in neighbors if other in goals]
                    for name,neighbors in annotated.items() if name in goals}
                measured_navigation["topologySource"] = "commissioned_connections"
            observation.robot_state["semantic_navigation"] = measured_navigation
            if not active:
                observation.robot_state.pop("active_map",None)
            observation.robot_state["navigation"] = {"approach_goal_pose":HOME_WAYPOINTS["kitchen"],
                "frame_id":"world", "work_area":"kitchen", "scene":"home_task"}
            observation.robot_state["perception"]["scene"] = "home_task"
            observation.robot_state["tool_commissioning"] = {
                **self.manipulation.tool, "contentRevision": self.manipulation.tool_revision}
            observation.robot_state["perception"]["self_filter"] = {
                "available": True, "model_revision": self.self_filter.revision,
                "masked_pixels": int(np.count_nonzero(perception_arguments["robot_mask"])),
                "source": "capture_encoders_and_robot_cad_surface", "tolerance_m": .004,
                "encoder_stamp_ns": str(sample.joint_stamp_ns), "capture_stamp_ns": str(sample.sensor_stamp_ns)}
            if self.navigation_receipt and active == {key:self.navigation_receipt[key] for key in ("mapId","mapRevision","calibrationRevision")}:
                observation.robot_state["map_route"] = dict(self.navigation_receipt)
            if self.manipulation.verification:
                observation.robot_state["verification"] = dict(self.manipulation.verification)
        return observation

    def execute(self, command):
        if command.capability == "arm.move" or (command.capability == "recover_to_safe_pose" and "action_chunk" in command.parameters):
            from .gazebo_actuation import execute_chunk
            return execute_chunk(self.node, command.parameters.get("action_chunk"), self.cancel_event)
        if command.capability in {"resolve_targets", "plan_grasp", "manipulation.pick", "verify_grasp", "manipulation.place", "verify_placement", "recover_to_safe_pose"}:
            return self.manipulation.execute(command)
        if command.capability == "observe_scene":
            from .runtime import ObservationRequest
            self.observe(ObservationRequest())
            return Result(True)
        if command.capability not in {"navigation.navigate", "navigation.pre_position", "verify_arrival"}:
            return Result(False, "CAPABILITY_UNAVAILABLE")
        goal = command.parameters.get("goalPose")
        if command.capability == "navigation.pre_position":
            goal = leveled_base_pose(self.node.runtime.base_pose)
            yaw = command.parameters.get("alignYaw", 2*math.atan2(goal[6], goal[3]))
            goal[3:] = [math.cos(yaw/2), 0., 0., math.sin(yaw/2)]
        if not isinstance(goal, list) or len(goal) != 7:
            return Result(False, "TOOL_PARAMETERS_INVALID", "goalPose 必须为七维位姿")
        try:
            pose = np.asarray(goal, dtype=float)
        except (TypeError, ValueError):
            return Result(False, "TOOL_PARAMETERS_INVALID")
        if not np.isfinite(pose).all() or abs(np.linalg.norm(pose[3:]) - 1) > 1e-3:
            return Result(False, "TOOL_PARAMETERS_INVALID")
        # Await a fresh pair before any motion. Startup / software rendering may
        # delay one camera while the other is current; never relabel the old pair.
        wait_budget = min(2., max(0., command.deadline_unix_ms/1000.-time.time()))
        fresh_deadline = time.monotonic()+wait_budget
        while True:
            if self.cancel_event.is_set():
                return Result(False, "CANCELLED")
            with self.node._lock:
                sample = self.node.runtime._samples.get("base-rgbd")
            if sample is not None and 0 <= time.monotonic_ns()-sample.received_monotonic_ns <= 1_000_000_000:
                break
            if time.monotonic() >= fresh_deadline:
                return Result(False, "SENSOR_STALE", "到位检查和导航需要新鲜的 RGB-D 与里程计")
            time.sleep(.02)
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
        # Commissioned workcell entry: align in free space before approaching
        # the table. Turning to remove lateral error at its edge sweeps the
        # front chassis corner into the table's measured obstacle envelope.
        # Every leg still goes through Nav2, collision checking and cancellation.
        admitted = None
        if self.home and command.capability == "navigation.navigate":
            workflow = self.node.workflow
            if not workflow or not workflow.active or workflow.grid is None:
                return Result(False,"NAV_MAP_NOT_READY","请先完成扫描并激活地图。")
            from .grid_navigation import world_route
            from .service_registry import ServiceError
            try:
                route = world_route(workflow.grid,workflow.map_from_world,current,goal,workflow.footprint_radius)
            except ServiceError as error:
                return Result(False,error.code,str(error))
            admitted = {**workflow.active,"waypoint_count":len(route),"admission":"saved_measured_grid",
                        "executionProvider":"rtabmap_nav2","commandId":command.command_id,"goalPose":list(goal)}
        self.navigation_receipt = None
        def remaining_navigation_s():
            if command.deadline_unix_ms <= 0:
                return 300.
            return max(0., command.deadline_unix_ms/1000.-time.time())
        waypoints = []
        if (not self.home and command.capability == "navigation.navigate"
                and np.linalg.norm(pose[:2]) < .001
                and abs(pose[3]) > .999999
                and np.linalg.norm(current[:2]) > .15):
            staging = [-.30, 0., 0., 1., 0., 0., 0.]
            staged = self.node.navigate(staging, command.command_id+"/workcell-entry", self.cancel_event,
                                        deadline_s=remaining_navigation_s())
            waypoints.append(staging)
            if not staged["ok"]:
                return Result(False, staged.get("code", "NAVIGATION_FAILED"),
                              "Workcell entry: "+staged.get("message", ""),
                              payload={"navigationWaypointsJson": json.dumps(waypoints)})
        outcome = self.node.navigate(goal, command.command_id, self.cancel_event,
                                     deadline_s=remaining_navigation_s())
        waypoints.append(goal)
        if outcome["ok"] and not self._wait_post_navigation_capture(time.monotonic_ns(), int(time.time()*1000)):
            return Result(False, "POSTCONDITION_OBSERVATION_TIMEOUT",
                          "Navigation ended without a newer RGB-D frame; reconcile before retrying motion")
        if outcome["ok"] and admitted:
            if self.node.workflow.active != {key:admitted[key] for key in ("mapId","mapRevision","calibrationRevision")}:
                return Result(False,"MAP_CHANGED_DURING_NAVIGATION")
            self.navigation_receipt = admitted
            self.navigation_receipt["observationHolds"] = outcome.get("observationHolds", [])
        return Result(bool(outcome["ok"]), outcome.get("code", ""), outcome.get("message", ""),
                      payload={"navigationWaypointsJson": json.dumps(waypoints)})

    def _wait_post_navigation_capture(self, completed_ns, completed_wall_ms):
        # Even POSE_ALREADY_CONFIRMED must expose post-dispatch evidence. A
        # cached frame can be perfectly fresh yet predate this fast command.
        deadline = time.monotonic()+2.
        with self.node._lock:
            completed_sensor_ns = getattr(self.node, "_joint_stamp_ns", 0)
        if self.home and completed_sensor_ns <= 0:
            return False
        while time.monotonic() < deadline and not self.cancel_event.is_set():
            with self.node._lock:
                samples = [self.node.runtime._samples.get(name) for name in ("head-rgbd", "base-rgbd")]
            if all(sample is not None and sample.received_monotonic_ns > completed_ns
                   and sample.captured_at_unix_ms > completed_wall_ms
                   and sample.sensor_stamp_ns > completed_sensor_ns for sample in samples):
                return True
            time.sleep(.02)
        return False

    def wait_command_capture(self, command, completed_ns, completed_wall_ms):
        # Shared Runtime pins this exact validated capture in the terminal
        # event *before* journaling. Replays return its original bytes rather
        # than a newer camera image. This covers read-only verification too.
        if command.deadline_unix_ms <= int(time.time()*1000):
            return False
        return self._wait_post_navigation_capture(completed_ns, completed_wall_ms)

    def stop(self, reason):
        self.cancel_event.set()
        if hasattr(self.node, "suction_command"):
            self.node.suction_command("hold")
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
        from .gazebo_grounded import TOOLS, collect
        if command.capability in TOOLS:
            return collect(self, command=command, action_id=action_id, start_ns=start_ns,
                           edge_boot_id=edge_boot_id, store=store, phase=phase)
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

    def grounded_contracts(self):
        from .gazebo_grounded import contracts
        return contracts()

    def grounded_parameters(self, command):
        from .gazebo_grounded import parameters
        return parameters(self, command)


def grounded_service_requires_contract(name: str, mutates_world: bool) -> bool:
    """The service RPC must not bypass the skill evidence boundary."""
    return mutates_world and name not in {"mapping.stop_motion", "mapping.cancel", "mapping.finish"}
