from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from numbers import Real
from pathlib import Path

from .calibration import validate_calibration_data
from .upstream_compat import (
    PINNED_XLEROBOT_COMMIT,
    checked_upstream_modules,
    create_compatible_robot,
)

__all__ = ["PINNED_XLEROBOT_COMMIT", "DriverCapabilities", "DriverResult", "XLeRobotDriver"]

CALIBRATION_FILENAME = "tangying-xlerobot.json"
DEFAULT_MAX_ACTION_CHUNK_LENGTH = 64
MOBILE_BASE_KEYS = {"x.vel", "theta.vel"}
# Motor names from the pinned XLerobot2Wheels.action_features contract.
ALLOWED_ACTION_KEYS = frozenset(
    f"{side}_arm_{joint}.pos"
    for side in ("left", "right")
    for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
) | {"head_motor_1.pos", "head_motor_2.pos"}
MAX_ABSOLUTE_ACTION_VALUE = 100.0


@dataclass(frozen=True)
class DriverCapabilities:
    manipulation_ready: bool
    blockers: tuple[str, ...] = ()
    skills: tuple[str, ...] = (
        "manipulation.pick",
        "manipulation.place",
        "recover_to_safe_pose",
        "emergency_stop",
    )
    calibration_file: Path | None = None
    max_relative_target: float = 8.0
    max_action_chunk_length: int = DEFAULT_MAX_ACTION_CHUNK_LENGTH


@dataclass(frozen=True)
class DriverResult:
    success: bool
    code: str = "OK"
    message: str = ""
    action_sent: dict[str, float] = field(default_factory=dict)


class XLeRobotDriver:
    def __init__(
        self,
        *,
        upstream_root: Path,
        calibration_root: Path,
        ports: tuple[str, str],
        path_exists: Callable[[Path | str], bool] | None = None,
        robot_factory: Callable[[], object] | None = None,
        max_relative_target: float = 8.0,
        max_action_chunk_length: int = DEFAULT_MAX_ACTION_CHUNK_LENGTH,
    ):
        self.upstream_root = Path(upstream_root)
        self.calibration_root = Path(calibration_root)
        self.ports = ports
        self.path_exists = path_exists or (lambda path: Path(path).exists())
        self.robot_factory = robot_factory
        self.max_relative_target = max_relative_target
        self.max_action_chunk_length = max_action_chunk_length
        self.calibration_file = self.calibration_root / CALIBRATION_FILENAME
        self._robot = None
        self._armed = False
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._stop_requested = False
        self._stop_reason = ""
        self._stops_pending = 0

    @property
    def stop_requested(self) -> bool:
        with self._stop_lock:
            return self._stop_requested

    @property
    def stop_reason(self) -> str:
        with self._stop_lock:
            return self._stop_reason

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._robot is not None and bool(self._robot.is_connected)

    @property
    def is_armed(self) -> bool:
        with self._stop_lock:
            return self._armed and not self._stop_requested

    def capabilities(self) -> DriverCapabilities:
        blockers = []
        if not self.path_exists(self.upstream_root):
            blockers.append("UPSTREAM_NOT_FOUND")
        if not self.path_exists(self.calibration_file):
            blockers.append("CALIBRATION_REQUIRED")
        missing_ports = [port for port in self.ports if not self.path_exists(port)]
        if missing_ports:
            blockers.append("SERIAL_PORTS_UNAVAILABLE")
        blockers.extend(self._configuration_blockers())
        if self.robot_factory is None:
            try:
                checked_upstream_modules()
            except ImportError:
                blockers.append("XLEROBOT_LEROBOT_INTEGRATION_MISSING")
            except RuntimeError as exc:
                blockers.append(str(exc).split(":", 1)[0])
        return DriverCapabilities(
            manipulation_ready=not blockers,
            blockers=tuple(blockers),
            calibration_file=self.calibration_file,
            max_relative_target=self.max_relative_target,
            max_action_chunk_length=self.max_action_chunk_length,
        )

    def validate_calibration_file(self) -> DriverResult:
        """Validate calibration without connecting or moving the robot."""
        path = self.calibration_file
        if not self.path_exists(path):
            return DriverResult(False, "CALIBRATION_REQUIRED", str(path))
        try:
            content = Path(path).read_text(encoding="utf-8")
            data = json.loads(content)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return DriverResult(False, "CALIBRATION_INVALID", str(exc))
        if not isinstance(data, dict) or not data:
            return DriverResult(False, "CALIBRATION_EMPTY", str(path))
        try:
            validate_calibration_data(data)
        except ValueError as exc:
            return DriverResult(False, "CALIBRATION_INVALID", str(exc))
        return DriverResult(True, message=f"calibration file contains {len(data)} keys")

    def connect(self) -> DriverResult:
        """Explicit commissioning connection; the compatible robot keeps torque disabled."""
        with self._lock:
            if self.stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self.stop_reason)
            capabilities = self.capabilities()
            if not capabilities.manipulation_ready:
                return DriverResult(False, "ROBOT_NOT_READY", ",".join(capabilities.blockers))
            calibration = self.validate_calibration_file()
            if not calibration.success:
                return calibration
            try:
                if self._robot is None:
                    self._robot = self.robot_factory() if self.robot_factory else self._create_upstream_robot()
                if not self._robot.is_connected:
                    self._armed = False
                    self._robot.connect(calibrate=False)
                if not self._robot.is_calibrated:
                    raise RuntimeError("CALIBRATION_RESTORE_FAILED")
            except Exception as exc:  # noqa: BLE001 - fail closed on upstream connect faults
                return self._commissioning_failure("CONNECT_FAILED", exc)
            except BaseException:
                self.stop("CONNECT_INTERRUPTED")
                raise
            if self.stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self.stop_reason)
            return DriverResult(True)

    def arm(self, *, operator_present: bool) -> DriverResult:
        """Local operator action: hold current joints, then enable arm/head torque only."""
        if operator_present is not True:
            return DriverResult(False, "LOCAL_OPERATOR_REQUIRED")
        with self._lock:
            if self.stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self.stop_reason)
            if not self.is_connected:
                return DriverResult(False, "ROBOT_NOT_CONNECTED")
            if self.is_armed:
                return DriverResult(True)
            try:
                self._robot.arm_tabletop(stop_requested=lambda: self.stop_requested)
            except Exception as exc:  # noqa: BLE001 - stop on any hardware arming fault
                return self._commissioning_failure("ARM_FAILED", exc)
            except BaseException:
                self.stop("ARM_INTERRUPTED")
                raise
            with self._stop_lock:
                if self._stop_requested:
                    return DriverResult(False, "SAFETY_STOPPED", self._stop_reason)
                self._armed = True
            return DriverResult(True)

    def _commissioning_failure(self, code: str, exc: Exception) -> DriverResult:
        message = str(exc)
        try:
            self.stop(f"{code}: {message}")
        except Exception as stop_exc:  # noqa: BLE001 - preserve both commissioning and stop errors
            message += f"; {stop_exc}"
        return DriverResult(False, code, message)

    def observation(self) -> dict:
        with self._lock:
            if self.stop_requested:
                raise RuntimeError(f"SAFETY_STOPPED: {self.stop_reason}")
            # A read-only observation must never open ports, restore calibration,
            # configure motors or enable torque as a side effect.
            if self._robot is None or not self._robot.is_connected:
                raise RuntimeError("ROBOT_NOT_CONNECTED: observation requires an existing connection")
            try:
                return self._robot.get_observation()
            except Exception as exc:
                raise RuntimeError(f"OBSERVATION_FAILED: {exc}") from exc

    def send_action(self, action: dict[str, float]) -> DriverResult:
        with self._lock:
            if self.stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self.stop_reason)
            validation = self._validate_action(action)
            if validation is not None:
                return validation
            bounded = {key: float(value) for key, value in action.items()}
            blockers = self._configuration_blockers()
            if blockers:
                return DriverResult(False, "ROBOT_NOT_READY", ",".join(blockers))
            if not self.is_armed:
                return DriverResult(False, "ROBOT_NOT_ARMED")
            if not self.is_connected:
                self._armed = False
                return DriverResult(False, "ROBOT_NOT_CONNECTED")
            if self.stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self.stop_reason)
            try:
                sent = self._robot.send_action(bounded)
            except Exception as exc:  # noqa: BLE001 - never manufacture motion success
                self.stop(f"SEND_ACTION_FAILED: {exc}")
                return DriverResult(False, "SEND_ACTION_FAILED", str(exc))
            if self.stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self.stop_reason)
            if (
                not isinstance(sent, dict)
                or set(sent) != set(bounded)
                or self._validate_action(sent) is not None
            ):
                self.stop("SEND_ACTION_INVALID_RESULT")
                return DriverResult(False, "SEND_ACTION_INVALID_RESULT")
            return DriverResult(True, action_sent={key: float(value) for key, value in sent.items()})

    def execute_action_chunk(self, actions: list[dict[str, float]]) -> DriverResult:
        if not isinstance(actions, list) or not actions:
            return DriverResult(False, "POLICY_ACTION_CHUNK_REQUIRED")
        blockers = self._configuration_blockers()
        if blockers:
            return DriverResult(False, "ROBOT_NOT_READY", ",".join(blockers))
        if len(actions) > self.max_action_chunk_length:
            return DriverResult(
                False,
                "ACTION_CHUNK_TOO_LONG",
                f"{len(actions)} > {self.max_action_chunk_length}",
            )
        # Validate and copy every step before connecting or sending any motion.
        # The caller must not be able to change a later step after this preflight.
        validated = []
        for action in actions:
            validation = self._validate_action(action)
            if validation is not None:
                return validation
            validated.append({key: float(value) for key, value in action.items()})
        last = DriverResult(True)
        for action in validated:
            if self.stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self.stop_reason)
            last = self.send_action(action)
            if not last.success:
                return last
        return last

    def stop(self, reason: str) -> None:
        errors: list[str] = []
        # Latch before waiting for a potentially blocked serial operation. The bus
        # calls remain serialized; physical torque-off still depends on I/O returning.
        with self._stop_lock:
            self._armed = False
            self._stop_requested = True
            self._stop_reason = reason
            self._stops_pending += 1
        try:
            with self._lock:
                robot = self._robot
                if robot is None:
                    return
                try:
                    if hasattr(robot, "stop_base"):
                        robot.stop_base()
                except Exception as exc:  # noqa: BLE001 - still attempt both torque disables
                    errors.append(f"stop_base: {exc}")
                for bus_name in ("bus1", "bus2"):
                    try:
                        bus = getattr(robot, bus_name, None)
                        if bus is not None and hasattr(bus, "disable_torque"):
                            bus.disable_torque()
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{bus_name}: {exc}")
        finally:
            with self._stop_lock:
                if errors:
                    self._stop_reason = f"{self._stop_reason}; {', '.join(errors)}"
                self._stops_pending -= 1
        if errors:
            # The supervisor must persist a failed physical stop instead of
            # treating an unsuccessful torque-disable as a completed cancellation.
            raise RuntimeError(f"STOP_FAILED: {'; '.join(errors)}")

    def reset_stop(self, *, operator_present: bool) -> bool:
        """Local-only reset after an operator inspected the robot."""
        if not operator_present:
            return False
        with self._lock, self._stop_lock:
            if self._stops_pending:
                return False
            self._stop_requested = False
            self._stop_reason = ""
        return True

    def disconnect(self) -> None:
        with self._lock:
            self._armed = False
            if self._robot is not None:
                self._robot.disconnect()

    def _create_upstream_robot(self):
        config_module, _ = checked_upstream_modules()
        config = config_module.XLerobot2WheelsConfig(
            id="tangying-xlerobot",
            port1=self.ports[0],
            port2=self.ports[1],
            calibration_dir=self.calibration_root,
            max_relative_target=self.max_relative_target,
        )
        return create_compatible_robot(config)

    @staticmethod
    def _allowed_action_key(key: object) -> bool:
        return isinstance(key, str) and key in ALLOWED_ACTION_KEYS

    def _configuration_blockers(self) -> list[str]:
        blockers = []
        try:
            valid_relative = (
                isinstance(self.max_relative_target, Real)
                and not isinstance(self.max_relative_target, bool)
                and math.isfinite(self.max_relative_target)
                and self.max_relative_target > 0
            )
        except OverflowError:
            valid_relative = False
        if not valid_relative:
            blockers.append("MAX_RELATIVE_TARGET_INVALID")
        if (
            not isinstance(self.max_action_chunk_length, int)
            or isinstance(self.max_action_chunk_length, bool)
            or self.max_action_chunk_length <= 0
        ):
            blockers.append("MAX_ACTION_CHUNK_LENGTH_INVALID")
        return blockers

    def _validate_action(self, action: object) -> DriverResult | None:
        if not isinstance(action, dict) or not action:
            return DriverResult(False, "ACTION_MALFORMED")
        for key, value in action.items():
            if key in MOBILE_BASE_KEYS:
                return DriverResult(False, "MOBILE_BASE_DISABLED", str(key))
            if not self._allowed_action_key(key):
                return DriverResult(False, "ACTION_KEY_REJECTED", str(key))
            error = self._validate_action_value(key, value)
            if error:
                return DriverResult(False, error, key)
        return None

    @staticmethod
    def _validate_action_value(key: str, value: object) -> str:
        if not isinstance(value, Real) or isinstance(value, bool):
            return "ACTION_VALUE_NOT_NUMERIC"
        try:
            number = float(value)
        except OverflowError:
            return "ACTION_VALUE_OUT_OF_RANGE"
        if not math.isfinite(number):
            return "ACTION_VALUE_NOT_FINITE"
        if abs(number) > MAX_ABSOLUTE_ACTION_VALUE:
            return "ACTION_VALUE_OUT_OF_RANGE"
        if key.endswith("gripper.pos") and not 0.0 <= number <= MAX_ABSOLUTE_ACTION_VALUE:
            return "GRIPPER_VALUE_OUT_OF_RANGE"
        return ""

    @staticmethod
    def _bound_value(key: str, value: float) -> float:
        """Legacy helper retained for tests/tooling; send_action now rejects out-of-range values."""
        if key.endswith("gripper.pos"):
            return min(100.0, max(0.0, value))
        return min(100.0, max(-100.0, value))
