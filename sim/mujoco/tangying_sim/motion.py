from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import ClassVar

import mujoco
import numpy as np


class MotionLimitError(ValueError):
    """Raised when a requested interpolation cannot be executed safely."""


HOME = {
    "Rotation": 0.0,
    "Pitch": 0.35,
    "Elbow": 0.55,
    "Wrist_Pitch": 0.0,
    "Wrist_Roll": 0.0,
    "Jaw": 0.70,
}
PRE_GRASP = {**HOME, "Rotation": -0.28, "Pitch": 0.85, "Elbow": 1.20}
LIFT = {**HOME, "Rotation": -0.18, "Pitch": 0.55, "Elbow": 1.45, "Jaw": -0.25}
PLACE = {**HOME, "Rotation": -0.38, "Pitch": 0.92, "Elbow": 1.10, "Jaw": -0.25}
OPEN = {"Jaw": 0.70}
CLOSED = {"Jaw": -0.25}

NAMED_TARGETS = {
    "HOME": HOME,
    "PRE_GRASP": PRE_GRASP,
    "LIFT": LIFT,
    "PLACE": PLACE,
    "OPEN": OPEN,
    "CLOSED": CLOSED,
}


class MotionController:
    """Bounded interpolation for the official XLeRobot arm and jaw joints."""

    MAX_STEPS = 200
    # The upstream names are mirrored relative to the robot's +Y-facing workspace.
    _SUFFIX: ClassVar[dict[str, str]] = {"left": "R", "right": "L"}

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData):
        self.model = model
        self.data = data

    def target_for(self, arm: str, name: str) -> dict[str, float]:
        self._validate_arm(arm)
        try:
            target = NAMED_TARGETS[name]
        except KeyError as exc:
            raise MotionLimitError(f"unknown named target: {name}") from exc
        suffix = self._SUFFIX[arm]
        mirrored = -1.0 if arm == "right" else 1.0
        return {
            f"{stem}_{suffix}": value * mirrored if stem == "Rotation" else value
            for stem, value in target.items()
        }

    def move_named(
        self,
        arm: str,
        name: str,
        *,
        steps: int = 20,
        on_step: Callable[[float], None] | None = None,
    ) -> dict[str, float]:
        return self.interpolate(arm, self.target_for(arm, name), steps=steps, on_step=on_step)

    def interpolate(
        self,
        arm: str,
        target: Mapping[str, float],
        *,
        steps: int,
        on_step: Callable[[float], None] | None = None,
    ) -> dict[str, float]:
        self._validate_arm(arm)
        if not 1 <= steps <= self.MAX_STEPS:
            raise MotionLimitError(f"steps must be between 1 and {self.MAX_STEPS}, got {steps}")

        suffix = self._SUFFIX[arm]
        resolved: dict[str, float] = {}
        addresses: dict[str, int] = {}
        starts: dict[str, float] = {}
        for requested_name, requested_value in target.items():
            joint_name = (
                requested_name
                if requested_name.endswith(("_L", "_R"))
                else f"{requested_name}_{suffix}"
            )
            if not joint_name.endswith(f"_{suffix}"):
                raise MotionLimitError(f"joint {joint_name} does not belong to {arm} arm")
            joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
            if joint_id < 0:
                raise MotionLimitError(f"unknown joint: {joint_name}")
            low, high = self.model.jnt_range[joint_id]
            clamped = float(np.clip(requested_value, low, high))
            address = int(self.model.jnt_qposadr[joint_id])
            resolved[joint_name] = clamped
            addresses[joint_name] = address
            starts[joint_name] = float(self.data.qpos[address])

        for index in range(1, steps + 1):
            progress = index / steps
            for joint_name, destination in resolved.items():
                address = addresses[joint_name]
                value = starts[joint_name] + (destination - starts[joint_name]) * progress
                self.data.qpos[address] = value
                joint_id = mujoco.mj_name2id(
                    self.model, mujoco.mjtObj.mjOBJ_JOINT, joint_name
                )
                dof_address = int(self.model.jnt_dofadr[joint_id])
                self.data.qvel[dof_address] = 0.0
                actuator_id = mujoco.mj_name2id(
                    self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, joint_name
                )
                if actuator_id >= 0:
                    self.data.ctrl[actuator_id] = value
            mujoco.mj_forward(self.model, self.data)
            if on_step is not None:
                on_step(progress)
        return resolved

    @classmethod
    def _validate_arm(cls, arm: str) -> None:
        if arm not in cls._SUFFIX:
            raise MotionLimitError(f"arm must be one of {tuple(cls._SUFFIX)}, got {arm!r}")
