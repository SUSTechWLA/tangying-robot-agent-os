"""The guided calibration flow a non-technical owner follows.

These tests are written from the user's side: what they are told to do, what the
machine records, and what happens when they stop halfway or the hardware is not
ready. Nothing here needs a robot — the simulated backend implements the same
small interface the real one does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tangying_robot_gateway.calibration import (
    MOTOR_IDS,
    calibration_revision,
    describe_calibration,
)
from tangying_robot_gateway.calibration_wizard import (
    CalibrationError,
    CalibrationWizard,
    SimulatedCalibrationHardware,
    build_steps,
)

CAMERAS = {
    "head-rgbd": {
        "sourceId": "xlerobot-01/head-rgbd", "width": 640, "height": 480,
        "intrinsics": {"fx": 610.0, "fy": 609.0, "cx": 319.5, "cy": 239.5},
        "distortion": {"model": "none", "coefficients": []},
        "extrinsics": {"parentLink": "head_tilt_link", "xyz": [0.05, 0.0, 0.06],
                       "rpy": [1.5708, 0.0, 0.0]},
    },
}
GEOMETRY = {"gripper": {"openM": 0.081, "closedM": 0.0}}
SAFETY = {"maxRelativeTargetDeg": 8.0, "maxActionChunkLength": 64,
          "maxLinearSpeedMPerS": 0.05, "maxAngularSpeedRadPerS": 0.2}


def a_wizard(tmp_path: Path, hardware=None, **kwargs) -> CalibrationWizard:
    kwargs.setdefault("base_document", {"cameras": CAMERAS, "geometry": GEOMETRY, "safety": SAFETY})
    return CalibrationWizard(
        hardware or SimulatedCalibrationHardware(),
        robot_id="xlerobot-01", adapter_id="xlerobot",
        session_path=tmp_path / "session.json", **kwargs,
    )


def run_to_completion(wizard: CalibrationWizard) -> None:
    step = wizard.next_step()
    assert step is not None
    wizard.acknowledge(step.id, {check: True for check in step.requires})
    while (step := wizard.next_step()) is not None:
        wizard.confirm(step.id)


def test_the_plan_covers_both_arms_both_grippers_and_the_head_and_base():
    steps = build_steps()
    zero_steps = {step.motor: step for step in steps if step.kind == "zero"}
    assert set(zero_steps) == set(MOTOR_IDS), "every servo gets its own capture step"
    for side, label in (("left", "左臂"), ("right", "右臂")):
        gripper = zero_steps[f"{side}_arm_gripper"]
        assert gripper.group == label
        assert "夹爪" in gripper.title
        assert "两个指头" in gripper.instruction
    # Travel is sampled per arm, not per joint: one sweep teaches six joints.
    sweeps = [step for step in steps if step.kind == "travel"]
    assert [step.group for step in sweeps] == ["左臂", "右臂"]
    assert all(len(step.motors) == 6 for step in sweeps)


def test_every_step_is_written_for_a_person_not_for_an_engineer():
    for step in build_steps():
        assert step.title and step.instruction and step.detail
        assert "用手" in step.instruction or step.kind in {"preflight", "travel", "review"}
        # No joint identifier or register name should leak into the instruction.
        assert "homing_offset" not in step.instruction
        assert "_arm_" not in step.instruction
        assert "range_min" not in step.instruction


def test_a_session_is_guided_step_by_step_and_reports_progress(tmp_path: Path):
    wizard = a_wizard(tmp_path)
    status = wizard.status()
    assert status["total"] == len(build_steps())
    assert status["completed"] == 0
    assert status["next"]["kind"] == "preflight"
    assert "尚未开始" in status["summary"]

    wizard.acknowledge("preflight", {check: True for check in build_steps()[0].requires})
    first_zero = wizard.next_step()
    assert first_zero is not None and first_zero.kind == "zero"
    wizard.confirm(first_zero.id)

    recorded = wizard.session.motors[first_zero.motor]
    assert recorded["id"] == MOTOR_IDS[first_zero.motor]
    assert -2048 <= recorded["homing_offset"] <= 2047
    status = wizard.status()
    assert status["completed"] == 2
    assert "下一步" in status["summary"]


def test_the_recorded_zero_offset_is_the_servo_reading_minus_its_centre(tmp_path: Path):
    class FixedReading(SimulatedCalibrationHardware):
        def read_motor_position(self, motor: str) -> int:
            return 2600

    wizard = a_wizard(tmp_path, FixedReading())
    wizard.acknowledge("preflight", {check: True for check in build_steps()[0].requires})
    step = wizard.next_step()
    wizard.confirm(step.id)
    assert wizard.session.motors[step.motor]["homing_offset"] == 2600 - 2048


def test_the_wizard_says_up_front_that_it_needs_camera_parameters(tmp_path: Path):
    """Otherwise the missing block is only discovered after the whole session."""
    with pytest.raises(CalibrationError) as failure:
        CalibrationWizard(SimulatedCalibrationHardware(), robot_id="xlerobot-01",
                          adapter_id="xlerobot", session_path=tmp_path / "session.json")
    assert failure.value.code == "CAMERAS_REQUIRED"
    assert "相机" in failure.value.message


def test_preflight_refuses_while_a_safety_check_is_unconfirmed(tmp_path: Path):
    wizard = a_wizard(tmp_path)
    checks = {check: True for check in build_steps()[0].requires}
    checks["estop_reachable"] = False
    with pytest.raises(CalibrationError) as failure:
        wizard.acknowledge("preflight", checks)
    assert failure.value.code == "PREFLIGHT_INCOMPLETE"
    assert "急停" in failure.value.message
    assert wizard.next_step().kind == "preflight", "the session must not advance"


def test_preflight_refuses_when_the_hardware_is_not_ready(tmp_path: Path):
    hardware = SimulatedCalibrationHardware(problems=["没有找到 /dev/tangying-left，请检查 USB 线和供电"])
    wizard = a_wizard(tmp_path, hardware)
    with pytest.raises(CalibrationError) as failure:
        wizard.acknowledge("preflight", {check: True for check in build_steps()[0].requires})
    assert failure.value.code == "HARDWARE_NOT_READY"
    assert "/dev/tangying-left" in failure.value.message


def test_a_bad_reading_stops_the_step_instead_of_recording_a_guess(tmp_path: Path):
    hardware = SimulatedCalibrationHardware(fail_reads=("left_arm_gripper",))
    wizard = a_wizard(tmp_path, hardware)
    wizard.acknowledge("preflight", {check: True for check in build_steps()[0].requires})
    while (step := wizard.next_step()) is not None and step.motor != "left_arm_gripper":
        wizard.confirm(step.id)
    with pytest.raises(CalibrationError) as failure:
        wizard.confirm("zero:left_arm_gripper")
    assert failure.value.code == "INVALID_READING"
    assert "left_arm_gripper" not in wizard.session.motors
    assert "zero:left_arm_gripper" not in wizard.session.completed


def test_stopping_halfway_and_resuming_keeps_the_finished_work(tmp_path: Path):
    hardware = SimulatedCalibrationHardware()
    wizard = a_wizard(tmp_path, hardware)
    wizard.acknowledge("preflight", {check: True for check in build_steps()[0].requires})
    for _ in range(4):
        step = wizard.next_step()
        wizard.confirm(step.id)
    progress = list(wizard.session.completed)
    recorded = dict(wizard.session.motors)
    wizard.close()

    resumed = a_wizard(tmp_path, SimulatedCalibrationHardware())
    assert resumed.session.completed == progress, "the session file must carry progress"
    assert resumed.session.motors == recorded
    assert resumed.status()["completed"] == len(progress)


def test_a_session_cannot_be_resumed_against_a_different_robot(tmp_path: Path):
    wizard = a_wizard(tmp_path)
    wizard.acknowledge("preflight", {check: True for check in build_steps()[0].requires})
    with pytest.raises(CalibrationError) as failure:
        CalibrationWizard(SimulatedCalibrationHardware(), robot_id="xlerobot-02",
                          adapter_id="xlerobot", session_path=tmp_path / "session.json",
                          base_document={"cameras": CAMERAS, "geometry": GEOMETRY, "safety": SAFETY})
    assert failure.value.code == "ROBOT_MISMATCH"


def test_an_arm_that_never_moves_is_reported_in_plain_language(tmp_path: Path):
    class StuckArm(SimulatedCalibrationHardware):
        def read_motor_position(self, motor: str) -> int:
            return 2048  # a joint nobody touched

    wizard = a_wizard(tmp_path, StuckArm())
    wizard.acknowledge("preflight", {check: True for check in build_steps()[0].requires})
    while (step := wizard.next_step()) is not None and step.kind != "travel":
        wizard.confirm(step.id)
    with pytest.raises(CalibrationError) as failure:
        wizard.confirm("travel:left")
    assert failure.value.code == "NO_TRAVEL"
    assert "左臂" in failure.value.message


def test_an_incomplete_session_refuses_to_produce_a_calibration(tmp_path: Path):
    wizard = a_wizard(tmp_path)
    wizard.acknowledge("preflight", {check: True for check in build_steps()[0].requires})
    with pytest.raises(CalibrationError) as failure:
        wizard.document()
    assert failure.value.code == "SESSION_INCOMPLETE"
    assert "步未完成" in failure.value.message


def test_a_completed_session_produces_a_valid_calibration_with_both_arms(tmp_path: Path):
    wizard = a_wizard(tmp_path, SimulatedCalibrationHardware())
    run_to_completion(wizard)
    document = wizard.document()
    assert describe_calibration(document)["valid"] is True
    assert set(document["motors"]) == set(MOTOR_IDS)
    # Every homing offset came from a real reading, and every sweep set a range.
    assert all(entry["range_min"] < entry["range_max"] for entry in document["motors"].values())
    assert document["source"] == "measured"
    assert calibration_revision(document)

    assert "标定已完成" in wizard.summary_text()
    status = wizard.status()
    assert status["finished"] is True
    assert status["next"] is None

    # The session file is machine-readable so a UI can render it, and it holds no
    # value the wizard did not observe.
    saved = json.loads((tmp_path / "session.json").read_text())
    assert saved["schemaVersion"] == "robot.calibration.session.v1"
    assert saved["robot_id"] == "xlerobot-01"
