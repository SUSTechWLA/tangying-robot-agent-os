from __future__ import annotations

import random
from dataclasses import dataclass

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


class TabletopWorld:
    # entity_id, body, free joint, category, color, legacy position (metadata only)
    _OBJECT_SPECS = (
        ("red-cup", "red_cup", "red_cup_free", "cup", "red", (-0.18, 0.08, 0.69)),
        ("blue-cup", "blue_cup", "blue_cup_free", "cup", "blue", (-0.06, 0.08, 0.69)),
        ("green-cup", "green_cup", "green_cup_free", "cup", "green", (0.08, 0.08, 0.69)),
        ("red-bottle", "red_bottle", "red_bottle_free", "bottle", "red", (-0.22, -0.02, 0.69)),
        ("blue-bottle", "blue_bottle", "blue_bottle_free", "bottle", "blue", (-0.08, -0.06, 0.69)),
        ("green-bottle", "green_bottle", "green_bottle_free", "bottle", "green", (0.10, -0.06, 0.69)),
        ("red-block", "red_block", "red_block_free", "block", "red", (-0.20, -0.16, 0.69)),
        ("blue-block", "blue_block", "blue_block_free", "block", "blue", (-0.02, -0.16, 0.69)),
        ("green-block", "green_block", "green_block_free", "block", "green", (0.16, -0.16, 0.69)),
    )
    _DUPLICATE_RED_CUP = ("red-cup-2", "red_cup_2", "red_cup_2_free", "cup", "red", (0.20, -0.14, 0.69))

    def __init__(self, seed: int, duplicate_red_cup: bool = False):
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
        for entity_id, _body_name, joint_name, _category, _color, _position in self._OBJECT_SPECS:
            self._pickable_joints[entity_id] = joint_name
        if duplicate_red_cup:
            self._pickable_joints["red-cup-2"] = "red_cup_free"
        self.motion = MotionController(self.model, self.data)
        self.tools = default_tool_registry()
        self._step(5)

    @classmethod
    def seeded(cls, seed: int, duplicate_red_cup: bool = False) -> TabletopWorld:
        return cls(seed=seed, duplicate_red_cup=duplicate_red_cup)

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

    def entities(self) -> list[SceneEntity]:
        entities = [
            self._body_entity(entity_id, body_name, category, {"color": color})
            for entity_id, body_name, _joint_name, category, color, _position in self._OBJECT_SPECS
        ]
        if self._duplicate_red_cup:
            entities.append(
                self._body_entity("red-cup-2", "red_cup", "cup", {"color": "red"})
            )
        entities.extend(
            [
                SceneEntity(
                    entity_id="left-bin",
                    category="storage_bin",
                    attributes={"color": "orange"},
                    relation="left_side",
                    confidence=0.99,
                    position=(-0.25, 0.0, 0.69),
                ),
                SceneEntity(
                    entity_id="right-bin",
                    category="storage_bin",
                    attributes={"color": "blue"},
                    relation="right_side",
                    confidence=0.99,
                    position=(0.25, 0.0, 0.69),
                ),
                SceneEntity(
                    entity_id="front-tray",
                    category="delivery_tray",
                    attributes={"color": "gray"},
                    relation="front_side",
                    confidence=0.99,
                    position=(0.0, 0.18, 0.69),
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
                "left": self._body_position("Fixed_Jaw_2"),
                "right": self._body_position("Fixed_Jaw"),
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

    def joint_positions(self) -> dict[str, float]:
        positions: dict[str, float] = {}
        for suffix in ("L", "R"):
            for stem in ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll", "Jaw"):
                name = f"{stem}_{suffix}"
                joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
                address = int(self.model.jnt_qposadr[joint_id])
                positions[name] = float(self.data.qpos[address])
        return positions

    def set_active_arm(self, arm: str, target: str = "") -> None:
        self._active_arm = arm
        if target:
            self._target = target

    def select_arm(self, entity_id: str, destination_id: str = "") -> str | None:
        if entity_id not in self._pickable_joints:
            return None
        source = next((item.position for item in self.entities() if item.entity_id == entity_id), None)
        if source is None:
            return None
        destinations = [source]
        destination = self._destination_target(destination_id) if destination_id else None
        if destination is not None:
            destination_body = self._destination_body(destination_id)
            destinations.append(self._body_position(destination_body))
        shoulders = {"left": "Rotation_Pitch_R", "right": "Rotation_Pitch"}
        reach = {
            arm: max(
                float(np.linalg.norm(np.asarray(point) - np.asarray(self._body_position(body))))
                for point in destinations
            )
            for arm, body in shoulders.items()
        }
        reachable = {arm: distance for arm, distance in reach.items() if distance <= 0.46}
        return min(reachable, key=reachable.get) if reachable else min(reach, key=reach.get)

    def pick(self, entity_id: str) -> ActionResult:
        if self._held is not None:
            return ActionResult(False, "GRIPPER_OCCUPIED", "another object is already held")
        joint = self._pickable_joints.get(entity_id)
        if joint is None:
            return ActionResult(False, "OBJECT_NOT_FOUND", entity_id)
        arm = (
            self._active_arm
            if self._active_arm is not None and self._target == entity_id
            else self.select_arm(entity_id)
        )
        if arm is None:
            return ActionResult(False, "OBJECT_NOT_REACHABLE", entity_id)
        self.set_active_arm(arm, entity_id)
        self._move_named(arm, "PRE_GRASP", steps=12)
        self._move_named(arm, "OPEN", steps=4)
        self._grippers[arm] = "open"
        position = self._joint_position(joint)
        self._move_named(arm, "CLOSED", steps=6)
        self._grippers[arm] = "closed"
        self._held = entity_id
        lift = (float(position[0]), float(position[1]), float(position[2] + 0.14))
        self._move_named(
            arm,
            "LIFT",
            steps=12,
            on_step=self._attachment_interpolator(joint, position, lift),
        )
        self.pick_count += 1
        return ActionResult(True)

    def place(self, destination_id: str) -> ActionResult:
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
            return ActionResult(False, "DESTINATION_NOT_REACHABLE", destination_id)
        self.set_active_arm(arm, destination_id)
        start = self._joint_position(joint)
        carry = (target[0], target[1], 0.90)
        self._move_named(
            arm,
            "PLACE",
            steps=16,
            on_step=self._attachment_interpolator(joint, start, carry),
        )
        self._move_named(arm, "OPEN", steps=6)
        self._grippers[arm] = "open"
        placed_entity = self._held
        self._set_free_body_position(joint, (target[0], target[1], 0.82))
        self._held = None
        self._placements[placed_entity] = destination_id
        mujoco.mj_forward(self.model, self.data)
        return ActionResult(True)

    def verify_grasp(self, entity_id: str) -> ActionResult:
        success = self._held == entity_id
        self._verification_confidence = 0.98 if success else 0.2
        return ActionResult(
            success,
            "OK" if success else "GRASP_LOST",
            confidence=self._verification_confidence,
        )

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

    def recover_to_safe_pose(self, arm: str | None = None) -> ActionResult:
        arms = (arm,) if arm else ((self._active_arm,) if self._active_arm else ("left", "right"))
        if any(candidate not in {"left", "right"} for candidate in arms):
            return ActionResult(False, "ARM_NOT_FOUND", str(arm))
        for candidate in arms:
            self._move_named(candidate, "OPEN", steps=4)
            self._move_named(candidate, "HOME", steps=12)
            self._grippers[candidate] = "open"
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
        self, entity_id: str, body_name: str, category: str, attributes: dict[str, str]
    ) -> SceneEntity:
        body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        position = tuple(float(value) for value in self.data.xpos[body_id])
        return SceneEntity(entity_id, category, attributes, "", 0.98, position)

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

    def _move_named(self, arm, name, *, steps, on_step=None):
        def advance(progress):
            if on_step is not None:
                on_step(progress)
            self.step_count += 1

        return self.motion.move_named(arm, name, steps=steps, on_step=advance)

    def _attachment_interpolator(self, joint_name, start, destination):
        start_array = np.asarray(start, dtype=float)
        destination_array = np.asarray(destination, dtype=float)

        def update(progress):
            position = start_array + (destination_array - start_array) * progress
            self._set_free_body_position(joint_name, tuple(float(value) for value in position))

        return update

    def _step(self, count: int) -> None:
        for _ in range(count):
            mujoco.mj_step(self.model, self.data)
            self.step_count += 1
