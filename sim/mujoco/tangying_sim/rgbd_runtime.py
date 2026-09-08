"""Single robot reference loop driven only by head RGB-D environment evidence."""

from __future__ import annotations

import copy
import threading
import time

import mujoco
from tangying_robot_gateway.contracts import RobotProfile
from tangying_robot_gateway.rgbd import RgbdFrame, validate_frame
from tangying_robot_gateway.rgbd_images import encode_depth_preview
from tangying_robot_proto.robot.v1 import robot_pb2

from .rendering import SceneRenderer, _encode_png
from .rgbd_perception import TabletopRgbdPerception
from .server import RobotRuntimeService
from .tools import ToolResult
from .world import SceneEntity, TabletopWorld


class RgbdTabletopWorld(TabletopWorld):
    """Physics still owns contact/attachment; goals and verification use vision."""

    def __init__(self, *args, **kwargs):
        self.capture_scene = None
        super().__init__(*args, **kwargs)
        # A deliberately commissioned two-object workcell. Other fixtures are
        # outside the work volume and must never appear through truth fallback.
        for name, _, joint, _, _ in self._OBJECT_SPECS:
            if name not in {"red-cup", "blue-bottle"}:
                self._set_free_body_position(joint, (10.0, 10.0, -2.0))
        camera = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, "head_depth")
        self.model.cam_mode[camera] = mujoco.mjtCamLight.mjCAMLIGHT_FIXED
        # Fixed calibrated head mount; never tracks a targetbody/world object.
        self.model.cam_quat[camera] = [0.668536, 0.230346, -0.230346, -0.668536]
        mujoco.mj_forward(self.model, self.data)
        self._publish_sensor_snapshot()

    def _publish_sensor_snapshot(self):
        self.sensor_snapshot = (copy.copy(self.data), self.robot_state(), int(time.time() * 1000))

    def _refresh_observation_cache(self):
        self._publish_sensor_snapshot()

    def _step(self, count):
        super()._step(count)
        self._publish_sensor_snapshot()

    def _increment_step_count(self):
        super()._increment_step_count()
        self._publish_sensor_snapshot()

    def _follow_attachment(self, joint_name, arm):
        super()._follow_attachment(joint_name, arm)
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
        target = (-0.2 if arm == "left" else 0.2, 0.4, 0.9)
        self.motion.approach_body(
            arm,
            {"left": "Fixed_Jaw_2", "right": "Fixed_Jaw"}[arm],
            target,
            on_step=lambda _: self._follow_attachment(joint, arm),
            cancel_event=cancel_event,
        )

    def verify_grasp(self, entity_id):
        return self._verify_relation(entity_id, f"held_by:{self.robot_id}", "GRASP_NOT_OBSERVED")

    def verify_inside(self, entity_id, destination_id):
        return self._verify_relation(
            entity_id, f"inside:{destination_id}", "PLACEMENT_NOT_OBSERVED"
        )

    def _verify_relation(self, entity_id, expected, failure):
        first = next((e for e in self.entities() if e.entity_id == entity_id), None)
        found = next((e for e in self.entities() if e.entity_id == entity_id), None)
        success = (
            first is not None
            and found is not None
            and first.relation == expected
            and found.relation == expected
        )
        self._verification_confidence = found.confidence if success else 0.0
        return ToolResult(
            success, "OK" if success else failure, confidence=self._verification_confidence
        )


class RgbdRuntimeService(RobotRuntimeService):
    def __init__(self, world: RgbdTabletopWorld, robot_id="xlerobot-mujoco-tabletop", **kwargs):
        super().__init__(world, robot_id, cameras=("head-rgbd",), **kwargs)
        self.renderer.close()
        self.renderer = SceneRenderer(
            camera="head_depth", width=self._render_width, height=self._render_height
        )
        self.perception = TabletopRgbdPerception()
        self._capture_lock = threading.RLock()
        self._sequence = int(time.time() * 1000) * 1000
        self._last_scene = None
        self.world.capture_scene = self.capture_scene
        self.GetRuntimeInfo(None, None)  # Static morphology must never wait for a moving robot.

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
        profile = RobotProfile.model_validate(
            {
                "schemaVersion": "robot.profile.v1",
                "robotId": info.robot_id,
                "adapterId": info.adapter,
                "adapterVersion": info.adapter_version,
                "modelId": "xlerobot-rgbd-reference",
                "embodiment": "dual_arm",
                "joints": joints,
                "endEffectors": [],
                "sensors": [
                    {
                        "sourceId": f"{info.robot_id}/head-rgbd",
                        "sourceType": "rgbd_camera",
                        "frameId": "head_depth_optical",
                        "transformRevision": "head-mount-fixed-v1",
                        "maxAgeMs": 2000,
                    }
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
            }
            return scene, pixels, public

    def _observation(self):
        scene, pixels, state = self.capture_scene()
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
        observation.robot_state.update(state)
        observation.reconstruction.update(scene.to_wire())
        return observation

    def _dispatch(self, command, active=None):
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
        return super()._dispatch(command, active)

    def _event(self, command, sequence, event_type, code, message, progress=0.0, confidence=0.0):
        event = super()._event(command, sequence, event_type, code, message, progress, confidence)
        if self._last_scene is not None and event_type == robot_pb2.SKILL_EVENT_SUCCEEDED:
            event.observation_id = self._last_scene.observation_id
        return event
