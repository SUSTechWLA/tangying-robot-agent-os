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
from tangying_robot_gateway.calibration_solver import (
    SIMULATED_CAMERA,
    CalibrationSolveError,
    IntrinsicsSolver,
    solve_intrinsics,
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
    kwargs.setdefault("solver", IntrinsicsSolver())
    return CalibrationWizard(
        hardware or SimulatedCalibrationHardware(),
        robot_id="xlerobot-01", adapter_id="xlerobot",
        session_path=tmp_path / "session.json", **kwargs,
    )


class DeclaredSolver:
    """A solver double for the plumbing tests, and it says so.

    These tests are about whether the wizard hands the solver what only the session
    knows — the lens it just measured, the motor document in force, the link named in
    the document — and files what comes back. So the double records what it was given
    and echoes the link, rather than solving anything.
    """

    def solve_intrinsics(self, camera, views):
        return solve_intrinsics(views)

    def solve_handeye(self, views, *, intrinsics=None, motors=None, parent_link=None):
        self.last_request = {"views": len(views), "intrinsics": intrinsics,
                             "motors": motors, "parent_link": parent_link}
        return {"parentLink": parent_link or "left_arm_link6", "xyz": [0.02, 0.0, 0.05],
                "rpy": [3.14159, 0.0, 0.0]}


def acknowledge_the_preparations(wizard: CalibrationWizard) -> None:
    """The two steps a person confirms by hand, in the order the plan puts them."""
    for step in planned_steps():
        if step.kind in ("connect", "preflight"):
            wizard.acknowledge(step.id, {check: True for check in step.requires})


def planned_steps(cameras=CAMERAS):
    """The plan as this robot gets it: camera steps come from its own declaration."""
    return build_steps(cameras)


def acknowledge_and_run(wizard: CalibrationWizard) -> None:
    """Walk the whole plan the way the page does: acknowledge, then confirm."""
    while (step := wizard.next_step()) is not None:
        if step.kind in ("connect", "preflight"):
            wizard.acknowledge(step.id, {check: True for check in step.requires})
        else:
            wizard.confirm(step.id)


def test_the_plan_covers_both_arms_both_grippers_and_the_head_and_base():
    steps = planned_steps()
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
    # Every step is phrased as something a person does. The set is listed rather
    # than inferred so adding a kind forces a decision about its wording: the
    # connecting, camera and hand-eye steps are written for a person too, they
    # simply do not involve turning a joint by hand.
    hands_on = {"preflight", "travel", "review", "connect", "intrinsics", "handeye"}
    for step in planned_steps():
        assert step.title and step.instruction and step.detail
        assert "用手" in step.instruction or step.kind in hands_on
        # No joint identifier or register name should leak into the instruction.
        assert "homing_offset" not in step.instruction
        assert "_arm_" not in step.instruction
        assert "range_min" not in step.instruction


def test_a_session_is_guided_step_by_step_and_reports_progress(tmp_path: Path):
    wizard = a_wizard(tmp_path)
    status = wizard.status()
    assert status["total"] == len(planned_steps())
    assert status["completed"] == 0
    # Connecting the hardware comes first: nothing can be measured before the
    # bus and the cameras are reachable, and the preflight check is about a robot
    # this page can already see.
    assert status["next"]["kind"] == "connect"
    assert "尚未开始" in status["summary"]

    acknowledge_the_preparations(wizard)
    first_zero = wizard.next_step()
    assert first_zero is not None and first_zero.kind == "zero"
    wizard.confirm(first_zero.id)

    recorded = wizard.session.motors[first_zero.motor]
    assert recorded["id"] == MOTOR_IDS[first_zero.motor]
    assert -2048 <= recorded["homing_offset"] <= 2047
    status = wizard.status()
    assert status["completed"] == 3, "the two preparations and the first joint"
    assert "下一步" in status["summary"]


def test_the_recorded_zero_offset_is_the_servo_reading_minus_its_centre(tmp_path: Path):
    class FixedReading(SimulatedCalibrationHardware):
        def read_motor_position(self, motor: str) -> int:
            return 2600

    wizard = a_wizard(tmp_path, FixedReading())
    acknowledge_the_preparations(wizard)
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
    for step in planned_steps():
        if step.kind == "connect":
            wizard.acknowledge(step.id, {check: True for check in step.requires})
    preflight = next(step for step in planned_steps() if step.kind == "preflight")
    checks = {check: True for check in preflight.requires}
    checks["estop_reachable"] = False
    with pytest.raises(CalibrationError) as failure:
        wizard.acknowledge("preflight", checks)
    assert failure.value.code == "PREFLIGHT_INCOMPLETE"
    assert "急停" in failure.value.message
    assert wizard.next_step().kind == "preflight", "the session must not advance"


def test_connecting_is_verified_against_the_bus_not_just_confirmed(tmp_path: Path):
    """The operator says the cable is in; the wizard still reads a servo."""
    class DeadBus(SimulatedCalibrationHardware):
        def read_motor_position(self, motor: str) -> int:
            return -1

    wizard = a_wizard(tmp_path, DeadBus())
    connect = next(step for step in planned_steps() if step.kind == "connect")
    with pytest.raises(CalibrationError) as failure:
        wizard.acknowledge(connect.id, {check: True for check in connect.requires})
    assert failure.value.code == "HARDWARE_NOT_READY"
    assert wizard.next_step().kind == "connect", "the session must not advance"

    # And the same step records what the bus answered when it does.
    working = a_wizard(tmp_path / "good")
    working.acknowledge(connect.id, {check: True for check in connect.requires})
    assert working.session.connection["buses"], "a real reading is kept as evidence"
    assert working.session.connection["backend"] == "simulated"
    # The backend declares no camera list, so the wizard says so instead of
    # writing "ok" for a check it never performed.
    assert working.session.connection["cameras"] == "unverified"


def test_preflight_refuses_when_the_hardware_is_not_ready(tmp_path: Path):
    hardware = SimulatedCalibrationHardware(problems=["没有找到 /dev/tangying-left，请检查 USB 线和供电"])
    wizard = a_wizard(tmp_path, hardware)
    with pytest.raises(CalibrationError) as failure:
        acknowledge_the_preparations(wizard)
    assert failure.value.code == "HARDWARE_NOT_READY"
    assert "/dev/tangying-left" in failure.value.message


def test_a_bad_reading_stops_the_step_instead_of_recording_a_guess(tmp_path: Path):
    hardware = SimulatedCalibrationHardware(fail_reads=("left_arm_gripper",))
    wizard = a_wizard(tmp_path, hardware)
    acknowledge_the_preparations(wizard)
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
    acknowledge_the_preparations(wizard)
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
    acknowledge_the_preparations(wizard)
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
    acknowledge_the_preparations(wizard)
    while (step := wizard.next_step()) is not None and step.kind != "travel":
        wizard.confirm(step.id)
    with pytest.raises(CalibrationError) as failure:
        wizard.confirm("travel:left")
    assert failure.value.code == "NO_TRAVEL"
    assert "左臂" in failure.value.message


def test_an_incomplete_session_refuses_to_produce_a_calibration(tmp_path: Path):
    wizard = a_wizard(tmp_path)
    acknowledge_the_preparations(wizard)
    with pytest.raises(CalibrationError) as failure:
        wizard.document()
    assert failure.value.code == "SESSION_INCOMPLETE"
    assert "步未完成" in failure.value.message


def test_a_completed_session_produces_a_valid_calibration_with_both_arms(tmp_path: Path):
    wizard = a_wizard(tmp_path, SimulatedCalibrationHardware())
    acknowledge_and_run(wizard)
    document = wizard.document()
    assert describe_calibration(document)["valid"] is True
    assert set(document["motors"]) == set(MOTOR_IDS)
    # Every homing offset came from a real reading, and every sweep set a range.
    assert all(entry["range_min"] < entry["range_max"] for entry in document["motors"].values())
    assert document["source"] == "simulation"
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


def test_wizard_motor_document_is_accepted_by_the_actual_adapter(tmp_path):
    from xlerobot_adapter.calibration import validate_calibration_data

    wizard = a_wizard(tmp_path)
    acknowledge_and_run(wizard)
    validate_calibration_data(wizard.document()["motors"])


# --- the camera half ------------------------------------------------------

ARM_CAMERAS = {
    "wrist-rgbd": {
        "sourceId": "xlerobot-01/wrist-rgbd", "width": 640, "height": 480,
        "intrinsics": {"fx": 600.0, "fy": 600.0, "cx": 320.0, "cy": 240.0},
        "distortion": {"model": "none", "coefficients": []},
        "extrinsics": {"parentLink": "left_arm_link6", "xyz": [0.02, 0.0, 0.05],
                       "rpy": [3.14159, 0.0, 0.0]},
    },
}


def test_a_camera_on_an_arm_gets_a_hand_eye_step_and_one_on_the_head_does_not():
    """Hand-eye asks where a camera sits relative to the arm carrying it.

    A head-mounted camera has no such question, and asking an operator to move an
    arm while photographing a board would collect poses that no equation can use.
    """
    assert not [step for step in planned_steps() if step.kind == "handeye"]
    with_arm = build_steps(ARM_CAMERAS)
    handeye = [step for step in with_arm if step.kind == "handeye"]
    assert len(handeye) == 1
    assert handeye[0].guide["cameras"] == ["wrist-rgbd"]


def test_the_solve_recovers_the_simulated_camera_from_its_own_observations(tmp_path: Path):
    """The end-to-end proof that the camera half measures rather than copies.

    The document handed in declares 600 px, the simulator's lens is 612.5, and the
    number that comes out has to be the one the solver derived — not the one it was
    given, and not the simulator's, which it never sees.
    """
    wizard = a_wizard(tmp_path)
    acknowledge_and_run(wizard)
    solved = wizard.document()["cameras"]["head-rgbd"]["intrinsics"]
    for key, expected in SIMULATED_CAMERA.items():
        assert abs(solved[key] - expected) < 0.5, f"{key} was not measured"
    assert abs(solved["fx"] - 600.0) > 1.0, "the declared value must not simply survive"


def test_the_simulated_backend_reports_a_full_board_on_every_call(tmp_path: Path):
    hardware = SimulatedCalibrationHardware()
    view = hardware.capture_calibration_view("head-rgbd")
    assert view is not None
    assert len(view["objectPoints"]) == len(view["imagePoints"])
    assert len(view["objectPoints"]) == 2 * 7 * 5
    assert hardware.capture_calibration_view("head-rgbd") != view, "each call is a new pose"


def test_an_unsolved_session_keeps_every_view_so_it_can_be_solved_later(tmp_path: Path):
    """Losing twelve poses because a solver is missing is the one cost that is not
    recoverable by walking over to the robot again."""
    wizard = a_wizard(tmp_path, solver=None)
    for step in planned_steps():
        if step.kind in ("connect", "preflight"):
            wizard.acknowledge(step.id, {check: True for check in step.requires})
        elif step.kind == "intrinsics":
            for _ in range(11):
                wizard.confirm(step.id)
            with pytest.raises(CalibrationError) as failure:
                wizard.confirm(step.id)
            assert failure.value.code == "SOLVER_UNAVAILABLE"
            assert "12" in failure.value.message, "the message says how much was kept"
            assert len(wizard.session.captures["intrinsics:head-rgbd"]) == 12
            assert "intrinsics:head-rgbd" not in wizard.session.completed
            break
        else:
            wizard.confirm(step.id)

    # The same session file, resumed with a solver, finishes without new captures.
    resumed = a_wizard(tmp_path)
    assert len(resumed.session.captures["intrinsics:head-rgbd"]) == 12
    assert resumed.session.completed == wizard.session.completed
    while (step := resumed.next_step()) is not None:
        resumed.confirm(step.id)
    assert resumed.document()["cameras"]["head-rgbd"]["intrinsics"]


def test_the_shipped_solver_refuses_hand_eye_it_cannot_answer(tmp_path: Path):
    """The shipped solver solves hand-eye now. What it must still never do is invent
    the pieces it was not given: a view with no arm pose has no ``A``, and a solver
    that assumed one would return a confident mount that nothing downstream can
    check."""
    from tangying_robot_gateway.calibration_solver import IntrinsicsSolver

    views = [{"camera": "wrist-rgbd",
              "observed": {"objectPoints": [0.0, 0.0, 0.02, 0.0, 0.0, 0.02, 0.02, 0.02],
                           "imagePoints": [100.0, 100.0, 200.0, 100.0,
                                           100.0, 200.0, 200.0, 200.0]}}
             for _ in range(3)]
    with pytest.raises(CalibrationSolveError) as failure:
        IntrinsicsSolver().solve_handeye(
            views, intrinsics=SIMULATED_CAMERA, parent_link="left_arm_link6")
    assert failure.value.code == "VIEW_INCOMPLETE"
    assert "舵机读数" in failure.value.message


def test_a_view_without_a_recognised_board_does_not_count_as_progress(tmp_path: Path):
    class BlindCamera(SimulatedCalibrationHardware):
        def capture_calibration_view(self, camera: str):
            return None

    wizard = a_wizard(tmp_path, BlindCamera())
    for step in planned_steps():
        if step.kind in ("connect", "preflight"):
            wizard.acknowledge(step.id, {check: True for check in step.requires})
        elif step.kind == "intrinsics":
            with pytest.raises(CalibrationError) as failure:
                wizard.confirm(step.id)
            assert failure.value.code == "BOARD_NOT_FOUND"
            assert "intrinsics:head-rgbd" not in wizard.session.captures
            break
        else:
            wizard.confirm(step.id)


def test_a_backend_without_cameras_says_so_at_the_camera_step(tmp_path: Path):
    class MotorsOnly(SimulatedCalibrationHardware):
        capture_calibration_view = None

    wizard = a_wizard(tmp_path, MotorsOnly())
    for step in planned_steps():
        if step.kind in ("connect", "preflight"):
            wizard.acknowledge(step.id, {check: True for check in step.requires})
        elif step.kind == "intrinsics":
            with pytest.raises(CalibrationError) as failure:
                wizard.confirm(step.id)
            assert failure.value.code == "CAPTURE_UNAVAILABLE"
            assert "capture_calibration_view" in failure.value.message
            break
        else:
            wizard.confirm(step.id)


def test_the_wizard_does_not_edit_the_document_it_was_given(tmp_path: Path):
    """The caller's document is live configuration. A session that writes measured
    values into it would apply them before anyone pressed save."""
    declared = {"cameras": json.loads(json.dumps(CAMERAS)), "geometry": GEOMETRY,
                "safety": SAFETY}
    before = json.loads(json.dumps(declared))
    wizard = CalibrationWizard(SimulatedCalibrationHardware(), robot_id="xlerobot-01",
                               adapter_id="xlerobot", session_path=tmp_path / "session.json",
                               base_document=declared, solver=IntrinsicsSolver())
    acknowledge_and_run(wizard)
    assert declared == before, "the wizard must copy, not adopt, the input document"
    assert wizard.document()["cameras"]["head-rgbd"]["intrinsics"]


def test_the_status_says_which_numbers_were_measured_and_which_were_inherited(tmp_path: Path):
    """A document whose mounting still comes from the factory sheet must not look
    like one that was measured end to end."""
    wizard = a_wizard(tmp_path)
    assert wizard.provenance()["intrinsicsMeasured"] == []
    assert wizard.provenance()["extrinsicsCarriedOver"] == ["head-rgbd"]

    acknowledge_and_run(wizard)
    provenance = wizard.status()["provenance"]
    assert provenance["intrinsicsMeasured"] == ["head-rgbd"]
    assert provenance["intrinsicsCarriedOver"] == []
    # Nothing in this session measured where the camera is bolted on, and the status
    # says so rather than leaving the reader to assume.
    assert provenance["extrinsicsMeasured"] == []
    assert provenance["extrinsicsCarriedOver"] == ["head-rgbd"]
    assert provenance["motorsMeasured"] == sorted(MOTOR_IDS)


def test_the_plan_is_readable_before_the_operator_does_anything(tmp_path: Path):
    """The page learns the plan from the status file. If that file only appeared after
    the first completed step, a freshly opened wizard would show "no session" and the
    operator would have nothing to be guided by."""
    wizard = a_wizard(tmp_path)
    status_file = tmp_path / "session.status.json"
    assert status_file.is_file(), "opening a session is enough to publish the plan"
    status = json.loads(status_file.read_text())
    assert status["total"] == len(planned_steps())
    assert status["completed"] == 0
    assert status["next"]["kind"] == "connect"
    assert all("guide" in step for step in status["steps"])
    assert wizard.session.completed == []


def test_every_step_carries_a_guide_naming_how_to_show_it(tmp_path: Path):
    """The front end must never have to invent a step or guess at its picture."""
    wizard = a_wizard(tmp_path)
    kinds = {step.guide["kind"] for step in wizard.steps}
    assert kinds == {"connect", "preflight", "zero", "travel", "intrinsics", "review"}
    for step in wizard.steps:
        assert step.guide["kind"], f"{step.id} has no guide"
    connect = next(step for step in wizard.steps if step.kind == "connect")
    # The page reports back which check it confirmed, so the ids must be the checks
    # and not the plug kinds — both cameras plug into a "camera" port.
    assert [port["id"] for port in connect.guide["ports"]] == list(connect.requires)
    assert [port["label"] for port in connect.guide["ports"]]
    preflight = next(step for step in wizard.steps if step.kind == "preflight")
    assert [check["label"] for check in preflight.guide["checks"]], "labels, not just ids"
    intrinsics = next(step for step in wizard.steps if step.kind == "intrinsics")
    assert intrinsics.guide == {"kind": "intrinsics", "camera": "head-rgbd",
                                "views": 12}
