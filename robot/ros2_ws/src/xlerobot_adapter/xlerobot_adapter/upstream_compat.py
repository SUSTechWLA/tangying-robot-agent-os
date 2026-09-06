"""Tabletop compatibility for the exact XLeRobot 3d14695 source and LeRobot 0.4.1.

The external checkout and installed upstream modules remain unchanged. Only the
factory below is the supported entry point; source drift fails before construction.
Connection configures torque-off. Enabling arm/head torque is a separate operation.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import math
from collections.abc import Callable
from numbers import Real
from pathlib import Path

PINNED_XLEROBOT_COMMIT = "3d14695e40c9c68229c0aacffca6053c75cd3eb6"
PINNED_LEROBOT_VERSION = "0.4.1"
_SOURCE_SHA256 = {
    "robot": "d1751d78fd3f0684cf0b1184d86338cc332ef52fe10300d85b51e9cfcc0b5dc4",
    "config": "4942d140c24eeecac1f3d123e12fe0cc5b84e030d57f8e2026e7e1960db664f4",
}


def verify_source_file(path: Path | str, kind: str) -> None:
    """Verify the actual imported bytes, not just the external checkout's git HEAD."""
    try:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError) as exc:
        raise RuntimeError(f"UPSTREAM_SOURCE_UNSUPPORTED: {kind}: {exc}") from exc
    if digest != _SOURCE_SHA256[kind]:
        raise RuntimeError(f"UPSTREAM_SOURCE_UNSUPPORTED: {kind}: {path}")


def checked_upstream_modules():
    if importlib.metadata.version("lerobot") != PINNED_LEROBOT_VERSION:
        raise RuntimeError(f"LEROBOT_VERSION_UNSUPPORTED: expected {PINNED_LEROBOT_VERSION}")
    config = importlib.import_module("lerobot.robots.xlerobot_2wheels.config_xlerobot_2wheels")
    robot = importlib.import_module("lerobot.robots.xlerobot_2wheels.xlerobot_2wheels")
    verify_source_file(getattr(config, "__file__", None), "config")
    verify_source_file(getattr(robot, "__file__", None), "robot")
    return config, robot


def create_compatible_robot(config):
    """Construct without opening ports; connect(calibrate=False) keeps torque off."""
    _, module = checked_upstream_modules()
    compatible = type("TabletopXLeRobot", (_TabletopCompatibility, module.XLerobot2Wheels), {})
    return compatible(config)


class _TabletopCompatibility:
    def connect(self, calibrate: bool = False) -> None:
        if calibrate:
            raise ValueError("MANUAL_CALIBRATION_REQUIRED: connect first, then call calibrate explicitly")
        if self.is_connected:
            raise RuntimeError("ROBOT_ALREADY_CONNECTED")
        try:
            for bus in (self.bus1, self.bus2):
                bus.connect()
                # Torque must be disabled before restoring calibration registers.
                bus.disable_torque()
            if self.calibration:
                for bus in (self.bus1, self.bus2):
                    bus.write_calibration({key: self.calibration[key] for key in bus.motors})
                if not self.is_calibrated:
                    raise RuntimeError("CALIBRATION_RESTORE_FAILED")
            for camera in self.cameras.values():
                camera.connect()
            self.configure()
        except BaseException as exc:
            try:
                self.disconnect()
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve cleanup failures
                raise RuntimeError(f"{exc}; CONNECT_CLEANUP_FAILED: {cleanup_exc}") from exc
            raise

    def configure(self):
        # STS3215 operating modes and PID values from the pinned upstream.
        for bus in (self.bus1, self.bus2):
            bus.disable_torque()
            bus.configure_motors()
        for bus, motors in self._position_groups():
            for name in motors:
                for register, value in (("Operating_Mode", 0), ("P_Coefficient", 16),
                                        ("I_Coefficient", 0), ("D_Coefficient", 43)):
                    bus.write(register, name, value)
        for name in self.base_motors:
            self.bus2.write("Operating_Mode", name, 1)
        self.stop_base()
        # Deliberately no enable_torque: arms may be unsupported and wheels stay off.

    def _position_groups(self):
        return ((self.bus1, self.left_arm_motors), (self.bus2, self.right_arm_motors),
                (self.bus1, self.head_motors))

    @staticmethod
    def _read_positions(bus, motors):
        values = bus.sync_read("Present_Position", motors)
        if not isinstance(values, dict) or set(values) != set(motors):
            raise ValueError("JOINT_FEEDBACK_INVALID: missing or unexpected motors")
        for name, value in values.items():
            if (not isinstance(value, Real) or isinstance(value, bool)
                    or not math.isfinite(value) or abs(value) > 100
                    or (name.endswith("gripper") and value < 0)):
                raise ValueError(f"JOINT_FEEDBACK_INVALID: {name}")
        return values

    def arm_tabletop(self, *, stop_requested: Callable[[], bool]) -> None:
        if not self.is_connected or not self.is_calibrated:
            raise RuntimeError("ROBOT_NOT_READY")
        groups = ((self.bus1, self.left_arm_motors + self.head_motors),
                  (self.bus2, self.right_arm_motors))
        # Read every joint before any hold write. No stale target may be enabled.
        present = [(bus, motors, self._read_positions(bus, motors)) for bus, motors in groups]
        self.bus2.disable_torque(self.base_motors)
        self.stop_base()
        for bus, _, values in present:
            if stop_requested():
                raise RuntimeError("SAFETY_STOPPED")
            bus.sync_write("Goal_Position", values)
        for bus, motors, _ in present:
            if stop_requested():
                raise RuntimeError("SAFETY_STOPPED")
            bus.enable_torque(motors)

    def send_action(self, action):
        if not self.is_connected:
            raise RuntimeError("ROBOT_NOT_CONNECTED")
        limit = self.config.max_relative_target
        if (not isinstance(limit, Real) or isinstance(limit, bool)
                or not math.isfinite(limit) or limit <= 0):
            raise ValueError("MAX_RELATIVE_TARGET_INVALID")
        allowed = {f"{name}.pos" for _, motors in self._position_groups() for name in motors}
        if not isinstance(action, dict) or not action or set(action) - allowed:
            raise ValueError("ACTION_KEY_REJECTED")
        for key, value in action.items():
            if (not isinstance(value, Real) or isinstance(value, bool)
                    or not math.isfinite(value) or abs(value) > 100
                    or (key.endswith("gripper.pos") and value < 0)):
                raise ValueError(f"ACTION_VALUE_INVALID: {key}")
        writes, sent = [], {}
        for bus, motors in self._position_groups():
            requested = [name for name in motors if f"{name}.pos" in action]
            if not requested:
                continue
            present = self._read_positions(bus, requested)
            goals = {}
            for name in requested:
                # Action keys are suffixed; the Feetech read/write contract is not.
                key = f"{name}.pos"
                goals[name] = present[name] + min(limit, max(-limit, action[key] - present[name]))
                sent[key] = goals[name]
            writes.append((bus, goals))
        # A bad later joint must fail before any earlier bus receives a target.
        for bus, goals in writes:
            bus.sync_write("Goal_Position", goals)
        return sent

    def disconnect(self):
        errors = []
        if self.bus2.is_connected:
            try:
                self.stop_base()
            except Exception as exc:  # noqa: BLE001 - attempt remaining hardware cleanup
                errors.append(f"stop_base: {exc}")
        for bus in (self.bus1, self.bus2):
            if bus.is_connected:
                try:
                    bus.disconnect(disable_torque=True)
                except Exception as exc:  # noqa: BLE001 - attempt remaining hardware cleanup
                    errors.append(f"{bus.port}: {exc}")
        for name, camera in self.cameras.items():
            if camera.is_connected:
                try:
                    camera.disconnect()
                except Exception as exc:  # noqa: BLE001 - attempt remaining hardware cleanup
                    errors.append(f"{name}: {exc}")
        if errors:
            raise RuntimeError("DISCONNECT_FAILED: " + "; ".join(errors))
