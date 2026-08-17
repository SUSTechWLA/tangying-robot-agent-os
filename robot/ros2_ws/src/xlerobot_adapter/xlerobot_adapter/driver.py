from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

PINNED_XLEROBOT_COMMIT = "3d14695e40c9c68229c0aacffca6053c75cd3eb6"


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
    ):
        self.upstream_root = Path(upstream_root)
        self.calibration_root = Path(calibration_root)
        self.ports = ports
        self.path_exists = path_exists or (lambda path: Path(path).exists())
        self.robot_factory = robot_factory
        self.max_relative_target = max_relative_target
        self._robot = None

    def capabilities(self) -> DriverCapabilities:
        blockers = []
        if not self.path_exists(self.upstream_root):
            blockers.append("UPSTREAM_NOT_FOUND")
        if not self.path_exists(self.calibration_root):
            blockers.append("CALIBRATION_REQUIRED")
        missing_ports = [port for port in self.ports if not self.path_exists(port)]
        if missing_ports:
            blockers.append("SERIAL_PORTS_UNAVAILABLE")
        if self.robot_factory is None:
            try:
                importlib.import_module("lerobot.robots.xlerobot_2wheels.xlerobot_2wheels")
            except ImportError:
                blockers.append("XLEROBOT_LEROBOT_INTEGRATION_MISSING")
        return DriverCapabilities(manipulation_ready=not blockers, blockers=tuple(blockers))

    def connect(self) -> DriverResult:
        capabilities = self.capabilities()
        if not capabilities.manipulation_ready:
            return DriverResult(False, "ROBOT_NOT_READY", ",".join(capabilities.blockers))
        if self._robot is None:
            self._robot = self.robot_factory() if self.robot_factory else self._create_upstream_robot()
        if not self._robot.is_connected:
            self._robot.connect(calibrate=False)
        if not self._robot.is_calibrated:
            return DriverResult(False, "CALIBRATION_REQUIRED")
        return DriverResult(True)

    def observation(self) -> dict:
        connected = self.connect()
        if not connected.success:
            raise RuntimeError(f"{connected.code}: {connected.message}")
        return self._robot.get_observation()

    def send_action(self, action: dict[str, float]) -> DriverResult:
        if any(key in action and abs(float(action[key])) > 0 for key in ("x.vel", "theta.vel")):
            return DriverResult(False, "MOBILE_BASE_DISABLED")
        invalid = [key for key in action if not self._allowed_action_key(key)]
        if invalid:
            return DriverResult(False, "ACTION_KEY_REJECTED", ",".join(invalid))
        connected = self.connect()
        if not connected.success:
            return connected
        bounded = {key: self._bound_value(key, float(value)) for key, value in action.items()}
        sent = self._robot.send_action(bounded)
        return DriverResult(True, action_sent={key: float(value) for key, value in sent.items()})

    def execute_action_chunk(self, actions: list[dict[str, float]]) -> DriverResult:
        if not actions:
            return DriverResult(False, "POLICY_ACTION_CHUNK_REQUIRED")
        last = DriverResult(True)
        for action in actions:
            last = self.send_action(action)
            if not last.success:
                return last
        return last

    def stop(self, reason: str) -> None:
        if self._robot is None:
            return
        if hasattr(self._robot, "stop_base"):
            self._robot.stop_base()
        for bus_name in ("bus1", "bus2"):
            bus = getattr(self._robot, bus_name, None)
            if bus is not None and hasattr(bus, "disable_torque"):
                bus.disable_torque()

    def disconnect(self) -> None:
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
            max_relative_target=self.max_relative_target,
        )
        return robot_module.XLerobot2Wheels(config)

    @staticmethod
    def _allowed_action_key(key: str) -> bool:
        return key.endswith(".pos") and key.startswith(("left_arm_", "right_arm_", "head_"))

    def _bound_value(self, key: str, value: float) -> float:
        if key.endswith("gripper.pos"):
            return min(100.0, max(0.0, value))
        return min(100.0, max(-100.0, value))
