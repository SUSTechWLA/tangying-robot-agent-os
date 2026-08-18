from __future__ import annotations

import math
import threading
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

ALLOWED_ACTION_PREFIXES = ("left_arm_", "right_arm_", "head_")
MOBILE_BASE_KEYS = {"x.vel", "theta.vel"}
MAX_ACTION_CHUNK_LENGTH = 64
MAX_ABSOLUTE_ACTION_VALUE = 100.0


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
        max_lease_ms: int = 60_000,
        max_action_chunk_length: int = MAX_ACTION_CHUNK_LENGTH,
    ):
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.backend = backend
        self.allowed_profiles = allowed_profiles or {"desktop_standard"}
        self.max_lease_ms = max_lease_ms
        self.max_action_chunk_length = max_action_chunk_length
        self.estop_latched = False
        self.last_stop_reason = ""
        self._active_command_id = ""
        self._lease_expires_ms = 0
        self._lock = threading.RLock()

    @property
    def active_command_id(self) -> str:
        with self._lock:
            return self._active_command_id

    def evaluate(self, command: robot_pb2.SkillCommand) -> SafetyDecision:
        with self._lock:
            if self.estop_latched:
                return SafetyDecision(False, "EMERGENCY_STOP_LATCHED")
            if self._active_command_id and self._active_command_id != command.command_id:
                return SafetyDecision(False, "ROBOT_BUSY")
            if command.schema_version != "robot.v1":
                return SafetyDecision(False, "SCHEMA_VERSION_UNSUPPORTED")
            if not command.task_id:
                return SafetyDecision(False, "TASK_ID_REQUIRED")
            if not command.command_id:
                return SafetyDecision(False, "COMMAND_ID_REQUIRED")
            if command.skill not in ALLOWED_SKILLS:
                return SafetyDecision(False, "SKILL_NOT_ALLOWED")
            if command.deadline_unix_ms <= self.clock_ms():
                return SafetyDecision(False, "COMMAND_EXPIRED")
            if command.lease_ms <= 0:
                return SafetyDecision(False, "LEASE_REQUIRED")
            if command.lease_ms > self.max_lease_ms:
                return SafetyDecision(False, "LEASE_TOO_LONG")
            if not command.idempotency_key:
                return SafetyDecision(False, "IDEMPOTENCY_KEY_REQUIRED")
            if command.safety_profile not in self.allowed_profiles:
                return SafetyDecision(False, "SAFETY_PROFILE_REJECTED")
            if command.skill in PHYSICAL_SKILLS and not command.approval_id:
                return SafetyDecision(False, "APPROVAL_REQUIRED")
            parameter_error = self._validate_parameters(command)
            if parameter_error:
                return parameter_error
            return SafetyDecision(True, "ALLOWED")

    def start(self, command: robot_pb2.SkillCommand) -> SafetyDecision:
        with self._lock:
            decision = self.evaluate(command)
            if decision.allowed:
                self._active_command_id = command.command_id
                self._lease_expires_ms = self.clock_ms() + command.lease_ms
            return decision

    def complete(self, command_id: str) -> None:
        with self._lock:
            if command_id == self._active_command_id:
                self._active_command_id = ""
                self._lease_expires_ms = 0

    def cancel(self, command_id: str, reason: str) -> bool:
        with self._lock:
            if command_id != self._active_command_id:
                return False
            self._active_command_id = ""
            self._lease_expires_ms = 0
        # Deliberately not latched when the controlled stop succeeds:
        # cancellation stops one command, emergency_stop is the latched path.
        if self.backend is not None:
            try:
                self.backend.stop(reason)
            except Exception as exc:  # noqa: BLE001 - unable to stop must fail safe
                with self._lock:
                    self.estop_latched = True
                    self.last_stop_reason = f"CANCEL_STOP_FAILED: {exc}"
        return True

    def tick(self) -> None:
        with self._lock:
            if self._active_command_id and self.clock_ms() > self._lease_expires_ms:
                self.emergency_stop("COMMAND_LEASE_EXPIRED")

    def emergency_stop(self, reason: str) -> None:
        with self._lock:
            # Set the latch first; a flaky backend/driver stop call must never
            # prevent the deterministic safety state transition.
            self.estop_latched = True
            self.last_stop_reason = reason
            self._active_command_id = ""
            self._lease_expires_ms = 0
            backend = self.backend
        if backend is not None:
            try:
                backend.stop(reason)
            except Exception as exc:  # noqa: BLE001 - latch is already set
                with self._lock:
                    self.last_stop_reason = f"{reason}; BACKEND_STOP_FAILED: {exc}"

    def clear_local(self, *, operator_present: bool) -> bool:
        with self._lock:
            if not operator_present:
                return False
            self.estop_latched = False
            self.last_stop_reason = ""
            backend = self.backend
        if backend is not None and hasattr(backend, "reset_stop"):
            try:
                backend.reset_stop(operator_present=True)
            except Exception as exc:  # noqa: BLE001 - local reset must remain inspectable
                with self._lock:
                    self.last_stop_reason = f"LOCAL_RESET_FAILED: {exc}"
                return False
        return True

    def _validate_parameters(self, command: robot_pb2.SkillCommand) -> SafetyDecision | None:
        if "action_chunk" not in command.parameters:
            return None
        values = command.parameters["action_chunk"].values
        if len(values) > self.max_action_chunk_length:
            return SafetyDecision(False, "ACTION_CHUNK_TOO_LONG")
        for item in values:
            error = self._validate_action_value(item)
            if error:
                return error
        return None

    def _validate_action_value(self, item) -> SafetyDecision | None:
        if not item.HasField("struct_value"):
            return SafetyDecision(False, "ACTION_CHUNK_MALFORMED")
        for key, value in item.struct_value.fields.items():
            if key in MOBILE_BASE_KEYS:
                return SafetyDecision(False, "MOBILE_BASE_DISABLED")
            if not key.endswith(".pos") or not key.startswith(ALLOWED_ACTION_PREFIXES):
                return SafetyDecision(False, "ACTION_KEY_REJECTED")
            if value.WhichOneof("kind") != "number_value":
                return SafetyDecision(False, "ACTION_VALUE_NOT_NUMERIC")
            number = value.number_value
            if not math.isfinite(number):
                return SafetyDecision(False, "ACTION_VALUE_NOT_FINITE")
            if abs(number) > MAX_ABSOLUTE_ACTION_VALUE:
                return SafetyDecision(False, "ACTION_VALUE_OUT_OF_RANGE")
            if key.endswith("gripper.pos") and not 0.0 <= number <= MAX_ABSOLUTE_ACTION_VALUE:
                return SafetyDecision(False, "GRIPPER_VALUE_OUT_OF_RANGE")
        return None
