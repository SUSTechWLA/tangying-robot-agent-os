from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np


@dataclass(frozen=True)
class SceneEntity:
    entity_id: str
    category: str
    attributes: dict[str, str]
    relation: str
    confidence: float
    position: tuple[float, float, float]


@dataclass(frozen=True)
class ActionResult:
    success: bool
    code: str = "OK"
    message: str = ""
    confidence: float = 1.0


class TabletopWorld:
    # entity_id, body, free joint, category, color, initial x/y/z
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
        model_path = Path(__file__).resolve().parents[1] / "assets" / "tabletop.xml"
        self.model = mujoco.MjModel.from_xml_path(str(model_path))
        self.data = mujoco.MjData(self.model)
        self._random = random.Random(seed)
        self._duplicate_red_cup = duplicate_red_cup
        self._held: str | None = None
        self.step_count = 0
        self.pick_count = 0
        self._pickable_joints: dict[str, str] = {}
        for entity_id, body_name, joint_name, _category, _color, position in self._OBJECT_SPECS:
            self._set_free_body_position(joint_name, position)
            self._pickable_joints[entity_id] = joint_name
        if duplicate_red_cup:
            _entity_id, _body_name, joint_name, _category, _color, position = self._DUPLICATE_RED_CUP
            self._set_free_body_position(joint_name, position)
            self._pickable_joints[_entity_id] = joint_name
        else:
            _, _, joint_name, _, _, position = self._DUPLICATE_RED_CUP
            self._set_free_body_position(joint_name, position)
        self._step(5)

    @classmethod
    def seeded(cls, seed: int, duplicate_red_cup: bool = False) -> TabletopWorld:
        return cls(seed=seed, duplicate_red_cup=duplicate_red_cup)

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
            entity_id, body_name, _joint_name, category, color, _position = self._DUPLICATE_RED_CUP
            entities.append(self._body_entity(entity_id, body_name, category, {"color": color}))
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
        return entities

    def pick(self, entity_id: str) -> ActionResult:
        if self._held is not None:
            return ActionResult(False, "GRIPPER_OCCUPIED", "another object is already held")
        joint = self._pickable_joints.get(entity_id)
        if joint is None:
            return ActionResult(False, "OBJECT_NOT_FOUND", entity_id)
        position = self._joint_position(joint)
        self._set_free_body_position(joint, (position[0], position[1], 0.92))
        self._held = entity_id
        self.pick_count += 1
        self._step(20)
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
        self._set_free_body_position(joint, (target[0], target[1], 0.72))
        self._held = None
        self._step(30)
        return ActionResult(True)

    def verify_grasp(self, entity_id: str) -> ActionResult:
        return ActionResult(self._held == entity_id, "OK" if self._held == entity_id else "GRASP_LOST", confidence=0.98)

    def verify_inside(self, entity_id: str, destination_id: str) -> ActionResult:
        target = self._destination_target(destination_id)
        if target is None:
            return ActionResult(False, "DESTINATION_NOT_FOUND", destination_id, 0.0)
        entity = next((item for item in self.entities() if item.entity_id == entity_id), None)
        if entity is None:
            return ActionResult(False, "OBJECT_NOT_FOUND", entity_id, 0.0)
        distance = np.linalg.norm(np.asarray(entity.position[:2]) - np.asarray(target[:2]))
        success = bool(distance <= 0.09 and entity.position[2] < 0.82)
        return ActionResult(success, "OK" if success else "PLACEMENT_NOT_VERIFIED", confidence=0.97 if success else 0.35)

    def _destination_target(self, destination_id: str) -> tuple[float, float] | None:
        if destination_id == "right-bin":
            return (0.25, 0.0)
        if destination_id == "left-bin":
            return (-0.25, 0.0)
        if destination_id == "front-tray":
            return (0.0, 0.18)
        return None

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

    def _set_free_body_position(self, joint_name: str, position: tuple[float, float, float]) -> None:
        joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        address = self.model.jnt_qposadr[joint_id]
        self.data.qpos[address : address + 3] = position
        self.data.qpos[address + 3 : address + 7] = (1.0, 0.0, 0.0, 0.0)

    def _step(self, count: int) -> None:
        for _ in range(count):
            mujoco.mj_step(self.model, self.data)
            self.step_count += 1
