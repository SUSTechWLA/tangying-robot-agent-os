"""Can a stack start on a calibration that contradicts the model it renders with?

A stored calibration document is adopted in preference to the model, and every RGB-D
capture's pose is then computed from it. That is by design — a real unit's measured
numbers have to beat a nominal model — and it is also why a document whose numbers
describe a *different* camera is the most expensive kind of wrong: it looks exactly
like a measured one, and the pose it publishes is wrong for every frame.

Earlier today one such document was found in this repository: the head camera's stored
rotation was 120.5° away from what MuJoCo renders. The comparison is now pinned by
``test_calibration_ground_truth.py``. These tests are about the other half — that a
stack refuses to *start* on it, in the ordinary construction path, with an error that
names the camera and the discrepancy.
"""

from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np
import pytest
from tangying_robot_gateway.calibration import CalibrationError, save_calibration
from tangying_sim.calibration import MODEL_CAMERA_FOR, SimulationCalibration

ROBOT_ID = "xlerobot-01"
STORED = Path("artifacts/calibration/furnished-home")


@pytest.fixture(scope="module")
def model():
    from tangying_sim.rgbd_navigation import load_navigation_model

    return load_navigation_model(scene="home_task")


def a_document(model, *, camera="base-rgbd", xyz=None, rpy=None, fx_scale=1.0,
               width=320, height=240) -> dict:
    """The model's own document, with one field moved if asked."""
    derived = SimulationCalibration(model=model, root=None, robot_id=ROBOT_ID,
                                    framebuffer=(width, height)).document
    entry = derived["cameras"][camera]
    if xyz is not None:
        entry["extrinsics"]["xyz"] = list(xyz)
    if rpy is not None:
        entry["extrinsics"]["rpy"] = list(rpy)
    if fx_scale != 1.0:
        entry["intrinsics"]["fx"] *= fx_scale
        entry["intrinsics"]["fy"] *= fx_scale
    return derived


def stored_at(tmp_path: Path, document: dict) -> Path:
    save_calibration(tmp_path / "calibration.json", document)
    return tmp_path


def test_a_document_that_describes_the_model_still_starts(model, tmp_path):
    """The check has to accept the honest case, or nobody can use a stored file."""
    root = stored_at(tmp_path, a_document(model))
    loaded = SimulationCalibration(model=model, root=root, robot_id=ROBOT_ID)
    assert loaded.document["cameras"]["base-rgbd"]["extrinsics"]["xyz"] == \
        pytest.approx([0.3, 0.0, 0.16000000000000003])


def test_the_head_camera_is_not_flagged_even_when_its_stored_rotation_is_wrong(model, tmp_path):
    """The subtlety that would otherwise make this check unusable.

    ``head_depth`` is declared ``mode="targetbody"``: MuJoCo recomputes its orientation
    every step to look at the body it was pointed at, so *no* fixed parent-relative
    rotation describes it, and ``world_from_camera`` already takes its pose from the
    model for that reason. A stored rotation for such a camera is a snapshot, not a
    contradiction — and the stored documents on this machine carry exactly that.
    """
    document = a_document(model)
    document["cameras"]["head-rgbd"]["extrinsics"]["rpy"] = \
        [-3.1201367191924403, -0.0, -1.5707963267948966]  # the real defect's numbers
    root = stored_at(tmp_path, document)
    loaded = SimulationCalibration(model=model, root=root, robot_id=ROBOT_ID)

    # And the pose it reports is the model's, not the stored snapshot's.
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "head_depth")
    reported = loaded.world_from_camera("head-rgbd", data)
    assert np.abs(reported[:3, 3] - np.array(data.cam_xpos[camera_id])).max() < 1e-9


def test_a_doctored_mount_refuses_to_start(model, tmp_path):
    """The shape of the defect that was actually shipped: a hand-written rotation."""
    document = a_document(model)
    truth = document["cameras"]["base-rgbd"]["extrinsics"]["rpy"]
    document["cameras"]["base-rgbd"]["extrinsics"]["rpy"] = [truth[0], truth[1], truth[2] + 2.1]
    root = stored_at(tmp_path, document)
    with pytest.raises(CalibrationError) as failure:
        SimulationCalibration(model=model, root=root, robot_id=ROBOT_ID)
    assert failure.value.code == "DOCUMENT_CONTRADICTS_MODEL"
    assert "base-rgbd" in failure.value.message
    assert "120.3" in failure.value.message or "120." in failure.value.message
    assert str(root / "calibration.json") in failure.value.message


def test_a_mount_moved_by_a_handful_of_millimetres_refuses_too(model, tmp_path):
    """Not a fudge factor: five millimetres is the point at which the document is
    describing a camera that is not the one the renderer used."""
    document = a_document(model)
    xyz = document["cameras"]["base-rgbd"]["extrinsics"]["xyz"]
    document["cameras"]["base-rgbd"]["extrinsics"]["xyz"] = [xyz[0] + 0.02, xyz[1], xyz[2]]
    with pytest.raises(CalibrationError) as failure:
        SimulationCalibration(model=model, root=stored_at(tmp_path, document), robot_id=ROBOT_ID)
    assert failure.value.code == "DOCUMENT_CONTRADICTS_MODEL"
    assert "mm" in failure.value.message


def test_a_mount_on_the_wrong_link_refuses(model, tmp_path):
    document = a_document(model)
    document["cameras"]["base-rgbd"]["extrinsics"]["parentLink"] = "head_tilt_link"
    with pytest.raises(CalibrationError) as failure:
        SimulationCalibration(model=model, root=stored_at(tmp_path, document), robot_id=ROBOT_ID)
    assert failure.value.code == "DOCUMENT_CONTRADICTS_MODEL"
    assert "chassis" in failure.value.message


def test_a_lens_that_is_not_the_models_refuses(model, tmp_path):
    """A focal length half again as long is a different camera, and the runtime would
    deproject every depth pixel with it."""
    document = a_document(model, fx_scale=1.5)
    with pytest.raises(CalibrationError) as failure:
        SimulationCalibration(model=model, root=stored_at(tmp_path, document), robot_id=ROBOT_ID)
    assert failure.value.code == "DOCUMENT_CONTRADICTS_MODEL"
    assert "fx" in failure.value.message


def test_the_same_lens_written_at_another_resolution_is_accepted(model, tmp_path):
    """Rescaling is the document's own contract (``intrinsics()``), so a document
    measured at 640x480 is not contradicting a 320x240 framebuffer."""
    derived = SimulationCalibration(model=model, root=None, robot_id=ROBOT_ID,
                                    framebuffer=(320, 240)).document
    entry = derived["cameras"]["base-rgbd"]
    camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "base_depth")
    entry["width"], entry["height"] = 640, 480
    focal = 0.5 * 480 / math.tan(math.radians(float(model.cam_fovy[camera_id])) / 2.0)
    entry["intrinsics"] = {"fx": focal, "fy": focal, "cx": 319.5, "cy": 239.5}
    loaded = SimulationCalibration(model=model, root=stored_at(tmp_path, derived),
                                   robot_id=ROBOT_ID, framebuffer=(320, 240))
    assert loaded.document["cameras"]["base-rgbd"]["width"] == 640


def test_a_camera_the_model_does_not_have_is_left_alone(model, tmp_path):
    """A document may describe a camera the model has none of — a real unit's extra
    eye, or a scene whose chassis camera was never commissioned. There is no model
    geometry to contradict it, so the check skips it rather than inventing one.

    The scene file without the commissioned chassis camera is exactly the model the
    loader starts from before it adds ``base_depth``.
    """
    from tangying_sim.home_scene import HOME_MODEL_PATH

    spec = mujoco.MjSpec.from_file(str(HOME_MODEL_PATH))
    without_base_camera = spec.compile()
    assert mujoco.mj_name2id(without_base_camera, mujoco.mjtObj.mjOBJ_CAMERA,
                             "base_depth") < 0

    root = stored_at(tmp_path, a_document(model))
    loaded = SimulationCalibration(model=without_base_camera, root=root, robot_id=ROBOT_ID)
    assert loaded.document["cameras"]["base-rgbd"]["extrinsics"]["parentLink"] == "chassis"


def test_a_document_that_omits_a_camera_the_model_has_is_not_an_error(model, tmp_path):
    """The check is about numbers that contradict the model, not about coverage: a
    document that declares fewer cameras says nothing wrong about the others."""
    document = a_document(model)
    del document["cameras"]["head-rgbd"]
    loaded = SimulationCalibration(model=model, root=stored_at(tmp_path, document),
                                   robot_id=ROBOT_ID)
    assert "head-rgbd" not in loaded.document["cameras"]


def test_a_document_for_another_robot_is_still_refused_first(model, tmp_path):
    """The order of the checks is part of the behaviour: a stranger's document is a
    mismatch, not a geometry disagreement, and the error has to say so."""
    with pytest.raises(CalibrationError) as failure:
        SimulationCalibration(model=model, root=stored_at(tmp_path, a_document(model)),
                              robot_id="some-other-robot")
    assert failure.value.code == "ROBOT_MISMATCH"


def test_the_documents_this_machine_already_has_still_load(model):
    """The check would be worthless if it rejected the repository's own calibration."""
    if not (STORED / "calibration.json").is_file():
        pytest.skip("no stored per-unit calibration on this machine")
    loaded = SimulationCalibration(model=model, root=STORED,
                                   robot_id="xlerobot-mujoco-tabletop")
    assert loaded.document["cameras"]["base-rgbd"]["extrinsics"]["parentLink"] == "chassis"


def test_the_tolerances_are_the_ones_the_defect_was_measured_against():
    """A guard on the guard: a tolerance wider than the defect it exists for would
    turn this whole file into a test that always passes."""
    from tangying_sim.calibration import MODEL_MOUNT_ROTATION_TOLERANCE_DEG

    assert MODEL_MOUNT_ROTATION_TOLERANCE_DEG < 120.543
    assert MODEL_CAMERA_FOR["head-rgbd"] == "head_depth"
