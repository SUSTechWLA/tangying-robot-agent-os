from __future__ import annotations

import importlib
import json
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

PINNED_XLEROBOT_COMMIT = "3d14695e40c9c68229c0aacffca6053c75cd3eb6"
CALIBRATION_FILENAME = "tangying-xlerobot.json"
DEFAULT_MAX_ACTION_CHUNK_LENGTH = 64
MOBILE_BASE_KEYS = {"x.vel", "theta.vel"}
ALLOWED_ACTION_PREFIXES = ("left_arm_", "right_arm_", "head_")
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
        self._lock = threading.RLock()
        self._stop_requested = False
        self._stop_reason = ""

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    @property
    def stop_reason(self) -> str:
        with self._lock:
            return self._stop_reason

    def capabilities(self) -> DriverCapabilities:
        blockers = []
        if not self.path_exists(self.upstream_root):
            blockers.append("UPSTREAM_NOT_FOUND")
        if not self.path_exists(self.calibration_file):
            blockers.append("CALIBRATION_REQUIRED")
        missing_ports = [port for port in self.ports if not self.path_exists(port)]
        if missing_ports:
            blockers.append("SERIAL_PORTS_UNAVAILABLE")
        if self.max_relative_target <= 0:
            blockers.append("MAX_RELATIVE_TARGET_INVALID")
        if self.max_action_chunk_length <= 0:
            blockers.append("MAX_ACTION_CHUNK_LENGTH_INVALID")
        if self.robot_factory is None:
            try:
                importlib.import_module("lerobot.robots.xlerobot_2wheels.xlerobot_2wheels")
            except ImportError:
                blockers.append("XLEROBOT_LEROBOT_INTEGRATION_MISSING")
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
        except (OSError, json.JSONDecodeError) as exc:
            return DriverResult(False, "CALIBRATION_INVALID", str(exc))
        if not isinstance(data, dict) or not data:
            return DriverResult(False, "CALIBRATION_EMPTY", str(path))
        return DriverResult(True, message=f"calibration file contains {len(data)} keys")

    def connect(self) -> DriverResult:
        with self._lock:
            if self._stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self._stop_reason)
            capabilities = self.capabilities()
            if not capabilities.manipulation_ready:
                return DriverResult(False, "ROBOT_NOT_READY", ",".join(capabilities.blockers))
            try:
                if self._robot is None:
                    self._robot = self.robot_factory() if self.robot_factory else self._create_upstream_robot()
                if not self._robot.is_connected:
                    # The pinned upstream asks whether to restore an existing calibration even when
                    # calibrate=False. A systemd service has no interactive stdin, so accept only the
                    # already-validated calibration file and never begin calibration implicitly.
                    with patch("builtins.input", return_value=""):
                        self._robot.connect(calibrate=False)
            except Exception as exc:  # noqa: BLE001 - fail closed on upstream connect faults
                return DriverResult(False, "CONNECT_FAILED", str(exc))
            if not self._robot.is_calibrated:
                return DriverResult(False, "CALIBRATION_REQUIRED")
            return DriverResult(True)

    def observation(self) -> dict:
        with self._lock:
            connected = self.connect()
            if not connected.success:
                raise RuntimeError(f"{connected.code}: {connected.message}")
            try:
                return self._robot.get_observation()
            except Exception as exc:
                raise RuntimeError(f"OBSERVATION_FAILED: {exc}") from exc

    def send_action(self, action: dict[str, float]) -> DriverResult:
        with self._lock:
            if self._stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self._stop_reason)
            if not isinstance(action, dict) or not action:
                return DriverResult(False, "ACTION_MALFORMED")
            for key in MOBILE_BASE_KEYS:
                if key in action:
                    return DriverResult(False, "MOBILE_BASE_DISABLED", key)
            invalid = [key for key in action if not self._allowed_action_key(key)]
            if invalid:
                return DriverResult(False, "ACTION_KEY_REJECTED", ",".join(invalid))
            bounded: dict[str, float] = {}
            for key, value in action.items():
                error = self._validate_action_value(key, value)
                if error:
                    return DriverResult(False, error)
                bounded[key] = float(value)
            connected = self.connect()
            if not connected.success:
                return connected
            try:
                sent = self._robot.send_action(bounded)
            except Exception as exc:  # noqa: BLE001 - never manufacture motion success
                self.stop(f"SEND_ACTION_FAILED: {exc}")
                return DriverResult(False, "SEND_ACTION_FAILED", str(exc))
            return DriverResult(True, action_sent={key: float(value) for key, value in sent.items()})

    def execute_action_chunk(self, actions: list[dict[str, float]]) -> DriverResult:
        if not isinstance(actions, list) or not actions:
            return DriverResult(False, "POLICY_ACTION_CHUNK_REQUIRED")
        if len(actions) > self.max_action_chunk_length:
            return DriverResult(
                False,
                "ACTION_CHUNK_TOO_LONG",
                f"{len(actions)} > {self.max_action_chunk_length}",
            )
        last = DriverResult(True)
        for action in actions:
            if self.stop_requested:
                return DriverResult(False, "SAFETY_STOPPED", self.stop_reason)
            last = self.send_action(action)
            if not last.success:
                return last
        return last

    def stop(self, reason: str) -> None:
        errors: list[str] = []
        with self._lock:
            self._stop_requested = True
            self._stop_reason = reason
            robot = self._robot
            if robot is None:
                return
            try:
                if hasattr(robot, "stop_base"):
                    robot.stop_base()
            except Exception as exc:  # noqa: BLE001 - disable torque must still be attempted
                errors.append(f"stop_base: {exc}")
            for bus_name in ("bus1", "bus2"):
                try:
                    bus = getattr(robot, bus_name, None)
                    if bus is not None and hasattr(bus, "disable_torque"):
                        bus.disable_torque()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{bus_name}: {exc}")
        if errors:
            # The latch is set by SafetySupervisor; keep this method non-raising so
            # a flaky bus cannot prevent the safety state transition.
            with self._lock:
                self._stop_reason = f"{reason}; {', '.join(errors)}"

    def reset_stop(self, *, operator_present: bool) -> bool:
        """Local-only reset after an operator inspected the robot."""
        if not operator_present:
            return False
        with self._lock:
            self._stop_requested = False
            self._stop_reason = ""
        return True

    def disconnect(self) -> None:
        with self._lock:
            if self._robot is not None and self._robot.is_connected:
                self._robot.disconnect()

    def _create_upstream_robot(self):
        config_module = importlib.import_module(
            "lerobot.robots.xlerobot_2wheels.config_xlerobot_2wheels"
        )
        robot_module = importlib.import_module("lerobot.robots.xlerobot_2wheels.xlerobot_2wheels")
        config = config_module.XLerobot2WheelsConfig(
            id="tangying-xlerobot",
            port1=self.ports[0],
            port2=self.ports[1],
            calibration_dir=self.calibration_root,
            max_relative_target=self.max_relative_target,
        )
        return robot_module.XLerobot2Wheels(config)

    @staticmethod
    def _allowed_action_key(key: str) -> bool:
        return key.endswith(".pos") and key.startswith(ALLOWED_ACTION_PREFIXES)

    @staticmethod
    def _validate_action_value(key: str, value: object) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "ACTION_VALUE_NOT_NUMERIC"
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
