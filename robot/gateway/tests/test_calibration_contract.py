"""The calibration contract: one document for a simulated and a real unit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tangying_robot_gateway.calibration import (
    ARM_JOINTS,
    CAMERA_NAMES,
    MOTOR_IDS,
    CalibrationError,
    calibration_revision,
    describe_calibration,
    gripper_motors,
    load_calibration,
    motor_layout,
    save_calibration,
    validate_calibration,
)


def a_calibration(**overrides) -> dict:
    document = {
        "schemaVersion": "robot.calibration.v1",
        "robotId": "xlerobot-01",
        "adapterId": "xlerobot",
        "source": "measured",
        "updatedAtUnixMs": 1_700_000_000_000,
        "motors": {name: {"id": servo_id, "drive_mode": 0, "homing_offset": 0,
                          "range_min": 0, "range_max": 4095}
                   for name, servo_id in MOTOR_IDS.items()},
        "cameras": {
            "head-rgbd": {
                "sourceId": "xlerobot-01/head-rgbd", "width": 640, "height": 480,
                "intrinsics": {"fx": 610.0, "fy": 609.0, "cx": 319.5, "cy": 239.5},
                "distortion": {"model": "plumb_bob", "coefficients": [0.01, -0.02, 0.0, 0.0, 0.0]},
                "extrinsics": {"parentLink": "head_tilt_link", "xyz": [0.05, 0.0, 0.06],
                               "rpy": [1.5708, 0.0, 0.0]},
            },
            "base-rgbd": {
                "sourceId": "xlerobot-01/base-rgbd", "width": 640, "height": 480,
                "intrinsics": {"fx": 320.0, "fy": 320.0, "cx": 319.5, "cy": 239.5},
                "distortion": {"model": "none", "coefficients": []},
                "extrinsics": {"parentLink": "chassis", "xyz": [0.0, 0.28, 0.35],
                               "rpy": [0.0, 0.0, 0.0]},
            },
        },
        "geometry": {"gripper": {"openM": 0.081, "closedM": 0.0}},
        "safety": {"maxRelativeTargetDeg": 8.0, "maxActionChunkLength": 64,
                   "maxLinearSpeedMPerS": 0.05, "maxAngularSpeedRadPerS": 0.2},
    }
    document.update(overrides)
    return document


def test_the_reference_unit_is_two_arms_two_grippers_and_two_rgbd():
    assert len(MOTOR_IDS) == 16, "2 arms x 6 joints + 2 head + 2 wheels"
    layout = motor_layout()
    assert [group["group"] for group in layout] == ["左臂", "右臂", "头部与底盘"]
    for group, side in zip(layout[:2], ("left", "right"), strict=True):
        names = [motor["name"] for motor in group["motors"]]
        assert names == [f"{side}_arm_{joint}" for joint in ARM_JOINTS]
        assert group["bus"] == side
        # Both arms reuse servo ids 1..6; the bus is what separates them.
        assert [motor["servoId"] for motor in group["motors"]] == [1, 2, 3, 4, 5, 6]
        assert [motor["isGripper"] for motor in group["motors"]] == [False] * 5 + [True]
    assert gripper_motors() == {"left": "left_arm_gripper", "right": "right_arm_gripper"}
    assert CAMERA_NAMES == ("head-rgbd", "base-rgbd")


def test_a_complete_calibration_validates_and_normalizes():
    normalized = validate_calibration(a_calibration())
    assert set(normalized["motors"]) == set(MOTOR_IDS)
    assert set(normalized["cameras"]) == set(CAMERA_NAMES)
    assert normalized["cameras"]["head-rgbd"]["intrinsics"]["fx"] == pytest.approx(610.0)
    assert describe_calibration(normalized)["valid"] is True


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"motors": {"left_arm_shoulder_pam": {"id": 1, "drive_mode": 0, "homing_offset": 0,
                                               "range_min": 0, "range_max": 4095}}}, "UNKNOWN_MOTOR"),
        ({"cameras": {"side-rgbd": {"sourceId": "x", "width": 640, "height": 480,
                                    "intrinsics": {"fx": 1.0, "fy": 1.0, "cx": 1.0, "cy": 1.0},
                                    "distortion": {"model": "none", "coefficients": []},
                                    "extrinsics": {"parentLink": "chassis", "xyz": [0, 0, 0],
                                                   "rpy": [0, 0, 0]}}}}, "UNKNOWN_FIELD"),
        ({"schemaVersion": "robot.calibration.v2"}, "SCHEMA_MISMATCH"),
        ({"source": "guessed"}, "INVALID_VALUE"),
    ],
)
def test_a_typo_cannot_silently_do_nothing(change, code):
    """A wrong field name on a physical robot must fail loudly, not be ignored."""
    document = a_calibration(**change)
    result = describe_calibration(document)
    assert result["valid"] is False
    assert result["code"] == code


def test_servo_numbers_must_stay_inside_the_encoder_range():
    document = a_calibration()
    document["motors"]["left_arm_gripper"]["homing_offset"] = 5000
    with pytest.raises(CalibrationError) as failure:
        validate_calibration(document)
    assert failure.value.code == "OUT_OF_RANGE"

    document = a_calibration()
    document["motors"]["right_arm_elbow_flex"]["range_min"] = 3000
    document["motors"]["right_arm_elbow_flex"]["range_max"] = 2000
    with pytest.raises(CalibrationError):
        validate_calibration(document)


def test_camera_intrinsics_must_describe_the_declared_image():
    document = a_calibration()
    document["cameras"]["head-rgbd"]["intrinsics"]["cx"] = 900.0
    with pytest.raises(CalibrationError) as failure:
        validate_calibration(document)
    assert failure.value.code == "OUT_OF_RANGE"

    document = a_calibration()
    document["cameras"]["head-rgbd"]["distortion"] = {"model": "plumb_bob", "coefficients": [0.1]}
    with pytest.raises(CalibrationError) as failure:
        validate_calibration(document)
    assert "plumb_bob" in failure.value.message


def test_revision_tracks_content_not_the_save_time():
    first = validate_calibration(a_calibration())
    second = validate_calibration(a_calibration(updatedAtUnixMs=1_800_000_000_000))
    assert calibration_revision(first) == calibration_revision(second), "same numbers, same identity"

    changed = a_calibration()
    changed["motors"]["left_arm_gripper"]["homing_offset"] = -132
    assert calibration_revision(validate_calibration(changed)) != calibration_revision(first)


def test_saving_is_atomic_and_refuses_to_overwrite_someone_elses_measurement(tmp_path: Path):
    path = tmp_path / "calibration.json"
    document = a_calibration()
    saved = save_calibration(path, document)
    revision = calibration_revision(saved)
    assert load_calibration(path) == saved
    assert json.loads(path.read_text())["schemaVersion"] == "robot.calibration.v1"
    assert not list(tmp_path.glob("*.tmp")), "no temporary file may survive a save"

    edited = a_calibration()
    edited["motors"]["right_arm_wrist_roll"]["homing_offset"] = 44
    with pytest.raises(CalibrationError) as failure:
        save_calibration(path, edited, expected_revision="deadbeef")
    assert failure.value.code == "REVISION_CONFLICT"
    assert load_calibration(path) == saved, "the stored measurement must be untouched"

    save_calibration(path, edited, expected_revision=revision)
    assert load_calibration(path)["motors"]["right_arm_wrist_roll"]["homing_offset"] == 44


def test_absent_calibration_is_reported_as_absent(tmp_path: Path):
    assert load_calibration(tmp_path / "missing.json") is None
