"""Test doubles for the robot tool layer.

``FakeRobotAdapter`` implements the ``RobotAdapter`` port in memory: it records
every call and lets a test script the result of each skill. That is enough to
exercise real tool logic — argument validation, error projection, composite
ordering, recovery hints — without a simulator or hardware, which is the point
of keeping the port this small.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from tangying_robot_gateway.runtime import Result
from tangying_robot_gateway.tools.registry import (
    ArmPlan,
    HardwareHealth,
    ObservationView,
    OperationContext,
)


@dataclass
class RecordedCall:
    kind: str
    name: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    timeout_s: float | None = None
    context: OperationContext | None = None


class FakeRobotAdapter:
    """An in-memory robot whose every response is scripted by the test."""

    def __init__(
        self,
        *,
        results: Mapping[str, Result] | None = None,
        observation: ObservationView | None = None,
        health: HardwareHealth | None = None,
        arm_plan: ArmPlan | None = None,
        cancel_accepted: bool = True,
    ) -> None:
        self.results = dict(results or {})
        self.observation = observation or ObservationView(
            observation_id="obs-1", observed_at_unix_ms=1_700_000_000_000,
            source_id="fake/head-rgbd", fresh=True,
            robot_state={"base_pose": [0.0, -1.25, 0.0, 1.0, 0.0, 0.0, 0.0],
                         "joint_positions": {"Pitch_L": 3.1},
                         "grippers": {"right_arm": "open"}, "held": ""},
            entities=({"entityId": "red-cup", "category": "cup",
                       "attributes": {"color": "red"}, "pose": [0.29, 0.49, 0.80],
                       "confidence": 0.9},),
        )
        self.health_state = health or HardwareHealth(
            reachable=True, mode="SIMULATION", activity="IDLE",
            available_tools=("navigation.navigate", "arm.move"),
        )
        self.arm_plan_result = arm_plan
        self.cancel_accepted = cancel_accepted
        self.calls: list[RecordedCall] = []
        self.speed_limits: list[tuple[str, float]] = []

    # -- port -------------------------------------------------------------

    def execute(self, skill, *, parameters=None, target_ref="", context=None, timeout_s=None):
        self.calls.append(RecordedCall("execute", skill, dict(parameters or {}), timeout_s, context))
        return self.results.get(skill, Result(True, "OK", "done", "obs-1", 1.0))

    def observe(self, *, streams=("entities", "reconstruction"), context=None):
        self.calls.append(RecordedCall("observe", ",".join(streams), {}, None, context))
        return self.observation

    def cancel(self, command_id: str, reason: str) -> bool:
        self.calls.append(RecordedCall("cancel", command_id, {"reason": reason}))
        return self.cancel_accepted

    def health(self) -> HardwareHealth:
        self.calls.append(RecordedCall("health", "health"))
        return self.health_state

    def set_speed_limit(self, component: str, limit: float) -> Result:
        self.calls.append(RecordedCall("set_speed_limit", component, {"limit": limit}))
        self.speed_limits.append((component, limit))
        return Result(True, "OK", "applied", "", 1.0)

    def plan_arm_motion(self, *, component, joints=None, target_pose=None, relative=None,
                        frame_id="base_link", velocity_scaling=0.5) -> ArmPlan:
        self.calls.append(RecordedCall("plan_arm_motion", component, {
            "joints": joints, "target_pose": target_pose, "relative": relative,
            "frame_id": frame_id, "velocity_scaling": velocity_scaling,
        }))
        if self.arm_plan_result is not None:
            return self.arm_plan_result
        resolved = tuple(joints) if joints else (0.1, 0.2, 0.3)
        return ArmPlan(
            ok=True, waypoints=({"left_arm_Pitch_L.pos": 1.0},), joints=resolved,
            final_pose=tuple(target_pose) if target_pose else (0.3, 0.0, 0.2, 0.0, 1.2, 0.0),
        )

    def gripper_waypoints(self, component: str, width: float):
        self.calls.append(RecordedCall("gripper_waypoints", component, {"width": width}))
        return ({"left_arm_gripper.pos": float(width)},)

    # -- assertions -------------------------------------------------------

    def skill_calls(self, skill: str) -> list[RecordedCall]:
        return [call for call in self.calls if call.kind == "execute" and call.name == skill]

    def called(self, kind: str, name: str | None = None) -> bool:
        return any(call.kind == kind and (name is None or call.name == name) for call in self.calls)
