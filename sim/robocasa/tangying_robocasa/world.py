"""One shared RoboCasa physics state exposed as two robot-local tool views."""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, RLock

import mujoco
import numpy as np
from tangying_sim.tools import ToolResult, default_tool_registry

from .composer import ComposedScene

WORLD_FRAME = "world"
TRANSFORM_REVISION = "robocasa-world-v1"


@dataclass(frozen=True, slots=True)
class RoboCasaEntity:
    entity_id: str
    category: str
    attributes: dict[str, str]
    relation: str
    confidence: float
    position: tuple[float, float, float]
    relations: dict[str, str] = field(default_factory=dict)
    frame_id: str = WORLD_FRAME
    transform_revision: str = TRANSFORM_REVISION


class RoboCasaSharedWorld:
    """Authoritative MuJoCo model, data and handoff ownership state.

    The two runtime endpoints never own simulator copies. Every mutation is
    serialized by ``lock`` and is immediately observable from both views.
    """

    OBJECT_ID = "red-block"
    RESOURCE_ID = "block:red-block"
    ZONES = ("left-start-zone", "handoff-zone", "right-target-zone")

    def __init__(
        self,
        scene: ComposedScene,
        model: mujoco.MjModel,
        *,
        seed: int,
        human_speed: float = 0.0,
    ) -> None:
        self.lock = RLock()
        self.scene = scene
        self.model = model
        self.data = mujoco.MjData(model)
        self.seed = seed
        self.human_speed = human_speed
        self.episode = 1
        self.step_count = 0
        self.sequence = 0
        self.owner = "robot-1"
        self.custodian = "robot-1"
        self.fencing_token = 1
        self.completed = False
        self.held_by = ""
        self.placement = "left-start-zone"
        self.pick_counts = {"robot-1": 0, "robot-2": 0}
        self.source_sequences = {"robot-1": 0, "robot-2": 0, "environment": 0}
        self._grant_listeners: list[Callable[[str, int], None]] = []
        self._cached_entities: tuple[RoboCasaEntity, ...] = ()
        self._cached_render_data = mujoco.MjData(model)
        self._set_block_at_zone("left-start-zone")
        mujoco.mj_forward(self.model, self.data)
        self._refresh_cache()

    @classmethod
    def from_scene(
        cls, scene: ComposedScene, seed: int, human_speed: float = 0.0
    ) -> RoboCasaSharedWorld:
        model = mujoco.MjModel.from_xml_string(scene.xml)
        return cls(scene, model, seed=seed, human_speed=human_speed)

    def reset(self) -> RoboCasaSharedWorld:
        with self.lock:
            # Runtime views keep a direct reference to MjData for rendering and
            # gRPC observations, so reset in place instead of replacing it.
            mujoco.mj_resetData(self.model, self.data)
            self.episode += 1
            self.step_count = 0
            self.sequence = 0
            self.owner = "robot-1"
            self.custodian = "robot-1"
            self.fencing_token = 1
            self.completed = False
            self.held_by = ""
            self.placement = "left-start-zone"
            self.pick_counts = {"robot-1": 0, "robot-2": 0}
            self.source_sequences = {"robot-1": 0, "robot-2": 0, "environment": 0}
            self._set_block_at_zone("left-start-zone")
            mujoco.mj_forward(self.model, self.data)
            self._refresh_cache()
        return self

    def command_grant(self) -> tuple[str, int]:
        with self.lock:
            owner = self.owner
            if owner == "environment" and self.custodian == "robot-2" and not self.completed:
                owner = "robot-2"
            return owner, self.fencing_token

    def add_grant_listener(self, listener: Callable[[str, int], None]) -> None:
        with self.lock:
            self._grant_listeners.append(listener)

    def adopt_fencing_token(self, token: int) -> None:
        """Advance to a durable cloud token after a simulator restart.

        Adoption is monotonic and ownership is still validated by the Runtime;
        this mirrors restoring a persisted grant journal on real hardware.
        """
        with self.lock:
            self.fencing_token = max(self.fencing_token, token)

    def _notify_grant(self) -> None:
        owner, token = self.command_grant()
        for listener in tuple(self._grant_listeners):
            listener(owner, token)

    def pick_count(self, robot_id: str) -> int:
        with self.lock:
            return self.pick_counts[robot_id]

    def _object_position(self) -> tuple[float, float, float]:
        joint_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_JOINT, "red-block-joint"
        )
        address = int(self.model.jnt_qposadr[joint_id])
        return tuple(float(value) for value in self.data.qpos[address : address + 3])

    def _set_block_position(self, position: tuple[float, float, float]) -> None:
        joint_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_JOINT, "red-block-joint"
        )
        qpos_address = int(self.model.jnt_qposadr[joint_id])
        dof_address = int(self.model.jnt_dofadr[joint_id])
        self.data.qpos[qpos_address : qpos_address + 3] = position
        self.data.qpos[qpos_address + 3 : qpos_address + 7] = (1.0, 0.0, 0.0, 0.0)
        self.data.qvel[dof_address : dof_address + 6] = 0.0

    def _zone_position(self, zone: str) -> tuple[float, float, float]:
        site_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, zone)
        if site_id < 0:
            raise ValueError(f"unknown RoboCasa zone: {zone}")
        position = self.data.site_xpos[site_id]
        return (float(position[0]), float(position[1]), float(position[2] + 0.045))

    def _set_block_at_zone(self, zone: str) -> None:
        mujoco.mj_forward(self.model, self.data)
        self._set_block_position(self._zone_position(zone))

    def _joint_address(self, name: str) -> tuple[int, int]:
        joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint_id < 0:
            raise ValueError(f"unknown joint: {name}")
        return int(self.model.jnt_qposadr[joint_id]), int(self.model.jnt_dofadr[joint_id])

    def _animate_joints(
        self,
        robot_id: str,
        arm: str,
        pose: str,
        *,
        steps: int,
        cancel_event: Event | None = None,
    ) -> bool:
        suffix = "L" if arm == "right" else "R"
        poses = {
            "home": (0.0, 0.35, 0.75, -0.25, 0.0, 0.15),
            "pre_grasp": (0.0, 1.00, 1.55, -0.55, 0.0, 0.15),
            "closed": (0.0, 1.15, 1.60, -0.60, 0.0, 0.95),
            "carry": (0.0, 0.75, 1.15, -0.35, 0.0, 0.95),
            "release": (0.0, 0.95, 1.40, -0.50, 0.0, 0.15),
        }
        targets = poses[pose]
        stems = ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll", "Jaw")
        addresses = [self._joint_address(f"{robot_id}__{stem}_{suffix}") for stem in stems]
        starts = [float(self.data.qpos[qpos]) for qpos, _dof in addresses]
        for step in range(1, steps + 1):
            if cancel_event is not None and cancel_event.is_set():
                return False
            ratio = step / steps
            for (qpos, dof), start, target in zip(addresses, starts, targets, strict=True):
                self.data.qpos[qpos] = start + (target - start) * ratio
                self.data.qvel[dof] = 0.0
            mujoco.mj_forward(self.model, self.data)
            self._advance(robot_id)
        return True

    def _move_block(
        self,
        robot_id: str,
        target: tuple[float, float, float],
        *,
        steps: int,
        cancel_event: Event | None = None,
    ) -> bool:
        start = np.asarray(self._object_position())
        end = np.asarray(target)
        for step in range(1, steps + 1):
            if cancel_event is not None and cancel_event.is_set():
                return False
            position = start + (end - start) * (step / steps)
            self._set_block_position(tuple(float(value) for value in position))
            mujoco.mj_forward(self.model, self.data)
            self._advance(robot_id)
        return True

    def _advance(self, source: str) -> None:
        self.step_count += 1
        self.sequence += 1
        self.source_sequences[source] += 1
        self.data.time += self.model.opt.timestep
        if self.human_speed > 0:
            time.sleep(self.human_speed)
        if self.step_count % 4 == 0:
            self._refresh_cache()

    def _refresh_cache(self) -> None:
        self._cached_entities = tuple(self._entities_unlocked())
        # MuJoCo 3.3.1 (pinned by RoboCasa 1.0.1) supports MjData's copy
        # protocol but does not expose the newer mj_copyData Python symbol.
        self._cached_render_data = copy.copy(self.data)

    def _entities_unlocked(self) -> list[RoboCasaEntity]:
        if self.held_by:
            relations = {"held_by": self.held_by}
            relation = f"held_by:{self.held_by}"
        else:
            relations = {"inside": self.placement}
            relation = f"inside:{self.placement}"
        entities = [
            RoboCasaEntity(
                self.OBJECT_ID,
                "block",
                {"color": "red", "resource_id": self.RESOURCE_ID},
                relation,
                0.99,
                self._object_position(),
                relations,
            )
        ]
        for zone in self.ZONES:
            zone_semantics = {
                "left-start-zone": ("source_zone", "left_side"),
                "handoff-zone": ("handoff_zone", "between_robots"),
                "right-target-zone": ("target_zone", "right_side"),
            }
            category, grounding_relation = zone_semantics[zone]
            entities.append(
                RoboCasaEntity(
                    zone,
                    category,
                    {"shared": "true"},
                    grounding_relation,
                    1.0,
                    self._zone_position(zone),
                    {
                        "on": "counter_main_main_group",
                        "spatial": grounding_relation,
                    },
                )
            )
        for robot_id in ("robot-1", "robot-2"):
            body_id = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_BODY, f"{robot_id}__chassis"
            )
            entities.append(
                RoboCasaEntity(
                    robot_id,
                    "robot",
                    {
                        "model": "XLeRobot",
                        "adapter": "robocasa",
                        "scene_id": self.scene.scene_id,
                        "model_hash": self.scene.model_hash,
                    },
                    "on:floor",
                    1.0,
                    tuple(float(value) for value in self.data.xpos[body_id]),
                    {"on": "floor"},
                )
            )
        entities.append(
            RoboCasaEntity("floor", "environment", {}, "", 1.0, (2.75, -1.5, 0.0))
        )
        return entities

    def entities(self) -> list[RoboCasaEntity]:
        with self.lock:
            return self._entities_unlocked()

    def cached_entities(self) -> list[RoboCasaEntity] | None:
        return list(self._cached_entities) if self._cached_entities else None

    def cached_render_data(self) -> mujoco.MjData | None:
        return self._cached_render_data

    def occupancy_grid(self) -> dict[str, object]:
        with self.lock:
            cell_size = 0.1
            origin = (0.0, -3.0)
            width, height = 55, 30
            cells = [0] * (width * height)

            def occupy(position: tuple[float, float, float], value: int) -> None:
                x = int((position[0] - origin[0]) / cell_size)
                y = int((position[1] - origin[1]) / cell_size)
                if 0 <= x < width and 0 <= y < height:
                    cells[y * width + x] = value

            for entity in self._entities_unlocked():
                occupy(entity.position, 100 if entity.category in {"robot", "block"} else 50)
            return {
                "frame_id": WORLD_FRAME,
                "transform_revision": TRANSFORM_REVISION,
                "origin_xy": origin,
                "cell_size_m": cell_size,
                "width": width,
                "height": height,
                "cells": cells,
                "sequence": self.sequence,
            }


class RoboCasaRobotView:
    """Robot-specific tools and state projected from one shared world."""

    def __init__(self, shared: RoboCasaSharedWorld, robot_id: str) -> None:
        if robot_id not in {"robot-1", "robot-2"}:
            raise ValueError(f"unknown RoboCasa robot: {robot_id}")
        self.shared = shared
        self.robot_id = robot_id
        self.model = shared.model
        self.data = shared.data
        self.lock = shared.lock
        self.tools = default_tool_registry()
        self._active_arm = ""
        self._target = ""
        self._grippers = {"left": "open", "right": "open"}
        self._verification_confidence = 0.0

    @property
    def active_arm(self) -> str:
        return self._active_arm

    def entities(self) -> list[RoboCasaEntity]:
        return self.shared.entities()

    def cached_entities(self) -> list[RoboCasaEntity] | None:
        return self.shared.cached_entities()

    def cached_render_data(self) -> mujoco.MjData | None:
        return self.shared.cached_render_data()

    def occupancy_grid(self) -> dict[str, object]:
        return self.shared.occupancy_grid()

    def adopt_fencing_token(self, token: int) -> None:
        self.shared.adopt_fencing_token(token)

    def resolve_all(
        self, *, category: str, color: str = "", relation: str = ""
    ) -> list[RoboCasaEntity]:
        return [
            entity
            for entity in self.entities()
            if entity.category == category
            and (not color or entity.attributes.get("color") == color)
            and (not relation or entity.relation == relation)
        ]

    def resolve(self, *, category: str, color: str = "", relation: str = ""):
        matches = self.resolve_all(category=category, color=color, relation=relation)
        if len(matches) != 1:
            raise ValueError(f"expected one entity, found {len(matches)}")
        return matches[0]

    def has_object(self, entity_id: str) -> bool:
        return entity_id == self.shared.OBJECT_ID and self.shared.custodian == self.robot_id

    def has_destination(self, entity_id: str) -> bool:
        return entity_id in self.shared.ZONES

    def select_arm(self, entity_id: str, destination_id: str = "") -> str | None:
        del destination_id
        if entity_id != self.shared.OBJECT_ID:
            return None
        return "right" if self.robot_id == "robot-1" else "left"

    def arm_can_reach(self, entity_id: str, arm: str) -> bool:
        return arm == self.select_arm(entity_id)

    def arm_can_reach_destination(self, destination_id: str, arm: str) -> bool:
        return self.has_destination(destination_id) and arm in {"left", "right"}

    def set_active_arm(self, arm: str, target: str = "") -> None:
        if arm not in {"left", "right"}:
            raise ValueError(f"unknown arm: {arm}")
        self._active_arm = arm
        if target:
            self._target = target

    def joint_positions(self) -> dict[str, float]:
        with self.lock:
            positions: dict[str, float] = {}
            for suffix in ("L", "R"):
                for stem in ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll", "Jaw"):
                    name = f"{self.robot_id}__{stem}_{suffix}"
                    qpos, _dof = self.shared._joint_address(name)
                    positions[name] = float(self.data.qpos[qpos])
            return positions

    def robot_state(self) -> dict[str, object]:
        with self.lock:
            body_id = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_BODY, f"{self.robot_id}__chassis"
            )
            return {
                "model_revision": self.shared.scene.model_hash,
                "world_revision": self.shared.scene.model_hash,
                "frame_id": WORLD_FRAME,
                "transform_revision": TRANSFORM_REVISION,
                "base_pose": [
                    *(float(value) for value in self.data.xpos[body_id]),
                    *(float(value) for value in self.data.xquat[body_id]),
                ],
                "joint_positions": self.joint_positions(),
                "grippers": dict(self._grippers),
                "held": self.shared.OBJECT_ID if self.shared.held_by == self.robot_id else "",
                "active_tool": f"{self._active_arm}_arm" if self._active_arm else "",
                "target": self._target,
                "episode": self.shared.episode,
                "step_count": self.shared.step_count,
                "pick_count": self.shared.pick_counts[self.robot_id],
                "owner": self.shared.owner,
                "custodian": self.shared.custodian,
                "fencing_token": self.shared.fencing_token,
                "verification_confidence": self._verification_confidence,
                "placements": {self.shared.OBJECT_ID: self.shared.placement},
                "simulation": True,
            }

    def cached_robot_state(self) -> dict[str, object] | None:
        return self.robot_state()

    def pick(self, entity_id: str, *, cancel_event: Event | None = None) -> ToolResult:
        with self.lock:
            if entity_id != self.shared.OBJECT_ID:
                return ToolResult(False, "OBJECT_NOT_FOUND", entity_id)
            if self.shared.held_by:
                return ToolResult(False, "GRIPPER_OCCUPIED", self.shared.held_by)
            if self.shared.custodian != self.robot_id or self.shared.owner not in {
                self.robot_id,
                "environment",
            }:
                return ToolResult(False, "RESOURCE_NOT_OWNED", entity_id)
            arm = self._active_arm or self.select_arm(entity_id)
            if arm is None:
                return ToolResult(False, "TARGET_UNREACHABLE", entity_id)
            self.set_active_arm(arm, entity_id)
            if not self.shared._animate_joints(
                self.robot_id, arm, "pre_grasp", steps=8, cancel_event=cancel_event
            ):
                return ToolResult(False, "CANCELLED", confidence=0.0)
            jaw_name = "Fixed_Jaw" if arm == "right" else "Fixed_Jaw_2"
            jaw_id = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_BODY, f"{self.robot_id}__{jaw_name}"
            )
            jaw = self.data.xpos[jaw_id]
            grasp = (float(jaw[0]), float(jaw[1]), float(jaw[2] - 0.04))
            if not self.shared._move_block(
                self.robot_id, grasp, steps=6, cancel_event=cancel_event
            ):
                return ToolResult(False, "CANCELLED", confidence=0.0)
            if not self.shared._animate_joints(
                self.robot_id, arm, "closed", steps=5, cancel_event=cancel_event
            ):
                return ToolResult(False, "CANCELLED", confidence=0.0)
            self._grippers[arm] = "closed"
            self.shared.held_by = self.robot_id
            self.shared.placement = ""
            if self.shared.owner == "environment":
                self.shared.owner = self.robot_id
            self.shared.pick_counts[self.robot_id] += 1
            self.shared._refresh_cache()
            return ToolResult(True)

    def place(
        self,
        destination_id: str,
        *,
        cancel_event: Event | None = None,
        try_commit: Callable[[], bool] | None = None,
    ) -> ToolResult:
        with self.lock:
            if self.shared.held_by != self.robot_id:
                return ToolResult(False, "NOT_HOLDING_OBJECT")
            allowed = {
                "robot-1": {"handoff-zone"},
                "robot-2": {"right-target-zone"},
            }[self.robot_id]
            if destination_id not in allowed:
                return ToolResult(False, "DESTINATION_NOT_ALLOWED", destination_id)
            arm = self._active_arm or self.select_arm(self.shared.OBJECT_ID)
            if arm is None:
                return ToolResult(False, "TARGET_UNREACHABLE", destination_id)
            self.set_active_arm(arm, destination_id)
            if not self.shared._animate_joints(
                self.robot_id, arm, "carry", steps=8, cancel_event=cancel_event
            ):
                return ToolResult(False, "CANCELLED", confidence=0.0)
            target = self.shared._zone_position(destination_id)
            if not self.shared._move_block(
                self.robot_id, target, steps=12, cancel_event=cancel_event
            ):
                return ToolResult(False, "CANCELLED", confidence=0.0)
            if try_commit is not None and not try_commit():
                return ToolResult(False, "CANCELLED", "place cancelled before commit", 0.0)
            if not self.shared._animate_joints(
                self.robot_id, arm, "release", steps=5, cancel_event=cancel_event
            ):
                return ToolResult(False, "CANCELLED", confidence=0.0)
            self._grippers[arm] = "open"
            self.shared.held_by = ""
            self.shared.placement = destination_id
            self.shared.owner = "environment"
            self.shared.fencing_token += 1
            if self.robot_id == "robot-1":
                self.shared.custodian = "robot-2"
            else:
                self.shared.custodian = "robot-2"
                self.shared.completed = True
            self.shared._set_block_position(target)
            self.shared._advance("environment")
            self.shared._refresh_cache()
            self.shared._notify_grant()
            return ToolResult(True)

    def verify_grasp(self, entity_id: str) -> ToolResult:
        with self.lock:
            success = entity_id == self.shared.OBJECT_ID and self.shared.held_by == self.robot_id
            self._verification_confidence = 0.99 if success else 0.1
            return ToolResult(
                success,
                "OK" if success else "GRASP_LOST",
                confidence=self._verification_confidence,
            )

    def verify_inside(self, entity_id: str, destination_id: str) -> ToolResult:
        with self.lock:
            success = (
                entity_id == self.shared.OBJECT_ID
                and not self.shared.held_by
                and self.shared.placement == destination_id
            )
            self._verification_confidence = 0.99 if success else 0.2
            return ToolResult(
                success,
                "OK" if success else "PLACEMENT_NOT_VERIFIED",
                confidence=self._verification_confidence,
            )

    def recover_to_safe_pose(
        self, arm: str | None = None, *, cancel_event: Event | None = None
    ) -> ToolResult:
        with self.lock:
            selected = arm or self._active_arm or self.select_arm(self.shared.OBJECT_ID)
            if selected not in {"left", "right"}:
                return ToolResult(False, "ARM_NOT_FOUND", str(selected))
            if not self.shared._animate_joints(
                self.robot_id, selected, "home", steps=8, cancel_event=cancel_event
            ):
                return ToolResult(False, "CANCELLED", confidence=0.0)
            self._active_arm = ""
            self._target = ""
            return ToolResult(True)

    def recover_cancelled_place(self) -> ToolResult:
        return self.recover_to_safe_pose()
