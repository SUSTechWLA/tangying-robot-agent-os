from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .backend import RobotBackend
from .journal import RuntimeJournal
from .runtime import Command

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
        journal: RuntimeJournal | None = None,
    ):
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self.backend = backend
        self.allowed_profiles = allowed_profiles or {"desktop_standard"}
        self.max_lease_ms = max_lease_ms
        self.max_action_chunk_length = max_action_chunk_length
        self.journal = journal or RuntimeJournal(None)
        self.estop_latched = self.journal.estop_latched
        self.last_stop_reason = self.journal.estop_reason
        self._active_command_id = ""
        self._lease_expires_ms = 0
        self._stops_in_progress = 0
        self._reset_in_progress = False
        self._stop_generation = 0
        self._lock = threading.RLock()

    @property
    def active_command_id(self) -> str:
        with self._lock:
            return self._active_command_id

    def evaluate(self, command: Command) -> SafetyDecision:
        with self._lock:
            if self.estop_latched:
                return SafetyDecision(False, "EMERGENCY_STOP_LATCHED")
            if self._active_command_id or self._stops_in_progress or self._reset_in_progress:
                return SafetyDecision(False, "ROBOT_BUSY")
            if command.schema_version != "robot.v1":
                return SafetyDecision(False, "SCHEMA_VERSION_UNSUPPORTED")
            if not command.task_id:
                return SafetyDecision(False, "TASK_ID_REQUIRED")
            if not command.command_id:
                return SafetyDecision(False, "COMMAND_ID_REQUIRED")
            if command.capability not in ALLOWED_SKILLS:
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
            if command.capability in PHYSICAL_SKILLS and not command.approval_id:
                return SafetyDecision(False, "APPROVAL_REQUIRED")
            parameter_error = self._validate_parameters(command)
            if parameter_error:
                return parameter_error
            return SafetyDecision(True, "ALLOWED")

    def start(self, command: Command) -> SafetyDecision:
        with self._lock:
            decision = self.evaluate(command)
            if decision.allowed:
                now = self.clock_ms()
                if command.deadline_unix_ms <= now:
                    return SafetyDecision(False, "COMMAND_EXPIRED")
                self._active_command_id = command.command_id
                self._lease_expires_ms = min(
                    now + command.lease_ms, command.deadline_unix_ms
                )
            return decision

    def complete(self, command_id: str) -> None:
        with self._lock:
            if command_id == self._active_command_id:
                self._active_command_id = ""
                self._lease_expires_ms = 0

    def cancel(self, command_id: str, reason: str) -> bool:
        with self._lock:
            if not command_id or command_id != self._active_command_id:
                return False
            # Keep the command reserved until its execution thread calls complete.
            # A successful stop request does not prove that thread has unwound.
            self._lease_expires_ms = 0
            self._stops_in_progress += 1
            self._stop_generation += 1
        # Deliberately not latched when the controlled stop succeeds:
        # cancellation stops one command, emergency_stop is the latched path.
        self._stop_backend(reason, failure_code="CANCEL_STOP_FAILED")
        return True

    def tick(self) -> None:
        with self._lock:
            if not (
                self._active_command_id
                and self._lease_expires_ms
                and not self.estop_latched
                and self.clock_ms() >= self._lease_expires_ms
            ):
                return
            self._begin_emergency_stop_locked("COMMAND_LEASE_EXPIRED")
        self._stop_backend("COMMAND_LEASE_EXPIRED")

    def emergency_stop(self, reason: str) -> None:
        with self._lock:
            self._begin_emergency_stop_locked(reason)
        self._stop_backend(reason)

    def _begin_emergency_stop_locked(self, reason: str) -> None:
        # Latch before storage or driver I/O. Keep an executing command reserved
        # even after stopping, until its owner acknowledges completion.
        self.estop_latched = True
        self.last_stop_reason = reason
        self._lease_expires_ms = 0
        self._stops_in_progress += 1
        self._stop_generation += 1
        self._persist_estop_locked(True, reason)

    def _stop_backend(self, reason: str, *, failure_code: str = "BACKEND_STOP_FAILED") -> None:
        try:
            if self.backend is not None:
                self.backend.stop(reason)
        except Exception as exc:  # noqa: BLE001 - unable to stop must fail safe
            with self._lock:
                self.estop_latched = True
                self._stop_generation += 1
                current_reason = self.last_stop_reason or reason
                self.last_stop_reason = f"{current_reason}; {failure_code}: {exc}"
                self._persist_estop_locked(True, self.last_stop_reason)
        finally:
            with self._lock:
                self._stops_in_progress -= 1

    def clear_local(self, *, operator_present: bool) -> bool:
        with self._lock:
            if (
                not operator_present
                or self._active_command_id
                or self._stops_in_progress
                or self._reset_in_progress
            ):
                return False
            self._reset_in_progress = True
            stop_generation = self._stop_generation
            backend = self.backend
        reset_error = ""
        if backend is not None and hasattr(backend, "reset_stop"):
            try:
                if not backend.reset_stop(operator_present=True):
                    reset_error = "LOCAL_RESET_REJECTED"
            except Exception as exc:  # noqa: BLE001 - local reset must remain inspectable
                reset_error = f"LOCAL_RESET_FAILED: {exc}"
        with self._lock:
            if self._stop_generation != stop_generation:
                reason = self.last_stop_reason or "LOCAL_RESET_INTERRUPTED"
            elif reset_error:
                reason = reset_error
            elif self._persist_estop_locked(False, ""):
                self.estop_latched = False
                self.last_stop_reason = ""
                self._reset_in_progress = False
                return True
            else:
                reason = self.last_stop_reason
            self._reset_in_progress = False
            # A reset may finish after an intervening emergency stop, or may
            # have partially succeeded before failing. Reapply the backend stop.
            self._begin_emergency_stop_locked(reason)
        self._stop_backend(reason)
        return False

    def _persist_estop_locked(self, latched: bool, reason: str) -> bool:
        try:
            self.journal.set_estop(latched, reason)
        except Exception as exc:  # noqa: BLE001 - storage failure cannot prevent stopping
            self.estop_latched = True
            separator = "; " if reason else ""
            self.last_stop_reason = f"{reason}{separator}RUNTIME_JOURNAL_WRITE_FAILED: {exc}"
            return False
        return True

    def _validate_parameters(self, command: Command) -> SafetyDecision | None:
        if "action_chunk" not in command.parameters:
            return None
        values = command.parameters["action_chunk"]
        if not isinstance(values, list):
            return SafetyDecision(False, "ACTION_CHUNK_MALFORMED")
        if len(values) > self.max_action_chunk_length:
            return SafetyDecision(False, "ACTION_CHUNK_TOO_LONG")
        for item in values:
            error = self._validate_action_value(item)
            if error:
                return error
        return None

    def _validate_action_value(self, item) -> SafetyDecision | None:
        if not isinstance(item, dict):
            return SafetyDecision(False, "ACTION_CHUNK_MALFORMED")
        for key, value in item.items():
            if key in MOBILE_BASE_KEYS:
                return SafetyDecision(False, "MOBILE_BASE_DISABLED")
            if not key.endswith(".pos") or not key.startswith(ALLOWED_ACTION_PREFIXES):
                return SafetyDecision(False, "ACTION_KEY_REJECTED")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return SafetyDecision(False, "ACTION_VALUE_NOT_NUMERIC")
            number = float(value)
            if not math.isfinite(number):
                return SafetyDecision(False, "ACTION_VALUE_NOT_FINITE")
            if abs(number) > MAX_ABSOLUTE_ACTION_VALUE:
                return SafetyDecision(False, "ACTION_VALUE_OUT_OF_RANGE")
            if key.endswith("gripper.pos") and not 0.0 <= number <= MAX_ABSOLUTE_ACTION_VALUE:
                return SafetyDecision(False, "GRIPPER_VALUE_OUT_OF_RANGE")
        return None
