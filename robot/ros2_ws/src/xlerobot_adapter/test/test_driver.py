from pathlib import Path

from xlerobot_adapter.driver import XLeRobotDriver


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
    blocked = driver.send_action({"left_arm_1.pos": 10.0})
    assert not blocked.success
    assert blocked.code == "SAFETY_STOPPED"
    assert not driver.reset_stop(operator_present=False)
    assert driver.reset_stop(operator_present=True)
    assert driver.send_action({"left_arm_1.pos": 10.0}).success


def test_driver_rejects_out_of_range_action_instead_of_clamping(tmp_path):
    driver = XLeRobotDriver(
        upstream_root=tmp_path,
        calibration_root=tmp_path,
        ports=("/dev/ttyACM0", "/dev/ttyACM1"),
        path_exists=lambda path: True,
        robot_factory=lambda: FakeRobot(),
    )
    result = driver.send_action({"left_arm_1.pos": 200.0})
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
        [{"left_arm_1.pos": 1.0}, {"left_arm_1.pos": 2.0}, {"left_arm_1.pos": 3.0}]
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
    calibration_file.write_text('{"homing_offset": [0, 0, 0]}')
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

    def disconnect(self):
        pass
