"""Single robot reference loop driven only by head RGB-D environment evidence."""

from __future__ import annotations

import copy
import math
import os
import threading
import time

import grpc
import mujoco
import numpy as np
from google.protobuf.json_format import MessageToDict
from tangying_robot_gateway.contracts import Reconstruction, RobotProfile, validate_tool_parameters
from tangying_robot_gateway.rgbd import RgbdFrame, RgbdPerception, validate_frame
from tangying_robot_gateway.rgbd_images import encode_depth_preview
from tangying_robot_proto.robot.v1 import robot_pb2

from .rendering import SceneRenderer, _encode_png
from .rgbd_navigation import (
    BASE_CAMERA_TRANSFORM_REVISION,
    NavigationCapture,
    NavigationController,
    load_navigation_model,
    robot_local_bounds,
    validate_navigation_model,
)
from .rgbd_perception import TabletopRgbdPerception
from .rgbd_workcell import WORKCELL_REVISION
from .rtabmap_client import RTABMapClient
from .self_filter import RobotSelfFilter, robot_joint_positions
from .server import RobotRuntimeService
from .tools import ToolResult
from .world import SceneEntity, TabletopWorld, _synchronized


class RgbdTabletopWorld(TabletopWorld):
    """Physics still owns contact/attachment; goals and verification use vision."""

    def _load_model(self, path):
        return load_navigation_model(path)

    def _validate_model(self, model):
        validate_navigation_model(model)

    def __init__(self, *args, **kwargs):
        self.capture_scene = None
        self._mobile_navigation_enabled = bool(os.environ.get("TANGYING_NAVIGATION_URL"))
        super().__init__(*args, **kwargs)
        self._configure_workcell()

    @_synchronized
    def reset(self):
        super().reset()
        self._configure_workcell()
        self.verification_capture = None
        return self

    def _configure_workcell(self):
        self.motion.allow_base_motion = False
        if not self._mobile_navigation_enabled:
            # The default demo is commissioned directly at its fixed workcell.
            # Only the explicitly configured navigation variant starts at home
            # and performs a measured, planned approach from that pose.
            joint = self.model.joint("slide_joint_x").id
            self.data.qpos[self.model.jnt_qposadr[joint]] = .05
        # A deliberately commissioned two-object workcell. Other fixtures are
        # outside the work volume and must never appear through truth fallback.
        for name, _, joint, _, _ in self._OBJECT_SPECS:
            if name not in {"red-cup", "blue-bottle"}:
                self._set_free_body_position(joint, (10.0, 10.0, -2.0))
        # Explicit reference fixture layout: the source cup is within the
        # stationary arm's reachable workcell. No action resets its position.
        self._set_free_body_position("red_cup_free", (0.29, 0.49, 0.80))
        self._set_free_body_position("blue_bottle_free", (-0.24, 0.49, 0.82))
        # Legacy fixtures are visual markers. In the camera reference workcell
        # these observed surfaces must actually support released free bodies.
        for body in ("left_bin", "right_bin", "front_tray"):
            body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body)
            geoms = self.model.geom_bodyid == body_id
            self.model.geom_contype[geoms] = 1
            self.model.geom_conaffinity[geoms] = 1
            self.model.body_contype[body_id] = 1
            self.model.body_conaffinity[body_id] = 1
        camera = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, "head_depth")
        self.model.cam_mode[camera] = mujoco.mjtCamLight.mjCAMLIGHT_FIXED
        # Fixed calibrated head mount; never tracks a targetbody/world object.
        self.model.cam_quat[camera] = [0.668536, 0.230346, -0.230346, -0.668536]
        mujoco.mj_forward(self.model, self.data)
        self._publish_sensor_snapshot()

    def _publish_sensor_snapshot(self):
        captured_at = int(time.time() * 1000)
        data, state = copy.copy(self.data), self.robot_state()
        # These are private capture-time encoders, including all three wheels.
        # Never query live qpos later while serializing an older camera frame.
        state["_self_filter_joint_positions"] = robot_joint_positions(self.model, data)
        state["_self_filter_observed_at_unix_ms"] = captured_at
        self.sensor_snapshot = (data, state, captured_at)

    @_synchronized
    def prepare_navigation(self, cancel_event, deadline_unix_ms=None):
        """Commissioned simultaneous empty-arm stow; never moves the base.

        This path is only calibrated from the model's zero arm posture or an
        already stowed state. Arbitrary recovery positions require a separately
        verified planner and are rejected, rather than interpolated blindly.
        """
        if cancel_event.is_set():
            return ToolResult(False, "CANCELLED", confidence=0.0)
        if deadline_unix_ms is not None and int(time.time()*1000) >= deadline_unix_ms:
            return ToolResult(False, "NAV_DEADLINE_EXCEEDED", confidence=0.0)
        if self._held is not None:
            return ToolResult(False, "NAV_STOW_REQUIRES_EMPTY_GRIPPERS", confidence=0.0)
        target = {f"{stem}_{suffix}": value for suffix in ("L", "R")
                  for stem, value in {"Rotation": 0.0, "Pitch": 3.1, "Elbow": 1.0,
                                      "Wrist_Pitch": 0.0, "Wrist_Roll": 0.0}.items()}
        joints = np.array([self.model.joint(name).id for name in target])
        addresses, dofs = self.model.jnt_qposadr[joints], self.model.jnt_dofadr[joints]
        start, finish = self.data.qpos[addresses].copy(), np.array(list(target.values()))
        if np.allclose(start, finish, atol=1e-6, rtol=0):
            return self._verify_navigation_stow("NAV_ARMS_ALREADY_STOWED")
        if not np.allclose(start, 0, atol=1e-6, rtol=0):
            return ToolResult(False, "NAV_STOW_START_UNSUPPORTED", "arm posture is outside the commissioned stow trajectory", 0.0)
        if np.any(finish < self.model.jnt_range[joints, 0]) or np.any(finish > self.model.jnt_range[joints, 1]):
            return ToolResult(False, "NAV_STOW_JOINT_LIMIT", confidence=0.0)
        chassis = self.model.body("chassis").id
        robot_bodies = {chassis}
        for body in range(chassis + 1, self.model.nbody):
            if int(self.model.body_parentid[body]) in robot_bodies:
                robot_bodies.add(body)
        for sample in range(1, 201):
            if cancel_event.is_set():
                return ToolResult(False, "CANCELLED", "stow stopped at the current arm posture", 0.0)
            if self.motion.step_delay > 0:
                time.sleep(self.motion.step_delay)
            if cancel_event.is_set():
                return ToolResult(False, "CANCELLED", confidence=0.0)
            if deadline_unix_ms is not None and int(time.time()*1000) >= deadline_unix_ms:
                return ToolResult(False, "NAV_DEADLINE_EXCEEDED", "navigation lease expired while stowing", 0.0)
            self.data.qpos[addresses] = start + (finish-start) * (sample/200)
            self.data.qvel[dofs] = 0
            mujoco.mj_forward(self.model, self.data)
            self._increment_step_count()
            if any(contact.dist < -1e-5 and (
                int(self.model.geom_bodyid[contact.geom1]) in robot_bodies
                or int(self.model.geom_bodyid[contact.geom2]) in robot_bodies
            ) for contact in self.data.contact):
                return ToolResult(False, "NAV_STOW_CONTACT", "physical contact interrupted the commissioned stow", 0.0)
        return self._verify_navigation_stow("NAV_ARMS_STOWED")

    def _verify_navigation_stow(self, code):
        lower, upper = robot_local_bounds(self.model, self.data)
        if np.any(lower < [-.24, -.23, -.06]) or np.any(upper > [.22, .21, 1.20]):
            return ToolResult(False, "NAV_STOW_ENVELOPE_MISMATCH", "robot geometry exceeds the commissioned Nav2 footprint", 0.0)
        return ToolResult(True, code, "empty arms fit the commissioned navigation envelope", 1.0,
                          {"robot_bounds_base": [lower.tolist(), upper.tolist()]})

    def _refresh_observation_cache(self):
        self._publish_sensor_snapshot()

    def _step(self, count):
        # This reference controller models an ideal position servo. Keep only
        # robot hinge/slide joints at their commanded posture while MuJoCo
        # integrates the free objects and their contacts. Never reposition a
        # released object to make placement pass.
        joints = np.flatnonzero(self.model.jnt_type != mujoco.mjtJoint.mjJNT_FREE)
        qpos = self.model.jnt_qposadr[joints]
        dofs = self.model.jnt_dofadr[joints]
        held_positions = self.data.qpos[qpos].copy()
        for _ in range(count):
            mujoco.mj_step(self.model, self.data)
            self.data.qpos[qpos] = held_positions
            self.data.qvel[dofs] = 0
            if self._held is not None and self._active_arm:
                self._follow_attachment(self._pickable_joints[self._held], self._active_arm)
            mujoco.mj_forward(self.model, self.data)
            self.step_count += 1
            if self._human_speed > 0:
                time.sleep(self._human_speed)
        self._publish_sensor_snapshot()

    def _increment_step_count(self):
        super()._increment_step_count()
        self._publish_sensor_snapshot()

    def _follow_attachment(self, joint_name, arm):
        super()._follow_attachment(joint_name, arm)
        joint = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        dof = self.model.jnt_dofadr[joint]
        self.data.qvel[dof:dof + 6] = 0
        self._publish_sensor_snapshot()

    def _move_named(self, arm, name, *, steps, on_step=None, cancel_event=None):
        def sample(progress):
            if on_step is not None:
                on_step(progress)
            self._publish_sensor_snapshot()

        return super()._move_named(
            arm, name, steps=steps, on_step=sample, cancel_event=cancel_event
        )

    def has_object(self, entity_id):
        return any(
            e.entity_id == entity_id and e.category in {"cup", "bottle"} for e in self.entities()
        )

    def has_destination(self, entity_id):
        return any(
            e.entity_id == entity_id and e.category in {"storage_bin", "delivery_tray"}
            for e in self.entities()
        )

    def entities(self):
        if self.capture_scene is None:
            return []
        scene, _, _ = self.capture_scene()
        return [
            SceneEntity(
                e.entity_id, e.category, e.attributes, e.relation, e.confidence, tuple(e.pose[:3])
            )
            for e in scene.entities
        ]

    def _observed_position(self, entity_id):
        entity = next((e for e in self.entities() if e.entity_id == entity_id), None)
        if entity is None:
            raise ValueError(f"RGBD_TARGET_NOT_VISIBLE: {entity_id}")
        return entity.position

    def _control_pick_target(self, entity_id, joint):
        return self._observed_position(entity_id)

    def _body_position(self, body_name):
        targets = {"left_bin": "left-bin", "right_bin": "right-bin", "front_tray": "front-tray"}
        if body_name in targets and self.capture_scene is not None:
            return self._observed_position(targets[body_name])
        return super()._body_position(body_name)

    def _after_lift(self, joint, arm, cancel_event):
        # This simulation controller presents the object to the onboard camera.
        # It is a commissioned inspection pose, not a transferable real policy.
        target = (-0.25 if arm == "left" else 0.25, 0.45, 0.95)
        self.motion.approach_body(
            arm,
            {"left": "Fixed_Jaw_2", "right": "Fixed_Jaw"}[arm],
            target,
            on_step=lambda _: self._follow_attachment(joint, arm),
            cancel_event=cancel_event,
        )

    def _control_place_target(self, destination_id, destination_position, entity_id):
        entity = next((e for e in self.entities() if e.entity_id == entity_id), None)
        if entity is None:
            raise ValueError(f"RGBD_TARGET_NOT_VISIBLE: {entity_id}")
        half_height = {"cup": 0.06, "bottle": 0.08}[entity.category]
        # Surface height comes from the RGB-D destination; object dimensions
        # belong to this commissioned two-object detector/controller catalog.
        return (*destination_position[:2], destination_position[2] + 0.02 + half_height + 0.004)

    def _placement_reached(self, object_position, destination_position, desired_object_position):
        return bool(np.linalg.norm(object_position - desired_object_position) <= 0.015)

    def _after_release(self, entity_id, destination_id, arm, cancel_event):
        position = np.asarray(self.end_effector_position(arm))
        target = position + np.array([0.0, -0.035, 0.16])
        cleared = self.motion.approach_body(
            arm, {"left": "Fixed_Jaw_2", "right": "Fixed_Jaw"}[arm], tuple(target),
            on_step=lambda _: self._increment_step_count(), cancel_event=cancel_event,
        )
        if not cleared:
            return ToolResult(False, "RELEASE_CLEARANCE_NOT_REACHED", confidence=0.0)
        # The lifted wrist can still hide the target from the head camera.
        # Retreat to the commissioned clear-view arm pose before verification.
        self._move_named(arm, "HOME", steps=12, cancel_event=cancel_event)
        self._step(125)  # 250 ms of actual free-body/contact simulation.
        return ToolResult(True)

    @_synchronized
    def verify_grasp(self, entity_id):
        return self._verify_relation(entity_id, f"held_by:{self.robot_id}", "GRASP_NOT_OBSERVED")

    @_synchronized
    def verify_inside(self, entity_id, destination_id):
        return self._verify_relation(
            entity_id, f"inside:{destination_id}", "PLACEMENT_NOT_OBSERVED"
        )

    def _verify_relation(self, entity_id, expected, failure):
        samples = []
        self.verification_capture = None
        found = None
        success = False
        max_displacement = 0.0
        for index in range(3):
            if index:
                self._step(25)
                delay = (samples[-1][0].observed_at_unix_ms + 50) / 1000 - time.time()
                if delay > 0:
                    time.sleep(delay)
            capture = self.capture_scene()
            scene, _, _ = capture
            self.verification_capture = capture
            found = next((e for e in scene.entities if e.entity_id == entity_id), None)
            if found is None or found.relation != expected:
                break
            if samples and (scene.observation_id == samples[-1][0].observation_id
                            or scene.sequence <= samples[-1][0].sequence
                            or scene.observed_at_unix_ms <= samples[-1][0].observed_at_unix_ms):
                break
            if samples:
                max_displacement = max(max_displacement, float(np.linalg.norm(
                    np.asarray(found.pose[:3]) - np.asarray(samples[0][1].pose[:3])
                )))
                if max_displacement > 0.008:
                    break
            samples.append((scene, found))
        duration = (samples[-1][0].observed_at_unix_ms - samples[0][0].observed_at_unix_ms) / 1000 if len(samples) > 1 else 0.0
        success = len(samples) == 3 and duration >= 0.1
        self._verification_confidence = min(e.confidence for _, e in samples) if success else 0.0
        if self.verification_capture is not None:
            self.verification_capture[2]["verification"] = {
                "kind": "verify_grasp" if expected.startswith("held_by:") else "verify_placement",
                "object_id": entity_id,
                "destination_id": "" if expected.startswith("held_by:") else expected.split(":", 1)[1],
                "passed": success, "sample_count": len(samples),
                "stable_duration_s": duration, "max_displacement_m": max_displacement,
                "expected_relation": expected,
                "observed_relation": found.relation if found is not None else "",
                "source_id": self.verification_capture[0].source_id,
                "observation_id": self.verification_capture[0].observation_id,
                "first_observed_at_unix_ms": samples[0][0].observed_at_unix_ms if samples else scene.observed_at_unix_ms,
                "last_observed_at_unix_ms": scene.observed_at_unix_ms,
            }
        return ToolResult(
            success, "OK" if success else failure, confidence=self._verification_confidence
        )


class RgbdRuntimeService(RobotRuntimeService):
    def __init__(self, world: RgbdTabletopWorld, robot_id="xlerobot-mujoco-tabletop", **kwargs):
        endpoint = os.environ.get("TANGYING_NAVIGATION_URL", "")
        self._navigation_client = (RTABMapClient(endpoint, os.environ.get("TANGYING_NAVIGATION_TOKEN", ""), robot_id=robot_id)
                                   if endpoint else None)
        self._navigation_last_result = {}
        super().__init__(world, robot_id, cameras=("head-rgbd", "base-rgbd"), **kwargs)
        self.renderer.close()
        self.renderer = SceneRenderer(
            camera="head_depth", width=self._render_width, height=self._render_height
        )
        self.perception = TabletopRgbdPerception()
        self.navigation = NavigationController(
            world, robot_id, render_width=self._render_width, render_height=self._render_height,
            approach_goal_pose=[0.0, 0.05, 0.035, 2**-0.5, 0.0, 0.0, 2**-0.5],
        )
        self._base_perception = RgbdPerception(lambda frame: [])
        self._self_filter = RobotSelfFilter()
        self._capture_lock = threading.RLock()
        self._sequence = int(time.time() * 1000) * 1000
        self._last_scene = None
        self._command_evidence = {}
        self.world.capture_scene = self.capture_scene
        self.GetRuntimeInfo(None, None)  # Static morphology must never wait for a moving robot.

    def _capability_infos(self):
        capabilities = super()._capability_infos()
        if self._navigation_client is None:
            return capabilities
        with self._commands_lock:
            ready = not self._estopped
        capabilities.append(robot_pb2.CapabilityInfo(
            name="navigation.navigate", description="Approach using the forward RGB-D camera",
            available=ready, safety_level="physical_motion", cancellable=True, recoverable=True,
            default_timeout_ms=15000, input_parameters=["goalPose"],
        ))
        return capabilities

    def GetRuntimeInfo(self, request, context):
        info = super().GetRuntimeInfo(request, context)
        if hasattr(self, "_profile_wire"):
            info.robot_profile.update(self._profile_wire)
            return info
        joints = []
        limits = {}
        for name in self.world.joint_positions():
            index = mujoco.mj_name2id(self.world.model, mujoco.mjtObj.mjOBJ_JOINT, name)
            lower, upper = [float(v) for v in self.world.model.jnt_range[index]]
            joints.append(
                {"name": name, "kind": "revolute", "unit": "rad", "lower": lower, "upper": upper}
            )
            limits[name] = {"min": lower, "max": upper, "unit": "rad"}
        if self._navigation_client is not None:
            for axis, lower, upper in zip("xyz", self.navigation.limits.world_lower, self.navigation.limits.world_upper, strict=True):
                limits[f"navigation.{axis}"] = {"min": lower, "max": upper, "unit": "m"}
        profile = RobotProfile.model_validate(
            {
                "schemaVersion": "robot.profile.v1",
                "robotId": info.robot_id,
                "adapterId": info.adapter,
                "adapterVersion": info.adapter_version,
                "modelId": "xlerobot-rgbd-reference",
                "embodiment": "mobile_manipulator" if self._navigation_client is not None else "dual_arm",
                "joints": joints,
                "endEffectors": [],
                "sensors": [
                    {
                        "sourceId": f"{info.robot_id}/head-rgbd",
                        "sourceType": "rgbd_camera",
                        "frameId": "head_depth_optical",
                        "transformRevision": "head-mount-fixed-v1",
                        "maxAgeMs": 2000,
                    },
                    {"sourceId": f"{info.robot_id}/base-rgbd", "sourceType": "rgbd_camera",
                     "frameId": "base_depth_optical", "transformRevision": BASE_CAMERA_TRANSFORM_REVISION,
                     "maxAgeMs": 2000},
                ],
                "actionLimits": limits,
                "tools": list(info.skills),
            }
        )
        self._profile_wire = profile.to_wire()
        info.robot_profile.update(self._profile_wire)
        return info

    def capture_scene(self):
        # Consistent acquisition/kinematics, one lock order for tool and observer
        # threads. Rendering consumes only camera and robot pose from this copy.
        with self._capture_lock:
            # During motion consume an atomic state copy published by the
            # controller's on_step callback. Its ORIGINAL timestamp is retained.
            # A stalled controller therefore produces stale, never fresh, frames.
            if self.world.lock.acquire(blocking=False):
                try:
                    self.world._publish_sensor_snapshot()
                finally:
                    self.world.lock.release()
            data, state, captured_at = self.world.sensor_snapshot
            state = copy.deepcopy(state)
            for arm in ("left", "right"):
                jaw, closed = next(iter(self.world.motion.target_for(arm, "CLOSED").items()))
                opened = self.world.motion.target_for(arm, "OPEN")[jaw]
                measured = state["joint_positions"][jaw]
                state["grippers"][arm] = (
                    "closed"
                    if abs(measured - closed) < 0.04
                    else "open"
                    if abs(measured - opened) < 0.04
                    else "transition"
                )
            pixels = self.renderer.render_rgbd(self.world.model, data)
            capture_times = (captured_at, pixels.captured_at_unix_ms)
            now_ms = int(time.time() * 1000)
            if any(type(value) is not int or value > now_ms for value in capture_times):
                raise ValueError("RGB-D capture time is invalid or future dated")
            # Both the physical state copy and rendered pixels must be fresh.
            # A cached camera image cannot borrow a new controller timestamp,
            # and rendering a stalled controller must retain its old timestamp.
            captured_at = min(capture_times)
            self._sequence += 1
            frame = RgbdFrame(
                self._robot_id,
                f"{self._robot_id}/head-rgbd",
                "head_depth_optical",
                "head-mount-fixed-v1",
                captured_at,
                self._sequence,
                pixels.rgb,
                pixels.depth_m,
                pixels.intrinsics,
                pixels.world_from_camera,
            )
            scene = self.perception.reconstruct(
                frame, end_effectors=state["end_effectors"], grippers=state["grippers"]
            )
            # Perception latency also consumes the capture's freshness budget.
            validate_frame(frame)
            self._last_scene = scene
            # No private simulator held/placement/reward values leave this path.
            public = {
                key: state[key]
                for key in (
                    "model_revision",
                    "base_pose",
                    "joint_positions",
                    "grippers",
                    "end_effectors",
                    "active_tool",
                    "step_count",
                    "simulation",
                    "episode",
                )
            }
            public["held"] = next(
                (e.entity_id for e in scene.entities if e.relation == f"held_by:{self._robot_id}"),
                "",
            )
            public["placements"] = {
                e.entity_id: e.relation.split(":", 1)[1]
                for e in scene.entities
                if e.relation.startswith("inside:")
            }
            public["perception"] = {
                "mode": "rgbd",
                "source_id": scene.source_id,
                "camera": "head_depth",
                "observed_at_unix_ms": scene.observed_at_unix_ms,
                "sequence": scene.sequence,
                "observation_id": scene.observation_id,
                "point_count": len(scene.points),
                "detector": "commissioned-two-object-colour-geometry-v1",
                "simulation": True,
                "ground_truth_fallback": False,
                "depth_range_m": [0.02, 5.0],
                "workcell_revision": WORKCELL_REVISION,
            }
            public["navigation"] = self._navigation_status()
            # Internal raw-frame hook consumes these and removes them before
            # ordinary robot_state serialization. They are not environment data.
            public["_self_filter_joint_positions"] = state["_self_filter_joint_positions"]
            public["_self_filter_observed_at_unix_ms"] = state["_self_filter_observed_at_unix_ms"]
            return scene, pixels, public

    def _navigation_status(self):
        if self._navigation_client is None:
            return {"backend": "fixed_workcell", "mobile_navigation_enabled": False}
        # Do not block camera acquisition on an HTTP health poll. Readiness is
        # checked by the client at dispatch, and this cached receipt is labeled
        # as such rather than pretending to be current map evidence.
        return {"backend": "rtabmap_nav2", "approach_goal_pose": self.navigation.approach_goal_pose.copy(),
                "last_execution": copy.deepcopy(self._navigation_last_result)}

    def Observe(self, request, context):
        source = request.source_id
        if source not in {"", f"{self._robot_id}/head-rgbd", f"{self._robot_id}/base-rgbd"}:
            if context is not None:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "unknown camera source_id")
            raise ValueError("unknown camera source_id")
        try:
            include_raw = "rgbd_raw" in request.streams
            if include_raw and "sensor_only" in request.streams:
                observation = self._sensor_observation(source)
            else:
                observation = (self._base_observation(include_raw=include_raw)
                               if source.endswith("/base-rgbd") else self._observation(include_raw=include_raw))
        except (ValueError, RuntimeError) as exc:
            if context is not None:
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
            raise
        with self._commands_lock:
            estopped = self._estopped
        observation.semantic_state.CopyFrom(robot_pb2.SemanticState(
            activity="EMERGENCY_STOPPED" if estopped else "IDLE", mode="SIMULATION",
            emergency_stopped=estopped,
            anomalies=["EMERGENCY_STOP_LATCHED"] if estopped else [],
        ))
        yield observation

    def _base_observation(self, capture=None, *, include_raw=False):
        raw_capture = self.navigation.capture_with_state() if include_raw and capture is None else None
        if raw_capture is not None:
            frame, base_pose = raw_capture.frame, raw_capture.base_pose
        else:
            frame, base_pose = capture if capture is not None else self.navigation.capture()
        scene = self._base_perception.reconstruct(frame)
        state = {
            "base_pose": base_pose, "simulation": True,
            "navigation": self._navigation_status(),
            "perception": {"mode": "rgbd_navigation", "source_id": frame.source_id,
                           "observed_at_unix_ms": frame.captured_at_unix_ms,
                           "observation_id": scene.observation_id},
        }
        if raw_capture is not None:
            state["_self_filter_joint_positions"] = raw_capture.joint_positions
            state["_self_filter_observed_at_unix_ms"] = raw_capture.joints_observed_at_unix_ms
        return self._observation_from_capture((scene, frame, state), include_raw=include_raw)

    def _observation(self, *, include_raw=False):
        return self._observation_from_capture(self.capture_scene(), include_raw=include_raw)

    def _observation_from_capture(self, capture, *, include_raw=False):
        scene, pixels, state = capture
        observation = robot_pb2.Observation(
            observation_id=scene.observation_id,
            wall_time_unix_ms=scene.observed_at_unix_ms,
            monotonic_time_ns=time.monotonic_ns(),
            compressed_image=_encode_png(
                self._render_width, self._render_height, pixels.rgb.tobytes()
            ),
            image_media_type="image/png",
            compressed_depth_image=encode_depth_preview(pixels.depth_m),
            depth_image_media_type="image/png",
            entities=[
                robot_pb2.SceneEntity(
                    entity_id=e.entity_id,
                    category=e.category,
                    attributes=e.attributes,
                    pose_xyz_quat=e.pose,
                    confidence=e.confidence,
                    relation=e.relation,
                )
                for e in scene.entities
            ],
        )
        observation.robot_state.update({key: value for key, value in state.items()
                                        if not key.startswith("_self_filter")})
        observation.reconstruction.update(scene.to_wire())
        if include_raw:
            frame = RgbdFrame(
                self._robot_id, scene.source_id, scene.source_frame_id, scene.transform_revision,
                scene.observed_at_unix_ms, scene.sequence, pixels.rgb, pixels.depth_m,
                pixels.intrinsics, pixels.world_from_camera,
            )
            self._populate_raw_frame(observation, frame, state)
        return observation

    def _populate_raw_frame(self, observation, frame, state):
        if "_self_filter_joint_positions" not in state:
            raise ValueError("raw RGB-D requires same capture robot joints for self filtering")
        filtered = self._self_filter.filter(
            frame, state["_self_filter_joint_positions"], state["base_pose"],
            joints_observed_at_unix_ms=state.get("_self_filter_observed_at_unix_ms"),
        )
        # Both transforms belong to this capture, including during motion.
        # Chassis axes already are ROS forward/left/up; its world heading
        # is represented by base_pose and must not be applied twice.
        world_from_base = np.eye(4)
        rotation = np.empty(9)
        mujoco.mju_quat2Mat(rotation, np.asarray(state["base_pose"][3:], dtype=float))
        world_from_base[:3, :3] = rotation.reshape(3, 3)
        world_from_base[:3, 3] = state["base_pose"][:3]
        base_from_camera = np.linalg.solve(world_from_base, frame.world_from_camera)
        observation.rgbd_frame.CopyFrom(robot_pb2.RGBDFrame(
            width=frame.rgb.shape[1], height=frame.rgb.shape[0],
            rgb=np.asarray(frame.rgb, dtype=np.uint8).tobytes(order="C"),
            depth_metres_f32=np.asarray(frame.depth_m, dtype="<f4").tobytes(order="C"),
            intrinsics=np.asarray(frame.intrinsics).reshape(-1).tolist(),
            base_from_camera=base_from_camera.reshape(-1).tolist(),
            robot_self_mask=filtered.mask.tobytes(order="C"),
            self_filter_model_revision=filtered.model_revision,
        ))

    def _head_sensor_capture(self):
        with self._capture_lock:
            if self.world.lock.acquire(blocking=False):
                try:
                    self.world._publish_sensor_snapshot()
                finally:
                    self.world.lock.release()
            data, state, captured_at = self.world.sensor_snapshot
            base = copy.deepcopy(state["base_pose"])
            joints = copy.deepcopy(state["_self_filter_joint_positions"])
            joint_stamp = state["_self_filter_observed_at_unix_ms"]
            pixels = self.renderer.render_rgbd(self.world.model, data)
            stamps = (captured_at, pixels.captured_at_unix_ms)
            now = int(time.time()*1000)
            if any(type(stamp) is not int or stamp > now for stamp in stamps):
                raise ValueError("raw camera capture is invalid or future dated")
            self._sequence += 1
            frame = RgbdFrame(
                self._robot_id, f"{self._robot_id}/head-rgbd", "head_depth_optical",
                "head-mount-fixed-v1", min(stamps), self._sequence,
                pixels.rgb, pixels.depth_m, pixels.intrinsics, pixels.world_from_camera,
            )
            validate_frame(frame)
            return NavigationCapture(frame, base, joints, joint_stamp)

    def _sensor_observation(self, source):
        # Explicit raw consumers (SLAM) need neither semantic inference nor
        # display PNGs/point-cloud JSON. Keep original calibrated measurements;
        # empty derived geometry means unknown, never inferred free space.
        capture = (self.navigation.capture_with_state() if source.endswith("/base-rgbd")
                   else self._head_sensor_capture())
        frame = capture.frame
        scene = Reconstruction(
            schema_version="scene.reconstruction.v1", robot_id=self._robot_id,
            observation_id=f"{frame.source_id}-{frame.sequence}-{frame.captured_at_unix_ms}",
            source_id=frame.source_id, source_type="rgbd_camera", source_frame_id=frame.frame_id,
            frame_id="world", transform_revision=frame.transform_revision,
            observed_at_unix_ms=frame.captured_at_unix_ms, sequence=frame.sequence, units="m",
        )
        observation = robot_pb2.Observation(
            observation_id=scene.observation_id, wall_time_unix_ms=frame.captured_at_unix_ms,
            monotonic_time_ns=time.monotonic_ns(), reconstruction=scene.to_wire(),
            robot_state={"base_pose": capture.base_pose, "simulation": True},
        )
        self._populate_raw_frame(observation, frame, {
            "base_pose": capture.base_pose, "_self_filter_joint_positions": capture.joint_positions,
            "_self_filter_observed_at_unix_ms": capture.joints_observed_at_unix_ms,
        })
        return observation

    def _dispatch(self, command, active=None):
        self.world.verification_capture = None
        if command.skill == "navigation.navigate":
            parameters = MessageToDict(command.parameters)
            try:
                validate_tool_parameters(command.skill, parameters, RobotProfile.model_validate(self._profile_wire))
            except ValueError as exc:
                return ToolResult(False, "TOOL_PARAMETERS_INVALID", str(exc), 0.0)
            if self._navigation_client is None:
                result = self.navigation.navigate(parameters["goalPose"], active.cancel_event if active else None)
            else:
                result = self._navigate_with_rtabmap(command, parameters["goalPose"], active)
            frame = result.payload.get("capture")
            if frame is not None:
                evidence = self._base_observation((frame, result.payload["base_pose"]))
                if "verification" in result.payload:
                    evidence.robot_state.update({"navigation": result.payload["verification"]})
                with self._capture_lock:
                    self._command_evidence[(command.command_id, command.idempotency_key)] = evidence
            return result
        self.capture_scene()  # Sensor failure cannot fall through to world truth.
        if command.skill in {"manipulation.pick", "manipulation.place"}:
            support = self.perception._support_z
            if support is None or abs(support - 0.73) > 0.015:
                return ToolResult(
                    False,
                    "WORKCELL_CALIBRATION_MISMATCH",
                    "reference controller requires its commissioned table height",
                    0.0,
                )
        result = super()._dispatch(command, active)
        capture = self.world.verification_capture
        if capture is not None:
            evidence = self._observation_from_capture(capture)
        elif result.success:
            evidence = self._observation_from_capture(self.capture_scene())
        else:
            evidence = None
        if evidence is not None:
            with self._capture_lock:
                self._command_evidence[(command.command_id, command.idempotency_key)] = evidence
        return result

    def _navigate_with_rtabmap(self, command, goal_pose, active):
        cancel = active.cancel_event if active is not None else threading.Event()
        deadline = min(command.deadline_unix_ms, int(time.time()*1000) + command.lease_ms)
        # Use the same receipt tolerances for preparation and final verification.
        # A previously accepted residual must not force an unplanned arm motion
        # before the next object. Proximity only permits requesting a fresh
        # RTAB confirmation; every nonzero velocity still requires verified stow.
        position_tolerance_m, yaw_tolerance_rad = .015, .04

        def yaw(pose):
            w, x, y, z = pose[3:]
            return math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))

        def yaw_error(pose):
            delta = yaw(pose) - yaw(goal_pose)
            return abs(math.atan2(math.sin(delta), math.cos(delta)))

        before = self.world.robot_state()["base_pose"]
        needs_motion = (np.linalg.norm(np.asarray(before[:3])-goal_pose[:3]) > position_tolerance_m
                        or yaw_error(before) > yaw_tolerance_rad)
        preparation = (self.world.prepare_navigation(cancel, deadline) if needs_motion
                       else ToolResult(True, "NAV_POSE_WITHIN_TOLERANCE",
                                       "awaiting fresh RTAB goal confirmation"))

        def guarded_velocity(vx, vy, wz, dt, **kwargs):
            if int(time.time()*1000) >= deadline:
                self.navigation.stop()
                return ToolResult(False, "NAV_DEADLINE_EXCEEDED", confidence=0.0)
            if any(value != 0 for value in (vx, vy, wz)):
                with self.world.lock:
                    stow = self.world._verify_navigation_stow("NAV_ARMS_STOWED")
                if not stow.success or self.world._held is not None:
                    self.navigation.stop()
                    return ToolResult(False, "NAV_STOW_REQUIRED", "nonzero velocity requires empty stowed arms", 0.0)
            return self.navigation.apply_velocity(vx, vy, wz, dt, **kwargs)

        try:
            result = (self._navigation_client.navigate(
                command.command_id, goal_pose, deadline, cancel,
                guarded_velocity, self.navigation.stop,
            ) if preparation.success else preparation)
        finally:
            self.navigation.stop()
        frame, base_pose = self.navigation.capture()
        validate_frame(frame)
        distance = float(np.linalg.norm(np.asarray(base_pose[:3]) - np.asarray(goal_pose[:3])))

        angle = yaw_error(base_pose)
        if result.success and int(time.time()*1000) >= deadline:
            result = ToolResult(False, "NAV_DEADLINE_EXCEEDED", "navigation receipt arrived after its lease", 0.0, result.payload)
        elif result.success and (distance > position_tolerance_m or angle > yaw_tolerance_rad):
            result = ToolResult(False, "NAV_ODOM_GOAL_NOT_REACHED",
                                "actual base pose does not match the requested odometry goal", 0.0, result.payload)
        verification = {
            "kind": "navigation.navigate", "passed": result.success,
            "pose_source": "sim_proprioceptive_odom", "base_pose": list(base_pose),
            "goal_pose": list(goal_pose), "position_error_m": distance, "yaw_error_rad": angle,
            "observed_at_unix_ms": frame.captured_at_unix_ms, "source_id": frame.source_id,
            "map_receipt": copy.deepcopy(result.payload),
            "preparation": {"code": preparation.code, **preparation.payload},
        }
        self._navigation_last_result = {"code": result.code, "message": result.message, **verification}
        return ToolResult(result.success, result.code, result.message, result.confidence,
                          {"capture": frame, "base_pose": list(base_pose), "verification": verification})

    def _recover_cancelled_command(self, command, active):
        if command.skill != "navigation.navigate":
            super()._recover_cancelled_command(command, active)

    def EmergencyStop(self, request, context):
        result = super().EmergencyStop(request, context)
        with self._commands_lock:
            for active in self._active_commands.values():
                active.cancel_event.set()
        return result

    def close(self):
        super().close()
        self.navigation.close()
        self._self_filter.close()

    def _event(self, command, sequence, event_type, code, message, progress=0.0, confidence=0.0):
        event = super()._event(command, sequence, event_type, code, message, progress, confidence)
        if event_type in {robot_pb2.SKILL_EVENT_SUCCEEDED, robot_pb2.SKILL_EVENT_FAILED,
                          robot_pb2.SKILL_EVENT_CANCELLED, robot_pb2.SKILL_EVENT_SAFETY_STOPPED}:
            with self._capture_lock:
                evidence = self._command_evidence.pop((command.command_id, command.idempotency_key), None)
            if evidence is not None:
                event.observation_id = evidence.observation_id
                event.evidence_observation.CopyFrom(evidence)
        return event
