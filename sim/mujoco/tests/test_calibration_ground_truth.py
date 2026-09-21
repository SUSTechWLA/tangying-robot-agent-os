"""Does the calibration describe the camera the renderer actually used?

Every other RGB-D test in this suite checks that the pipeline is *self-consistent*:
that a coloured cloud reprojects to the same pixels under whatever transform the
frame carries. That is a real property, and it holds perfectly well while the
transform itself points the camera the wrong way. It is also why a wrong extrinsic
survived: nothing compared the document against MuJoCo, which is the only thing
that knows where the camera really was.

These tests make that comparison. They are the ground truth for "the calibration is
correct", as opposed to "the calibration is internally coherent".
"""

from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np
import pytest
from tangying_robot_gateway.calibration import CalibrationError
from tangying_sim.calibration import (
    _OPTICAL_FROM_MUJOCO,
    MODEL_CAMERA_FOR,
    SimulationCalibration,
)

#: Head poses to sample. The head is the hard case: its camera is declared
#: ``mode="targetbody"``, so MuJoCo recomputes its orientation every step.
HEAD_POSES = ((0.0, 0.0), (0.3, 0.0), (0.76, 0.0), (-0.76, 0.0), (0.0, 0.5))


@pytest.fixture(scope="module")
def model():
    from tangying_sim.rgbd_navigation import load_navigation_model

    return load_navigation_model(scene="home_task")


def true_world_from_camera(model, name: str, tilt: float, pan: float) -> np.ndarray:
    """The pose MuJoCo rendered from, in the runtime's optical convention."""
    data = mujoco.MjData(model)
    for joint, value in (("head_tilt_joint", tilt), ("head_pan_joint", pan)):
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint)
        if joint_id >= 0:
            data.qpos[model.jnt_qposadr[joint_id]] = value
    mujoco.mj_forward(model, data)
    camera_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_CAMERA, MODEL_CAMERA_FOR.get(name, name))
    assert camera_id >= 0, f"the model has no camera for {name}"
    pose = np.eye(4)
    pose[:3, :3] = np.array(data.cam_xmat[camera_id]).reshape(3, 3) @ _OPTICAL_FROM_MUJOCO
    pose[:3, 3] = data.cam_xpos[camera_id]
    return pose


def rotation_error_degrees(left: np.ndarray, right: np.ndarray) -> float:
    product = left[:3, :3].T @ right[:3, :3]
    cosine = max(-1.0, min(1.0, (float(np.trace(product)) - 1.0) / 2.0))
    return math.degrees(math.acos(cosine))


def test_the_head_pose_fixture_actually_moves_the_camera(model):
    """The whole test would be vacuous if the joints were not being set.

    Worth its own test: an earlier version of this investigation used the joint name
    ``head_tilt`` instead of ``head_tilt_joint``, the lookup returned -1, the guard
    skipped the assignment, and every "pose" was silently the default one.
    """
    first = true_world_from_camera(model, "head-rgbd", 0.0, 0.0)
    moved = true_world_from_camera(model, "head-rgbd", 0.76, 0.0)
    assert np.abs(first[:3, 3] - moved[:3, 3]).max() > 0.01, "the head did not move"


@pytest.mark.parametrize("camera", ["base-rgbd", "head-rgbd"])
def test_the_calibration_names_the_pose_the_renderer_used(model, camera, tmp_path):
    """The reported camera pose must be the one the depth image was taken from.

    A document is allowed to describe a fixed mount. It is not allowed to describe a
    fixed mount for a camera MuJoCo aims itself, and then have the runtime publish
    that number as the capture's pose.
    """
    calibration = SimulationCalibration(
        model=model, root=None, robot_id="xlerobot-01")
    data = mujoco.MjData(model)
    for tilt, pan in HEAD_POSES:
        for joint, value in (("head_tilt_joint", tilt), ("head_pan_joint", pan)):
            joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint)
            if joint_id >= 0:
                data.qpos[model.jnt_qposadr[joint_id]] = value
        mujoco.mj_forward(model, data)
        reported = calibration.world_from_camera(camera, data)
        truth = true_world_from_camera(model, camera, tilt, pan)
        assert np.abs(reported[:3, 3] - truth[:3, 3]).max() < 1e-9, (
            f"{camera} position is wrong at tilt={tilt} pan={pan}")
        error = rotation_error_degrees(reported, truth)
        assert error < 0.01, (
            f"{camera} orientation is {error:.3f}° off at tilt={tilt} pan={pan}; "
            "the transform attached to every RGB-D capture would be wrong")


def test_a_stored_document_cannot_make_the_head_camera_point_the_wrong_way(model):
    """The form the defect actually took in this repository.

    The stored per-unit documents carried a head rotation equal to the *base*
    camera's yaw — plausibly written by hand, and wrong by more than a right angle at
    every pose. Loading one must not be able to move the camera, because the camera
    is where MuJoCo put it.
    """
    directory = Path("artifacts/calibration/furnished-home")
    if not (directory / "calibration.json").is_file():
        pytest.skip("no stored per-unit calibration on this machine")
    stored = SimulationCalibration(
        model=model, root=directory, robot_id="xlerobot-mujoco-tabletop")
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    reported = stored.world_from_camera("head-rgbd", data)
    truth = true_world_from_camera(model, "head-rgbd", 0.0, 0.0)
    assert np.abs(reported[:3, 3] - truth[:3, 3]).max() < 1e-9
    assert rotation_error_degrees(reported, truth) < 0.01


def test_the_intrinsics_match_the_model_lens(model):
    """The half of the calibration that is unambiguously right, pinned so it stays."""
    calibration = SimulationCalibration(
        model=model, root=None, robot_id="xlerobot-01")
    document = calibration.document
    for name in ("base-rgbd", "head-rgbd"):
        camera_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_CAMERA, MODEL_CAMERA_FOR.get(name, name))
        width, height = document["cameras"][name]["width"], document["cameras"][name]["height"]
        expected = 0.5 * height / math.tan(math.radians(float(model.cam_fovy[camera_id])) / 2.0)
        assert document["cameras"][name]["intrinsics"]["fx"] == pytest.approx(expected, abs=1e-9)
        assert document["cameras"][name]["intrinsics"]["fy"] == pytest.approx(expected, abs=1e-9)
        assert document["cameras"][name]["intrinsics"]["cx"] == pytest.approx((width - 1) / 2)
        assert document["cameras"][name]["intrinsics"]["cy"] == pytest.approx((height - 1) / 2)


def test_a_calibration_for_another_robot_is_refused_not_adopted(model):
    """A stored document wins over the model, so adopting the wrong one means
    projecting every capture through another robot's camera. `replace` already
    guarded against this; loading was the way around the guard."""
    directory = Path("artifacts/calibration/furnished-home")
    if not (directory / "calibration.json").is_file():
        pytest.skip("no stored per-unit calibration on this machine")
    with pytest.raises(CalibrationError) as failure:
        SimulationCalibration(model=model, root=directory, robot_id="some-other-robot")
    assert failure.value.code == "ROBOT_MISMATCH"

    # The document's own robot still loads, so the check rejects strangers rather
    # than stored calibrations in general.
    stored = SimulationCalibration(model=model, root=directory, robot_id="xlerobot-mujoco-tabletop")
    assert stored.document["robotId"] == "xlerobot-mujoco-tabletop"
