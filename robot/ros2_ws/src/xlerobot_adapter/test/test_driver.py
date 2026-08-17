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
