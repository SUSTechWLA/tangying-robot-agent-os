from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from tangying_robot_proto.robot.v1 import robot_pb2

from .backend import RobotBackend

ALLOWED_SKILLS = {
    "observe_scene",
    "resolve_targets",
    "plan_grasp",
    "manipulation.pick",
    "verify_grasp",
    "manipulation.place",
    "verify_placement",
    "recover_to_safe_pose",
    "emergency_stop",
}

PHYSICAL_SKILLS = {
    "manipulation.pick",
    "manipulation.place",
    "recover_to_safe_pose",
    "emergency_stop",
}


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    code: str
    message: str = ""


class SafetySupervisor:
    def __init__(
        self,
        *,
        clock_ms: Callable[[], int] | None = None,
        backend: RobotBackend | None = None,
        allowed_profiles: set[str] | None = None,
    ):
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.backend = backend
        self.allowed_profiles = allowed_profiles or {"desktop_standard"}
        self.estop_latched = False
        self._active_command_id = ""
        self._lease_expires_ms = 0

    def evaluate(self, command: robot_pb2.SkillCommand) -> SafetyDecision:
        if self.estop_latched:
            return SafetyDecision(False, "EMERGENCY_STOP_LATCHED")
        if command.schema_version != "robot.v1":
            return SafetyDecision(False, "SCHEMA_VERSION_UNSUPPORTED")
        if command.skill not in ALLOWED_SKILLS:
            return SafetyDecision(False, "SKILL_NOT_ALLOWED")
        if command.deadline_unix_ms <= self.clock_ms():
            return SafetyDecision(False, "COMMAND_EXPIRED")
        if command.lease_ms <= 0:
            return SafetyDecision(False, "LEASE_REQUIRED")
        if not command.idempotency_key:
            return SafetyDecision(False, "IDEMPOTENCY_KEY_REQUIRED")
        if command.safety_profile not in self.allowed_profiles:
            return SafetyDecision(False, "SAFETY_PROFILE_REJECTED")
        if command.skill in PHYSICAL_SKILLS and not command.approval_id:
            return SafetyDecision(False, "APPROVAL_REQUIRED")
        return SafetyDecision(True, "ALLOWED")

    def start(self, command: robot_pb2.SkillCommand) -> SafetyDecision:
        decision = self.evaluate(command)
        if decision.allowed:
            self._active_command_id = command.command_id
            self._lease_expires_ms = self.clock_ms() + command.lease_ms
        return decision

    def complete(self, command_id: str) -> None:
        if command_id == self._active_command_id:
            self._active_command_id = ""
            self._lease_expires_ms = 0

    def tick(self) -> None:
        if self._active_command_id and self.clock_ms() > self._lease_expires_ms:
            self.emergency_stop("COMMAND_LEASE_EXPIRED")

    def emergency_stop(self, reason: str) -> None:
        if self.backend is not None:
            self.backend.stop(reason)
        self.estop_latched = True
        self._active_command_id = ""
        self._lease_expires_ms = 0

    def clear_local(self, *, operator_present: bool) -> bool:
        if not operator_present:
            return False
        self.estop_latched = False
        return True
