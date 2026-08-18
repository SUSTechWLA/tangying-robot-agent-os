from __future__ import annotations

import random
from dataclasses import dataclass
from functools import wraps
from threading import Event, RLock

import mujoco
import numpy as np

from .model import MODEL_REVISION, load_task_model, validate_task_model
from .motion import MotionController
from .tools import ToolResult, default_tool_registry


@dataclass(frozen=True)
class SceneEntity:
    entity_id: str
    category: str
    attributes: dict[str, str]
    relation: str
    confidence: float
    position: tuple[float, float, float]


ActionResult = ToolResult


def _synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)

    return locked


class TabletopWorld:
    GRASP_TOLERANCE = 0.055
    ATTACHMENT_OFFSET = (0.0, 0.0, -0.04)
    ARM_REACH = 0.42
    PLACEMENT_TOLERANCE = 0.08
    PLACEMENT_XY_TOLERANCE = 0.02
    PLACEMENT_HEIGHT = 0.82
    # entity_id, body, free joint, category, color. Poses always come from MuJoCo.
    _OBJECT_SPECS = (
        ("red-cup", "red_cup", "red_cup_free", "cup", "red"),
        ("blue-cup", "blue_cup", "blue_cup_free", "cup", "blue"),
        ("green-cup", "green_cup", "green_cup_free", "cup", "green"),
        ("red-bottle", "red_bottle", "red_bottle_free", "bottle", "red"),
        ("blue-bottle", "blue_bottle", "blue_bottle_free", "bottle", "blue"),
        ("green-bottle", "green_bottle", "green_bottle_free", "bottle", "green"),
        ("red-block", "red_block", "red_block_free", "block", "red"),
        ("blue-block", "blue_block", "blue_block_free", "block", "blue"),
        ("green-block", "green_block", "green_block_free", "block", "green"),
    )
    _DUPLICATE_RED_CUP = (
        "red-cup-2",
        "red_cup_2",
        "red_cup_2_free",
        "cup",
        "red",
        (0.19, 0.43, 0.80),
    )

    def __init__(self, seed: int, duplicate_red_cup: bool = False):
        self.lock = RLock()
        self.model = load_task_model()
        validate_task_model(self.model)
        self.data = mujoco.MjData(self.model)
        self._random = random.Random(seed)
        self._seed = seed
        self._duplicate_red_cup = duplicate_red_cup
        self._held: str | None = None
        self._active_arm: str | None = None
        self._target = ""
        self._placements: dict[str, str] = {}
        self._verification_confidence = 0.0
        self._grippers = {"left": "open", "right": "open"}
        self.episode = 1
        self.step_count = 0
        self.pick_count = 0
        self._pickable_joints: dict[str, str] = {}
        for entity_id, _body_name, joint_name, _category, _color in self._OBJECT_SPECS:
            self._pickable_joints[entity_id] = joint_name
        if duplicate_red_cup:
            self._pickable_joints["red-cup-2"] = "red_cup_2_free"
            self._set_free_body_position("red_cup_2_free", self._DUPLICATE_RED_CUP[-1])
        self.motion = MotionController(self.model, self.data)
        self.tools = default_tool_registry()
        self._step(5)

    @classmethod
    def seeded(cls, seed: int, duplicate_red_cup: bool = False) -> TabletopWorld:
        return cls(seed=seed, duplicate_red_cup=duplicate_red_cup)

    @_synchronized
    def reset(self) -> TabletopWorld:
        """Reset an episode without recompiling the 19 MB pinned model."""
        self.data = mujoco.MjData(self.model)
        self.motion = MotionController(self.model, self.data)
        self._held = None
        self._active_arm = None
        self._target = ""
        self._placements.clear()
        self._verification_confidence = 0.0
        self._grippers = {"left": "open", "right": "open"}
        self.step_count = 0
        self.pick_count = 0
        self.episode += 1
        if self._duplicate_red_cup:
            self._set_free_body_position("red_cup_2_free", self._DUPLICATE_RED_CUP[-1])
        self._step(5)
        return self

    def resolve(self, *, category: str, color: str = "", relation: str = "") -> SceneEntity:
        matches = self.resolve_all(category=category, color=color, relation=relation)
        if len(matches) != 1:
            raise ValueError(f"expected one entity, found {len(matches)}")
        return matches[0]

    def resolve_all(
        self, *, category: str, color: str = "", relation: str = ""
    ) -> list[SceneEntity]:
        entities = self.entities()
        return [
            entity
            for entity in entities
            if entity.category == category
            and (not color or entity.attributes.get("color") == color)
            and (not relation or entity.relation == relation)
        ]

    @_synchronized
    def entities(self) -> list[SceneEntity]:
        entities = [
            self._body_entity(entity_id, body_name, category, {"color": color})
            for entity_id, body_name, _joint_name, category, color in self._OBJECT_SPECS
        ]
        if self._duplicate_red_cup:
            entities.append(
                self._body_entity("red-cup-2", "red_cup_2", "cup", {"color": "red"})
            )
        entities.extend(
            [
                self._body_entity(
                    "left-bin",
                    "left_bin",
                    "storage_bin",
                    {"color": "orange"},
                    relation="left_side",
                    confidence=0.99,
                ),
                self._body_entity(
                    "right-bin",
                    "right_bin",
                    "storage_bin",
                    {"color": "blue"},
                    relation="right_side",
                    confidence=0.99,
                ),
                self._body_entity(
                    "front-tray",
                    "front_tray",
                    "delivery_tray",
                    {"color": "gray"},
                    relation="front_side",
                    confidence=0.99,
                ),
            ]
        )
        entities.extend(
            [
                self._body_entity("xlerobot", "chassis", "robot", {"model": "XLeRobot"}),
                self._body_entity("table", "table", "work_surface", {}),
                SceneEntity("floor", "environment", {}, "under", 1.0, (0.0, 0.0, 0.0)),
            ]
        )
        return entities

    @_synchronized
    def robot_state(self) -> dict[str, object]:
        chassis_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "chassis")
        base_pose = [
            *(float(value) for value in self.data.xpos[chassis_id]),
            *(float(value) for value in self.data.xquat[chassis_id]),
        ]
        return {
            "model_revision": MODEL_REVISION,
            "base_pose": base_pose,
            "joint_positions": self.joint_positions(),
            "grippers": dict(self._grippers),
            "held": self._held or "",
            "active_tool": f"{self._active_arm}_arm" if self._active_arm else "",
            "target": self._target,
            "end_effectors": {
                "left": self.end_effector_position("left"),
                "right": self.end_effector_position("right"),
            },
            "reward": float(len(self._placements)),
            "episode": self.episode,
            "verification_confidence": self._verification_confidence,
            "placements": dict(self._placements),
            "step_count": self.step_count,
            "pick_count": self.pick_count,
            "simulation": True,
        }

    @property
    def active_arm(self) -> str:
        return self._active_arm or ""

    @_synchronized
    def joint_positions(self) -> dict[str, float]:
        positions: dict[str, float] = {}
        for suffix in ("L", "R"):
            for stem in ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll", "Jaw"):
                name = f"{stem}_{suffix}"
                joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
                address = int(self.model.jnt_qposadr[joint_id])
                positions[name] = float(self.data.qpos[address])
        return positions

    @_synchronized
    def set_active_arm(self, arm: str, target: str = "") -> None:
        self._active_arm = arm
        if target:
            self._target = target

    def has_object(self, entity_id: str) -> bool:
        return entity_id in self._pickable_joints

    def has_destination(self, entity_id: str) -> bool:
        return self._destination_body(entity_id) is not None

    @_synchronized
    def end_effector_position(self, arm: str) -> tuple[float, float, float]:
        body_name = {"left": "Fixed_Jaw_2", "right": "Fixed_Jaw"}.get(arm)
        if body_name is None:
            raise ValueError(f"unknown arm: {arm}")
        return self._body_position(body_name)

    def select_arm(self, entity_id: str, destination_id: str = "") -> str | None:
        if entity_id not in self._pickable_joints:
            return None
        source = next((item.position for item in self.entities() if item.entity_id == entity_id), None)
        if source is None:
            return None
        shoulders = {"left": "Rotation_Pitch_R", "right": "Rotation_Pitch"}
        source_reach = {
            arm: float(
                np.linalg.norm(np.asarray(source) - np.asarray(self._body_position(body)))
            )
            for arm, body in shoulders.items()
        }
        if destination_id:
            reachable = {
                arm: distance
                for arm, distance in source_reach.items()
                if distance <= self._mobile_reach()
                and self._arm_matches_destination(destination_id, arm)
            }
        else:
            reachable = {
                arm: distance
                for arm, distance in source_reach.items()
                if distance <= self._mobile_reach()
            }
        return min(reachable, key=reachable.get) if reachable else None

    def arm_can_reach(self, entity_id: str, arm: str) -> bool:
        source = next((item.position for item in self.entities() if item.entity_id == entity_id), None)
        shoulder = {"left": "Rotation_Pitch_R", "right": "Rotation_Pitch"}.get(arm)
        if source is None or shoulder is None:
            return False
        distance = np.linalg.norm(
            np.asarray(source) - np.asarray(self._body_position(shoulder))
        )
        return bool(distance <= self._mobile_reach())

    def arm_can_reach_destination(self, destination_id: str, arm: str) -> bool:
        body_name = self._destination_body(destination_id)
        shoulder = {"left": "Rotation_Pitch_R", "right": "Rotation_Pitch"}.get(arm)
        if body_name is None or shoulder is None:
            return False
        distance = np.linalg.norm(
            np.asarray(self._body_position(body_name))
            - np.asarray(self._body_position(shoulder))
        )
        return bool(
            self._arm_matches_destination(destination_id, arm)
            and distance <= self._mobile_reach()
        )

    @_synchronized
    def pick(self, entity_id: str, *, cancel_event: Event | None = None) -> ActionResult:
        if self._held is not None:
            return ActionResult(False, "GRIPPER_OCCUPIED", "another object is already held")
        joint = self._pickable_joints.get(entity_id)
        if joint is None:
            return ActionResult(False, "OBJECT_NOT_FOUND", entity_id)
        planned_arm = (
            self._active_arm if self._active_arm is not None and self._target == entity_id else None
        )
        arm = planned_arm or self.select_arm(entity_id)
        if planned_arm is not None and not self.arm_can_reach(entity_id, planned_arm):
            arm = None
        if arm is None:
            return ActionResult(False, "TARGET_UNREACHABLE", entity_id)
        self.set_active_arm(arm, entity_id)
        self._move_named(arm, "PRE_GRASP", steps=12, cancel_event=cancel_event)
        self._move_named(arm, "OPEN", steps=4, cancel_event=cancel_event)
        self._grippers[arm] = "open"
        object_position = tuple(float(value) for value in self._joint_position(joint))
        approach_target = tuple(
            float(value)
            for value in np.asarray(object_position) - np.asarray(self.ATTACHMENT_OFFSET)
        )
        jaw_body = {"left": "Fixed_Jaw_2", "right": "Fixed_Jaw"}[arm]
        approached = self.motion.approach_body(
            arm,
            jaw_body,
            approach_target,
            on_step=lambda _progress: self._increment_step_count(),
            cancel_event=cancel_event,
        )
        grasp_distance = np.linalg.norm(
            self._joint_position(joint) - np.asarray(self.end_effector_position(arm))
        )
        if not approached or grasp_distance > self.GRASP_TOLERANCE:
            return ActionResult(False, "GRASP_NOT_REACHED", entity_id, 0.0)
        self._move_named(arm, "CLOSED", steps=6, cancel_event=cancel_event)
        self._grippers[arm] = "closed"
        self._held = entity_id
        self._follow_attachment(joint, arm)
        self._move_named(
            arm,
            "LIFT",
            steps=12,
            on_step=lambda _progress: self._follow_attachment(joint, arm),
            cancel_event=cancel_event,
        )
        if not self.verify_grasp(entity_id).success:
            self._held = None
            return ActionResult(False, "GRASP_FAILED", entity_id, 0.0)
        self.pick_count += 1
        return ActionResult(True)

    @_synchronized
    def place(
        self, destination_id: str, *, cancel_event: Event | None = None
    ) -> ActionResult:
        if self._held is None:
            return ActionResult(False, "NOT_HOLDING_OBJECT", "pick must succeed before place")
        target = self._destination_target(destination_id)
        if target is None:
            return ActionResult(False, "DESTINATION_NOT_FOUND", destination_id)
        joint = self._pickable_joints.get(self._held)
        if joint is None:
            return ActionResult(False, "HELD_OBJECT_NOT_FOUND", self._held)
        arm = self._active_arm or self.select_arm(self._held, destination_id)
        if arm is None:
            return ActionResult(False, "TARGET_UNREACHABLE", destination_id)
        if not self.arm_can_reach_destination(destination_id, arm):
            return ActionResult(False, "TARGET_UNREACHABLE", destination_id)
        self.set_active_arm(arm, destination_id)
        destination_position = np.asarray(
            self._body_position(self._destination_body(destination_id))
        )
        desired_object_position = np.asarray(
            (destination_position[0], destination_position[1], self.PLACEMENT_HEIGHT)
        )
        approach_target = tuple(
            float(value)
            for value in desired_object_position - np.asarray(self.ATTACHMENT_OFFSET)
        )
        jaw_body = {"left": "Fixed_Jaw_2", "right": "Fixed_Jaw"}[arm]

        def advance_attachment(_progress):
            self._increment_step_count()
            self._follow_attachment(joint, arm)

        approached = self.motion.approach_body(
            arm,
            jaw_body,
            approach_target,
            on_step=advance_attachment,
            cancel_event=cancel_event,
        )
        object_position = self._joint_position(joint)
        xy_distance = np.linalg.norm(object_position[:2] - destination_position[:2])
        distance = np.linalg.norm(object_position - destination_position)
        if (
            not approached
            or xy_distance > self.PLACEMENT_XY_TOLERANCE
            or distance > self.PLACEMENT_TOLERANCE
        ):
            return ActionResult(False, "PLACE_NOT_REACHED", destination_id, 0.0)
        self._move_named(arm, "OPEN", steps=6, cancel_event=cancel_event)
        self._grippers[arm] = "open"
        placed_entity = self._held
        self._held = None
        self._placements[placed_entity] = destination_id
        self._step(10)
        return ActionResult(True)

    @_synchronized
    def verify_grasp(self, entity_id: str) -> ActionResult:
        arm = self._active_arm
        joint = self._pickable_joints.get(entity_id)
        jaw_closed = False
        distance = float("inf")
        if arm and joint:
            closed_target = next(iter(self.motion.target_for(arm, "CLOSED").values()))
            jaw_name = next(iter(self.motion.target_for(arm, "CLOSED")))
            jaw_closed = bool(
                self._grippers[arm] == "closed"
                and abs(self.joint_positions()[jaw_name] - closed_target) <= 1e-6
            )
            distance = float(
                np.linalg.norm(
                    self._joint_position(joint) - np.asarray(self.end_effector_position(arm))
                )
            )
        success = bool(
            self._held == entity_id
            and jaw_closed
            and distance <= self.GRASP_TOLERANCE
        )
        self._verification_confidence = 0.98 if success else 0.2
        return ActionResult(
            success,
            "OK" if success else "GRASP_LOST",
            confidence=self._verification_confidence,
        )

    @_synchronized
    def verify_inside(self, entity_id: str, destination_id: str) -> ActionResult:
        target = self._destination_target(destination_id)
        if target is None:
            return ActionResult(False, "DESTINATION_NOT_FOUND", destination_id, 0.0)
        entity = next((item for item in self.entities() if item.entity_id == entity_id), None)
        if entity is None:
            return ActionResult(False, "OBJECT_NOT_FOUND", entity_id, 0.0)
        distance = np.linalg.norm(np.asarray(entity.position[:2]) - np.asarray(target[:2]))
        success = bool(
            distance <= 0.09
            and entity.position[2] < 0.90
            and self._held != entity_id
            and self._placements.get(entity_id) == destination_id
        )
        self._verification_confidence = 0.97 if success else 0.35
        return ActionResult(
            success,
            "OK" if success else "PLACEMENT_NOT_VERIFIED",
            confidence=self._verification_confidence,
        )

    @_synchronized
    def recover_to_safe_pose(
        self, arm: str | None = None, *, cancel_event: Event | None = None
    ) -> ActionResult:
        if arm is not None and arm not in {"left", "right"}:
            return ActionResult(False, "ARM_NOT_FOUND", str(arm))
        for candidate in ("left", "right"):
            self._move_named(
                candidate, "OPEN", steps=4, cancel_event=cancel_event
            )
            if candidate == self._active_arm:
                self._held = None
            self._move_named(
                candidate, "HOME", steps=12, cancel_event=cancel_event
            )
            self._grippers[candidate] = "open"
        self.motion.home_base(
            steps=12,
            on_step=lambda _progress: self._increment_step_count(),
            cancel_event=cancel_event,
        )
        self._active_arm = None
        self._target = ""
        return ActionResult(True)

    def _destination_target(self, destination_id: str) -> tuple[float, float] | None:
        body_name = self._destination_body(destination_id)
        if body_name is None:
            return None
        position = self._body_position(body_name)
        return (position[0], position[1])

    @staticmethod
    def _destination_body(destination_id: str) -> str | None:
        return {
            "right-bin": "right_bin",
            "left-bin": "left_bin",
            "front-tray": "front_tray",
        }.get(destination_id)

    def _body_entity(
        self,
        entity_id: str,
        body_name: str,
        category: str,
        attributes: dict[str, str],
        *,
        relation: str = "",
        confidence: float = 0.98,
    ) -> SceneEntity:
        body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        position = tuple(float(value) for value in self.data.xpos[body_id])
        return SceneEntity(entity_id, category, attributes, relation, confidence, position)

    def _joint_position(self, joint_name: str) -> np.ndarray:
        joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        address = self.model.jnt_qposadr[joint_id]
        return self.data.qpos[address : address + 3].copy()

    def _body_position(self, body_name: str | None) -> tuple[float, float, float]:
        if body_name is None:
            raise ValueError("body name is required")
        body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        return tuple(float(value) for value in self.data.xpos[body_id])

    def _set_free_body_position(self, joint_name: str, position: tuple[float, float, float]) -> None:
        joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        address = self.model.jnt_qposadr[joint_id]
        self.data.qpos[address : address + 3] = position
        self.data.qpos[address + 3 : address + 7] = (1.0, 0.0, 0.0, 0.0)

    def _move_named(self, arm, name, *, steps, on_step=None, cancel_event=None):
        def advance(progress):
            if on_step is not None:
                on_step(progress)
            self.step_count += 1

        return self.motion.move_named(
            arm,
            name,
            steps=steps,
            on_step=advance,
            cancel_event=cancel_event,
        )

    def _increment_step_count(self) -> None:
        self.step_count += 1

    def _follow_attachment(self, joint_name: str, arm: str) -> None:
        position = np.asarray(self.end_effector_position(arm)) + np.asarray(
            self.ATTACHMENT_OFFSET
        )
        self._set_free_body_position(joint_name, tuple(float(value) for value in position))
        mujoco.mj_forward(self.model, self.data)

    def _mobile_reach(self) -> float:
        return self.ARM_REACH + np.sqrt(2.0) * self.motion.BASE_TRANSLATION_LIMIT

    @staticmethod
    def _arm_matches_destination(destination_id: str, arm: str) -> bool:
        required_arm = {"left-bin": "left", "right-bin": "right"}.get(destination_id)
        return required_arm is None or arm == required_arm

    def _step(self, count: int) -> None:
        for _ in range(count):
            mujoco.mj_step(self.model, self.data)
            self.step_count += 1
