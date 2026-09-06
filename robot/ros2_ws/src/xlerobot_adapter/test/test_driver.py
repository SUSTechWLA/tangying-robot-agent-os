import ast
import json
import logging
import threading
import time
from functools import cached_property
from itertools import chain
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from xlerobot_adapter.driver import XLeRobotDriver


def calibration_data():
    data = {}
    for side in ("left", "right"):
        for motor_id, joint in enumerate(
            ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"), 1
        ):
            data[f"{side}_arm_{joint}"] = {
                "id": motor_id, "drive_mode": 0, "homing_offset": 0, "range_min": 0, "range_max": 4095
            }
    for motor_id, name in ((7, "head_motor_1"), (8, "head_motor_2"),
                           (9, "base_left_wheel"), (10, "base_right_wheel")):
        data[name] = {"id": motor_id, "drive_mode": 0, "homing_offset": 0,
                          "range_min": 0, "range_max": 4095}
    return data


def write_calibration(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "tangying-xlerobot.json").write_text(json.dumps(calibration_data()))


def test_action_does_not_connect_or_arm_hardware_implicitly(tmp_path):
    constructed = []
    driver = XLeRobotDriver(upstream_root=tmp_path, calibration_root=tmp_path,
                           ports=("one", "two"), path_exists=lambda _: True,
                           robot_factory=lambda: constructed.append(True))
    result = driver.send_action({"left_arm_shoulder_pan.pos": 1.0})
    assert result.code == "ROBOT_NOT_ARMED"
    assert constructed == []


def test_operator_arm_is_required_again_after_stop_reset(tmp_path):
    driver, _robot = recording_driver(tmp_path, armed=False)
    assert driver.connect().success
    assert not driver.is_armed
    assert driver.arm(operator_present=False).code == "LOCAL_OPERATOR_REQUIRED"
    assert driver.arm(operator_present=True).success
    assert driver.is_armed
    driver.stop("operator stop")
    assert not driver.is_armed
    assert driver.reset_stop(operator_present=True)
    assert driver.send_action({"left_arm_shoulder_pan.pos": 1.0}).code == "ROBOT_NOT_ARMED"
    assert driver.arm(operator_present=True).success
    assert driver.send_action({"left_arm_shoulder_pan.pos": 1.0}).success


@pytest.mark.parametrize("mutation", ["missing_motor", "wrong_id", "bool", "inverted_range",
                                      "extra_motor", "raw_out_of_range", "non_integer"])
def test_calibration_schema_rejects_invalid_motor_data_before_connection(tmp_path, mutation):
    data = calibration_data()
    entry = data["left_arm_shoulder_pan"]
    if mutation == "missing_motor":
        del data["base_left_wheel"]
    elif mutation == "wrong_id":
        entry["id"] = 99
    elif mutation == "bool":
        entry["homing_offset"] = True
    elif mutation == "inverted_range":
        entry["range_min"] = entry["range_max"]
    elif mutation == "extra_motor":
        data["unknown_motor"] = entry.copy()
    elif mutation == "raw_out_of_range":
        entry["range_max"] = 4096
    else:
        entry["range_min"] = 0.5
    (tmp_path / "tangying-xlerobot.json").write_text(json.dumps(data))
    constructed = []
    driver = XLeRobotDriver(upstream_root=tmp_path, calibration_root=tmp_path,
                           ports=("one", "two"), path_exists=lambda _: True,
                           robot_factory=lambda: constructed.append(True))
    assert driver.validate_calibration_file().code == "CALIBRATION_INVALID"
    assert driver.connect().code == "CALIBRATION_INVALID"
    assert constructed == []


def test_driver_reports_unavailable_without_calibration(tmp_path):
    driver = XLeRobotDriver(
        upstream_root=tmp_path / "XLeRobot",
        calibration_root=tmp_path / "calibration",
        ports=("/dev/ttyACM0", "/dev/ttyACM1"),
        path_exists=lambda path: False,
    )
    capabilities = driver.capabilities()
    assert not capabilities.manipulation_ready
    assert "CALIBRATION_REQUIRED" in capabilities.blockers
    assert "UPSTREAM_NOT_FOUND" in capabilities.blockers


def test_driver_rejects_mobile_base_commands_in_tabletop_profile(tmp_path):
    driver = XLeRobotDriver(
        upstream_root=Path(tmp_path),
        calibration_root=Path(tmp_path),
        ports=("/dev/ttyACM0", "/dev/ttyACM1"),
        path_exists=lambda path: True,
        robot_factory=lambda: FakeRobot(),
    )
    result = driver.send_action({"x.vel": 0.1})
    assert not result.success
    assert result.code == "MOBILE_BASE_DISABLED"


def test_driver_requires_the_named_lerobot_calibration_file(tmp_path):
    checked = []

    def record_path(path):
        checked.append(Path(path))
        return True

    calibration_root = tmp_path / "calibration"
    driver = XLeRobotDriver(
        upstream_root=tmp_path / "XLeRobot",
        calibration_root=calibration_root,
        ports=("/dev/tangying-left", "/dev/tangying-right"),
        path_exists=record_path,
        robot_factory=lambda: FakeRobot(),
    )
    assert driver.capabilities().manipulation_ready
    assert calibration_root / "tangying-xlerobot.json" in checked


def test_driver_stop_latches_until_local_operator_reset(tmp_path):
    driver = XLeRobotDriver(
        upstream_root=tmp_path,
        calibration_root=tmp_path,
        ports=("/dev/ttyACM0", "/dev/ttyACM1"),
        path_exists=lambda path: True,
        robot_factory=lambda: FakeRobot(),
    )
    driver.stop("test stop")
    blocked = driver.send_action({"left_arm_shoulder_pan.pos": 10.0})
    assert not blocked.success
    assert blocked.code == "SAFETY_STOPPED"
    assert not driver.reset_stop(operator_present=False)
    assert driver.reset_stop(operator_present=True)
    assert driver.send_action({"left_arm_shoulder_pan.pos": 10.0}).code == "ROBOT_NOT_ARMED"
    write_calibration(tmp_path)
    assert driver.connect().success
    assert driver.arm(operator_present=True).success
    assert driver.send_action({"left_arm_shoulder_pan.pos": 10.0}).success


def test_driver_rejects_out_of_range_action_instead_of_clamping(tmp_path):
    driver = XLeRobotDriver(
        upstream_root=tmp_path,
        calibration_root=tmp_path,
        ports=("/dev/ttyACM0", "/dev/ttyACM1"),
        path_exists=lambda path: True,
        robot_factory=lambda: FakeRobot(),
    )
    result = driver.send_action({"left_arm_shoulder_pan.pos": 200.0})
    assert not result.success
    assert result.code == "ACTION_VALUE_OUT_OF_RANGE"


def test_driver_rejects_oversized_action_chunk(tmp_path):
    driver = XLeRobotDriver(
        upstream_root=tmp_path,
        calibration_root=tmp_path,
        ports=("/dev/ttyACM0", "/dev/ttyACM1"),
        path_exists=lambda path: True,
        robot_factory=lambda: FakeRobot(),
        max_action_chunk_length=2,
    )
    result = driver.execute_action_chunk(
        [{"left_arm_shoulder_pan.pos": 1.0}, {"left_arm_shoulder_pan.pos": 2.0}, {"left_arm_shoulder_pan.pos": 3.0}]
    )
    assert not result.success
    assert result.code == "ACTION_CHUNK_TOO_LONG"


def test_driver_validates_calibration_json_without_connecting(tmp_path):
    calibration_root = tmp_path / "calibration"
    calibration_root.mkdir()
    calibration_file = calibration_root / "tangying-xlerobot.json"
    calibration_file.write_text("{broken json")
    driver = XLeRobotDriver(
        upstream_root=tmp_path,
        calibration_root=calibration_root,
        ports=("/dev/ttyACM0", "/dev/ttyACM1"),
        path_exists=lambda path: True,
        robot_factory=lambda: FakeRobot(),
    )
    invalid = driver.validate_calibration_file()
    assert not invalid.success
    assert invalid.code == "CALIBRATION_INVALID"
    write_calibration(calibration_root)
    valid = driver.validate_calibration_file()
    assert valid.success


class FakeRobot:
    is_connected = True
    is_calibrated = True

    def send_action(self, action):
        return action

    def get_observation(self):
        return {"left_arm_gripper.pos": 10.0}

    def stop_base(self):
        pass

    def arm_tabletop(self, *, stop_requested):
        if stop_requested():
            raise RuntimeError("SAFETY_STOPPED")

    def disconnect(self):
        pass


class RecordingRobot(FakeRobot):
    def __init__(self):
        self.sent = []
        self.stop_count = 0

    def send_action(self, action):
        self.sent.append(action.copy())
        return action

    def stop_base(self):
        self.stop_count += 1


def recording_driver(tmp_path, robot=None, *, armed=True, **settings):
    write_calibration(tmp_path)
    robot = robot or RecordingRobot()
    driver = XLeRobotDriver(
        upstream_root=tmp_path,
        calibration_root=tmp_path,
        ports=("/dev/ttyACM0", "/dev/ttyACM1"),
        path_exists=lambda path: True,
        robot_factory=lambda: robot,
        **settings,
    )
    if armed and not driver._configuration_blockers():
        assert driver.connect().success
        assert driver.arm(operator_present=True).success
    return driver, robot


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "8", None])
def test_invalid_relative_limit_prevents_hardware_send(tmp_path, value):
    driver, robot = recording_driver(tmp_path, max_relative_target=value)
    assert "MAX_RELATIVE_TARGET_INVALID" in driver.capabilities().blockers
    result = driver.send_action({"left_arm_shoulder_pan.pos": 1.0})
    assert not result.success
    assert result.code == "ROBOT_NOT_READY"
    assert robot.sent == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "64", 1.5, None])
def test_invalid_chunk_limit_prevents_hardware_send(tmp_path, value):
    driver, robot = recording_driver(tmp_path, max_action_chunk_length=value)
    assert "MAX_ACTION_CHUNK_LENGTH_INVALID" in driver.capabilities().blockers
    result = driver.execute_action_chunk([{"left_arm_shoulder_pan.pos": 1.0}])
    assert not result.success
    assert result.code == "ROBOT_NOT_READY"
    assert robot.sent == []


@pytest.mark.parametrize(
    ("action", "code"),
    [
        ({}, "ACTION_MALFORMED"),
        ({1: 1.0}, "ACTION_KEY_REJECTED"),
        ({None: 1.0}, "ACTION_KEY_REJECTED"),
        ({"left_arm_unknown.pos": 1.0}, "ACTION_KEY_REJECTED"),
        ({"left_arm_1.pos": 1.0}, "ACTION_KEY_REJECTED"),
        ({"head_motor_3.pos": 1.0}, "ACTION_KEY_REJECTED"),
        ({"left_arm_shoulder_pan.pos": True}, "ACTION_VALUE_NOT_NUMERIC"),
        ({"left_arm_shoulder_pan.pos": "1.0"}, "ACTION_VALUE_NOT_NUMERIC"),
        ({"left_arm_shoulder_pan.pos": 10**1000}, "ACTION_VALUE_OUT_OF_RANGE"),
        ({"left_arm_shoulder_pan.pos": float("nan")}, "ACTION_VALUE_NOT_FINITE"),
        ({"left_arm_gripper.pos": -1}, "GRIPPER_VALUE_OUT_OF_RANGE"),
        ({"x.vel": 0.0}, "MOBILE_BASE_DISABLED"),
    ],
)
def test_invalid_later_step_rejects_entire_chunk_without_sending(tmp_path, action, code):
    driver, robot = recording_driver(tmp_path)
    result = driver.execute_action_chunk([{"left_arm_shoulder_pan.pos": 1.0}, action])
    assert not result.success
    assert result.code == code
    assert robot.sent == []


def test_chunk_uses_validated_snapshot_when_caller_changes_later_step(tmp_path):
    actions = [{"left_arm_shoulder_pan.pos": 1.0}, {"left_arm_shoulder_pan.pos": 2.0}]

    class MutatingRobot(RecordingRobot):
        def send_action(self, action):
            actions[1]["left_arm_shoulder_pan.pos"] = 90.0
            return super().send_action(action)

    driver, robot = recording_driver(tmp_path, MutatingRobot())
    result = driver.execute_action_chunk(actions)
    assert result.success
    assert robot.sent == [
        {"left_arm_shoulder_pan.pos": 1.0},
        {"left_arm_shoulder_pan.pos": 2.0},
    ]


@pytest.mark.parametrize(
    "acknowledgement",
    [None, {}, {"right_arm_gripper.pos": 1.0}, {"left_arm_shoulder_pan.pos": float("nan")},
     {"left_arm_shoulder_pan.pos": "1.0"}],
)
def test_invalid_send_acknowledgement_stops_and_aborts_chunk(tmp_path, acknowledgement):
    class IncompleteRobot(RecordingRobot):
        def send_action(self, action):
            super().send_action(action)
            return acknowledgement

    driver, robot = recording_driver(tmp_path, IncompleteRobot())
    result = driver.execute_action_chunk(
        [{"left_arm_shoulder_pan.pos": 1.0}, {"left_arm_shoulder_pan.pos": 2.0}]
    )
    assert not result.success
    assert result.code == "SEND_ACTION_INVALID_RESULT"
    assert driver.stop_requested
    assert robot.stop_count == 1
    assert robot.sent == [{"left_arm_shoulder_pan.pos": 1.0}]


def test_stop_during_last_send_does_not_report_chunk_success(tmp_path):
    class StoppingRobot(RecordingRobot):
        def send_action(self, action):
            sent = super().send_action(action)
            driver.stop("operator stop")
            return sent

    driver, robot = recording_driver(tmp_path, StoppingRobot())
    result = driver.execute_action_chunk([{"left_arm_shoulder_pan.pos": 1.0}])
    assert not result.success
    assert result.code == "SAFETY_STOPPED"
    assert robot.stop_count == 1


def test_stop_during_connect_prevents_first_action_send(tmp_path):
    class StoppingConnectionRobot(RecordingRobot):
        is_connected = False

        def connect(self, calibrate):
            assert not calibrate
            self.is_connected = True
            driver.stop("operator stop while connecting")

    driver, robot = recording_driver(tmp_path, StoppingConnectionRobot(), armed=False)
    result = driver.connect()
    assert not result.success
    assert result.code == "SAFETY_STOPPED"
    assert driver.arm(operator_present=True).code == "SAFETY_STOPPED"
    assert driver.send_action({"left_arm_shoulder_pan.pos": 1.0}).code == "SAFETY_STOPPED"
    assert robot.sent == []


def test_stop_latches_while_hardware_send_is_blocked_and_aborts_later_steps(tmp_path):
    sending = threading.Event()
    release_send = threading.Event()
    stop_started = threading.Event()
    latch_observed = threading.Event()
    results = []

    class BlockingRobot(RecordingRobot):
        def send_action(self, action):
            sent = super().send_action(action)
            sending.set()
            assert release_send.wait(3.0)
            return sent

    driver, robot = recording_driver(tmp_path, BlockingRobot())
    worker = threading.Thread(target=lambda: results.append(driver.execute_action_chunk(
        [{"left_arm_shoulder_pan.pos": 1.0}, {"left_arm_shoulder_pan.pos": 2.0}]
    )))

    def stop():
        stop_started.set()
        driver.stop("operator stop")

    stopper = threading.Thread(target=stop)

    def observe_latch():
        while not release_send.is_set():
            if driver.stop_requested:
                latch_observed.set()
                return
            release_send.wait(0.001)

    observer = threading.Thread(target=observe_latch)
    worker.start()
    try:
        assert sending.wait(1.0)
        stopper.start()
        assert stop_started.wait(1.0)
        observer.start()
        assert latch_observed.wait(0.5), "stop latch must not wait for the serial I/O lock"
    finally:
        release_send.set()
        worker.join(1.0)
        if stopper.ident is not None:
            stopper.join(1.0)
        if observer.ident is not None:
            observer.join(1.0)
    assert not worker.is_alive()
    assert not stopper.is_alive()
    assert not results[0].success
    assert results[0].code == "SAFETY_STOPPED"
    assert robot.sent == [{"left_arm_shoulder_pan.pos": 1.0}]
    assert robot.stop_count == 1


@pytest.mark.parametrize("failed_components", [("base",), ("bus1",), ("bus2",),
                                               ("base", "bus1", "bus2")])
def test_stop_attempts_every_bus_and_propagates_hardware_failures(tmp_path, failed_components):
    attempted = []

    def attempt(component):
        attempted.append(component)
        if component in failed_components:
            raise OSError(f"{component} disconnected")

    class Bus:
        def __init__(self, name):
            self.name = name

        def disable_torque(self):
            attempt(self.name)

    class FailingStopRobot(RecordingRobot):
        bus1 = Bus("bus1")
        bus2 = Bus("bus2")

        def stop_base(self):
            attempt("base")

    driver, _ = recording_driver(tmp_path, FailingStopRobot())
    assert driver.connect().success
    with pytest.raises(RuntimeError, match="STOP_FAILED") as failure:
        driver.stop("operator stop")
    assert attempted == ["base", "bus1", "bus2"]
    assert driver.stop_requested
    for component in failed_components:
        assert f"{component} disconnected" in str(failure.value)
        assert f"{component} disconnected" in driver.stop_reason


def test_observation_never_reconnects_a_previously_connected_robot(tmp_path):
    class ReconnectingRobot(RecordingRobot):
        def __init__(self):
            super().__init__()
            self.connect_calls = 0

        def connect(self, calibrate):
            self.connect_calls += 1
            self.is_connected = True

    driver, robot = recording_driver(tmp_path, ReconnectingRobot())
    assert driver.connect().success
    assert driver.observation() == {"left_arm_gripper.pos": 10.0}
    robot.is_connected = False
    with pytest.raises(RuntimeError, match="ROBOT_NOT_CONNECTED"):
        driver.observation()
    assert robot.connect_calls == 0
    assert robot.sent == []


def test_observation_does_not_read_after_stop(tmp_path):
    class ObservedRobot(RecordingRobot):
        def get_observation(self):
            raise AssertionError("stopped robot must not be read")

    driver, _ = recording_driver(tmp_path, ObservedRobot())
    assert driver.connect().success
    driver.stop("operator stop")
    with pytest.raises(RuntimeError, match="SAFETY_STOPPED"):
        driver.observation()


class ContractBus:
    """Hardware boundary double matching LeRobot 0.4.1 Feetech signatures."""

    events: ClassVar[list] = []

    def __init__(self, port, motors, calibration):
        self.port, self.motors, self.calibration = port, motors, calibration
        self.is_connected = False
        self.is_calibrated = True
        self.positions = dict.fromkeys(motors, 10.0)

    def connect(self):
        self.is_connected = True
        self.events.append((self.port, "connect"))

    def disable_torque(self, motors=None):
        self.events.append((self.port, "disable", motors))

    def enable_torque(self, motors=None):
        self.events.append((self.port, "enable", motors))

    def configure_motors(self):
        self.events.append((self.port, "configure"))

    def write(self, register, name, value):
        self.events.append((self.port, register, name, value))

    def sync_read(self, register, motors):
        self.events.append((self.port, "read", register, tuple(motors)))
        return {name: self.positions[name] for name in motors}

    def sync_write(self, register, values, **kwargs):
        assert set(values) <= set(self.motors)
        self.events.append((self.port, register, values.copy()))

    def write_calibration(self, values):
        self.calibration = values
        self.events.append((self.port, "calibration"))

    def disconnect(self, disable_torque=True):
        self.events.append((self.port, "disconnect", disable_torque))
        self.is_connected = False


@pytest.fixture
def real_upstream_class(tmp_path):
    """Execute the actual pinned class AST; only serial/camera dependencies are fake."""
    from xlerobot_adapter.upstream_compat import verify_source_file
    source_path = Path(__file__).parent / "fixtures/xlerobot_2wheels.py.txt"
    verify_source_file(source_path, "robot")
    source = source_path.read_text()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "XLerobot2Wheels")

    class RobotBase:
        def __init__(self, config):
            self.calibration = {k: SimpleNamespace(**v) for k, v in calibration_data().items()}
            self.calibration_fpath = tmp_path / "tangying-xlerobot.json"

    def safe_position(pairs, limit):
        return {key: present + min(limit, max(-limit, goal - present))
                for key, (goal, present) in pairs.items()}

    import __future__

    import numpy as np

    namespace = {
        "Robot": RobotBase, "XLerobot2WheelsConfig": object,
        "MotorNormMode": SimpleNamespace(DEGREES="degrees", RANGE_M100_100="range",
                                      RANGE_0_100="gripper"),
        "Motor": lambda motor_id, model, mode: SimpleNamespace(id=motor_id),
        "FeetechMotorsBus": ContractBus, "make_cameras_from_configs": lambda _: {},
        "cached_property": cached_property, "chain": chain, "logger": logging.getLogger(__name__),
        "time": time, "np": np, "DeviceNotConnectedError": RuntimeError,
        "DeviceAlreadyConnectedError": RuntimeError, "ensure_safe_goal_position": safe_position,
        "OperatingMode": SimpleNamespace(POSITION=SimpleNamespace(value=0),
                                      VELOCITY=SimpleNamespace(value=1)),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source_path), "exec",  # noqa: S102 - hash-verified fixture
                 flags=__future__.annotations.compiler_flag), namespace)
    ContractBus.events = []
    return namespace["XLerobot2Wheels"], source_path


def contract_robot(upstream_class, compatible=True):
    if compatible:
        from xlerobot_adapter.upstream_compat import _TabletopCompatibility
        cls = type("ContractRobot", (_TabletopCompatibility, upstream_class), {})
    else:
        cls = upstream_class
    config = SimpleNamespace(port1="left", port2="right", teleop_keys={}, use_degrees=False,
                             cameras={}, max_relative_target=8.0, wheel_radius=0.05,
                             wheelbase=0.25, disable_torque_on_disconnect=True)
    return cls(config)


def test_real_pinned_source_reproduces_suffix_key_and_duplicate_bus_defects(real_upstream_class):
    cls, _ = real_upstream_class
    robot = contract_robot(cls, compatible=False)
    robot.bus1.is_connected = robot.bus2.is_connected = True
    with pytest.raises(KeyError, match="left_arm_shoulder_pan.pos"):
        robot.send_action({"left_arm_shoulder_pan.pos": 30.0})
    robot.configure()
    configured = [event for event in ContractBus.events if event[1] == "configure"]
    assert configured == [("right", "configure"), ("right", "configure")]


def test_compatibility_clamps_suffix_actions_and_never_writes_wheel_motion(real_upstream_class):
    robot = contract_robot(real_upstream_class[0])
    robot.bus1.is_connected = robot.bus2.is_connected = True
    sent = robot.send_action({"left_arm_shoulder_pan.pos": 30.0,
                             "right_arm_gripper.pos": 0.0, "head_motor_1.pos": 11.0})
    assert sent == {"left_arm_shoulder_pan.pos": 18.0, "right_arm_gripper.pos": 2.0,
                    "head_motor_1.pos": 11.0}
    writes = [event for event in ContractBus.events if event[1].startswith("Goal_")]
    assert writes == [("left", "Goal_Position", {"left_arm_shoulder_pan": 18.0}),
                      ("right", "Goal_Position", {"right_arm_gripper": 2.0}),
                      ("left", "Goal_Position", {"head_motor_1": 11.0})]


def test_compatible_connect_configures_both_buses_without_enabling_torque(real_upstream_class):
    robot = contract_robot(real_upstream_class[0])
    robot.connect(calibrate=False)
    assert robot.is_connected
    configured = [event for event in ContractBus.events if event[1] == "configure"]
    assert configured == [("left", "configure"), ("right", "configure")]
    assert not any(event[1] in ("enable", "Goal_Position") for event in ContractBus.events)


def test_compatible_arm_holds_current_pose_before_enabling_only_arm_and_head(real_upstream_class):
    robot = contract_robot(real_upstream_class[0])
    robot.connect(calibrate=False)
    ContractBus.events.clear()
    robot.arm_tabletop(stop_requested=lambda: False)
    enabled = [event for event in ContractBus.events if event[1] == "enable"]
    assert len(enabled) == 2
    assert set(enabled[0][2]) == set(robot.left_arm_motors + robot.head_motors)
    assert set(enabled[1][2]) == set(robot.right_arm_motors)
    first_enable = next(i for i, event in enumerate(ContractBus.events) if event[1] == "enable")
    writes = [event for event in ContractBus.events[:first_enable] if event[1] == "Goal_Position"]
    assert len(writes) == 2
    assert all(value == 10.0 for event in writes for value in event[2].values())
    assert all(not any(name.startswith("base") for name in event[2]) for event in enabled)


def test_compatibility_rejects_bad_feedback_before_any_goal_write(real_upstream_class):
    robot = contract_robot(real_upstream_class[0])
    robot.bus1.is_connected = robot.bus2.is_connected = True
    robot.bus2.positions["right_arm_gripper"] = float("nan")
    with pytest.raises(ValueError, match="FEEDBACK"):
        robot.send_action({"left_arm_shoulder_pan.pos": 30.0, "right_arm_gripper.pos": 0.0})
    assert not any(event[1].startswith("Goal_") for event in ContractBus.events)


def test_compatibility_disconnect_closes_both_buses_even_when_first_stop_fails(real_upstream_class):
    robot = contract_robot(real_upstream_class[0])
    robot.bus1.is_connected = robot.bus2.is_connected = True

    def broken_stop(*_args, **_kwargs):
        raise OSError("serial failure")

    robot.bus2.sync_write = broken_stop
    with pytest.raises(RuntimeError, match="serial failure"):
        robot.disconnect()
    assert not robot.bus1.is_connected
    assert not robot.bus2.is_connected


def test_source_drift_is_rejected_before_robot_creation(real_upstream_class, tmp_path):
    from xlerobot_adapter.upstream_compat import verify_source_file
    _, source_path = real_upstream_class
    verify_source_file(source_path, "robot")
    drifted = tmp_path / "robot.py"
    drifted.write_text(source_path.read_text().replace("self.bus2.configure_motors()",
                                                      "self.bus1.configure_motors()", 1))
    with pytest.raises(RuntimeError, match="UPSTREAM_SOURCE_UNSUPPORTED"):
        verify_source_file(drifted, "robot")


def test_arm_never_constructs_disconnected_hardware(tmp_path):
    constructed = []
    driver = XLeRobotDriver(upstream_root=tmp_path, calibration_root=tmp_path,
                           ports=("one", "two"), path_exists=lambda _: True,
                           robot_factory=lambda: constructed.append(True))
    assert driver.arm(operator_present=True).code == "ROBOT_NOT_CONNECTED"
    assert constructed == []


def test_failed_arm_latches_stop_and_never_sends_action(tmp_path):
    class FailedArmRobot(RecordingRobot):
        def arm_tabletop(self, *, stop_requested):
            raise OSError("second torque write failed")

    driver, robot = recording_driver(tmp_path, FailedArmRobot(), armed=False)
    assert driver.connect().success
    result = driver.arm(operator_present=True)
    assert result.code == "ARM_FAILED"
    assert driver.stop_requested
    assert not driver.is_armed
    assert robot.stop_count == 1
    assert driver.send_action({"head_motor_1.pos": 1.0}).code == "SAFETY_STOPPED"
    assert robot.sent == []


def test_stop_during_arm_is_not_overwritten_by_arm_success(tmp_path):
    class StoppedArmRobot(RecordingRobot):
        def arm_tabletop(self, *, stop_requested):
            driver.stop("operator interrupted arm")

    driver, robot = recording_driver(tmp_path, StoppedArmRobot(), armed=False)
    assert driver.connect().success
    assert driver.arm(operator_present=True).code == "SAFETY_STOPPED"
    assert driver.stop_requested
    assert not driver.is_armed
    assert robot.sent == []


def test_disconnect_revokes_arming(tmp_path):
    driver, robot = recording_driver(tmp_path)
    driver.disconnect()
    assert not driver.is_armed
    assert driver.send_action({"head_motor_1.pos": 1.0}).code == "ROBOT_NOT_ARMED"
    assert robot.sent == []


def test_connection_loss_does_not_reconnect_or_remain_armed(tmp_path):
    driver, robot = recording_driver(tmp_path)
    robot.is_connected = False
    assert driver.send_action({"head_motor_1.pos": 1.0}).code == "ROBOT_NOT_CONNECTED"
    assert not driver.is_armed
    assert robot.sent == []


@pytest.mark.parametrize("failure", ["second_connect", "calibration_restore", "configure"])
def test_compatible_connect_failure_closes_all_open_ports_without_enabling(
    real_upstream_class, failure
):
    robot = contract_robot(real_upstream_class[0])

    def fail(*_args, **_kwargs):
        raise OSError(failure)

    if failure == "second_connect":
        robot.bus2.connect = fail
    elif failure == "calibration_restore":
        robot.bus2.write_calibration = fail
    else:
        robot.bus1.configure_motors = fail
    with pytest.raises(OSError, match=failure):
        robot.connect(calibrate=False)
    assert not robot.bus1.is_connected
    assert not robot.bus2.is_connected
    assert not any(event[1] == "enable" for event in ContractBus.events)


def test_arm_stops_before_second_bus_enable_when_interrupted(real_upstream_class):
    robot = contract_robot(real_upstream_class[0])
    robot.connect(calibrate=False)
    ContractBus.events.clear()

    def stopped():
        return any(event[1] == "enable" for event in ContractBus.events)

    with pytest.raises(RuntimeError, match="SAFETY_STOPPED"):
        robot.arm_tabletop(stop_requested=stopped)
    enabled = [event for event in ContractBus.events if event[1] == "enable"]
    assert len(enabled) == 1
    assert enabled[0][0] == "left"


def test_factory_verifies_loaded_config_before_constructing_robot(
    real_upstream_class, tmp_path, monkeypatch
):
    from xlerobot_adapter import upstream_compat
    _, source_path = real_upstream_class
    bad_config = tmp_path / "config.py"
    bad_config.write_text("# another hardware revision")
    constructed = []
    modules = {
        "lerobot.robots.xlerobot_2wheels.config_xlerobot_2wheels": SimpleNamespace(__file__=bad_config),
        "lerobot.robots.xlerobot_2wheels.xlerobot_2wheels": SimpleNamespace(
            __file__=source_path, XLerobot2Wheels=lambda _: constructed.append(True)),
    }
    monkeypatch.setattr(upstream_compat.importlib.metadata, "version", lambda _: "0.4.1")
    monkeypatch.setattr(upstream_compat.importlib, "import_module", modules.__getitem__)
    with pytest.raises(RuntimeError, match="UPSTREAM_SOURCE_UNSUPPORTED: config"):
        upstream_compat.create_compatible_robot(object())
    assert constructed == []


def test_factory_rejects_unverified_lerobot_version_before_loading_robot(monkeypatch):
    from xlerobot_adapter import upstream_compat
    imported = []
    monkeypatch.setattr(upstream_compat.importlib.metadata, "version", lambda _: "0.5.0")
    monkeypatch.setattr(upstream_compat.importlib, "import_module", lambda name: imported.append(name))
    with pytest.raises(RuntimeError, match="LEROBOT_VERSION_UNSUPPORTED"):
        upstream_compat.create_compatible_robot(object())
    assert imported == []


def test_compatible_empty_calibration_connection_allows_explicit_manual_calibration(
    real_upstream_class,
):
    robot = contract_robot(real_upstream_class[0])
    robot.calibration = {}
    robot.bus1.is_calibrated = robot.bus2.is_calibrated = False
    robot.connect(calibrate=False)
    assert robot.is_connected
    assert not any(event[1] in ("enable", "calibration") for event in ContractBus.events)
    with pytest.raises(RuntimeError, match="ROBOT_NOT_READY"):
        robot.arm_tabletop(stop_requested=lambda: False)


def test_real_source_factory_and_driver_require_separate_connect_and_arm(
    real_upstream_class, tmp_path, monkeypatch
):
    from xlerobot_adapter import upstream_compat
    cls, source_path = real_upstream_class
    config_path = source_path.with_name("config_xlerobot_2wheels.py.txt")
    modules = {
        "lerobot.robots.xlerobot_2wheels.config_xlerobot_2wheels": SimpleNamespace(
            __file__=config_path,
            XLerobot2WheelsConfig=lambda **settings: SimpleNamespace(
                **settings, teleop_keys={}, use_degrees=False, cameras={},
                wheel_radius=0.05, wheelbase=0.25, disable_torque_on_disconnect=True)),
        "lerobot.robots.xlerobot_2wheels.xlerobot_2wheels": SimpleNamespace(
            __file__=source_path, XLerobot2Wheels=cls),
    }
    monkeypatch.setattr(upstream_compat.importlib.metadata, "version", lambda _: "0.4.1")
    monkeypatch.setattr(upstream_compat.importlib, "import_module", modules.__getitem__)
    write_calibration(tmp_path)
    driver = XLeRobotDriver(upstream_root=tmp_path, calibration_root=tmp_path,
                           ports=("left", "right"), path_exists=lambda _: True)
    assert driver.capabilities().manipulation_ready
    assert ContractBus.events == []
    with pytest.raises(RuntimeError, match="ROBOT_NOT_CONNECTED"):
        driver.observation()
    assert driver.send_action({"head_motor_1.pos": 30.0}).code == "ROBOT_NOT_ARMED"
    assert ContractBus.events == []
    assert driver.connect().success
    assert driver.is_connected
    assert driver.observation()["head_motor_1.pos"] == 10.0
    assert not driver.is_armed
    assert not any(event[1] == "enable" for event in ContractBus.events)
    assert driver.arm(operator_present=True).success
    assert driver.send_action({"head_motor_1.pos": 30.0}).action_sent == {"head_motor_1.pos": 18.0}
    driver.stop("fixture emergency stop")
    assert driver.reset_stop(operator_present=True)
    assert driver.send_action({"head_motor_1.pos": 30.0}).code == "ROBOT_NOT_ARMED"
    driver.disconnect()
    assert not driver.is_connected


def test_interrupted_connect_releases_partial_connections(real_upstream_class):
    robot = contract_robot(real_upstream_class[0])

    def interrupt():
        raise KeyboardInterrupt

    robot.configure = interrupt
    with pytest.raises(KeyboardInterrupt):
        robot.connect(calibrate=False)
    assert not robot.bus1.is_connected
    assert not robot.bus2.is_connected


def test_interrupted_arm_latches_stop_and_disables_already_enabled_bus(
    real_upstream_class, tmp_path
):
    robot = contract_robot(real_upstream_class[0])
    driver, _ = recording_driver(tmp_path, robot, armed=False)
    assert driver.connect().success
    ContractBus.events.clear()

    def interrupt(_motors):
        raise KeyboardInterrupt

    robot.bus2.enable_torque = interrupt
    with pytest.raises(KeyboardInterrupt):
        driver.arm(operator_present=True)
    assert driver.stop_requested
    assert not driver.is_armed
    first_enable = next(i for i, event in enumerate(ContractBus.events) if event[1] == "enable")
    assert ("left", "disable", None) in ContractBus.events[first_enable:]
    assert ("right", "disable", None) in ContractBus.events[first_enable:]


def test_invalid_calibration_encoding_returns_failure_before_hardware_construction(tmp_path):
    (tmp_path / "tangying-xlerobot.json").write_bytes(b"\xff\xfe")
    constructed = []
    driver = XLeRobotDriver(upstream_root=tmp_path, calibration_root=tmp_path,
                           ports=("one", "two"), path_exists=lambda _: True,
                           robot_factory=lambda: constructed.append(True))
    assert driver.connect().code == "CALIBRATION_INVALID"
    assert constructed == []
