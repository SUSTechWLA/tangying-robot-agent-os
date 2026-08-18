from itertools import combinations

import mujoco
import numpy as np
import pytest
from tangying_sim import model as model_module
from tangying_sim.model import MODEL_REVISION, load_task_model, validate_task_model


def test_task_model_contains_pinned_xlerobot_and_task_scene():
    model = load_task_model()
    validate_task_model(model)

    assert MODEL_REVISION == "3d14695e40c9c68229c0aacffca6053c75cd3eb6"
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "chassis") >= 0
    for name in ("Rotation_L", "Rotation_R", "Jaw_L", "Jaw_R"):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) >= 0
    for name in ("red_cup", "blue_bottle", "right_bin", "front_tray"):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name) >= 0
    for name in (
        "Rotation_L",
        "Pitch_L",
        "Elbow_L",
        "Jaw_L",
        "Rotation_R",
        "Pitch_R",
        "Elbow_R",
        "Jaw_R",
    ):
        assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) >= 0
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "overview") >= 0


def test_task_model_remains_stable_for_one_simulated_second():
    model = load_task_model()
    data = mujoco.MjData(model)
    previous_time = 0.0
    max_speed = 0.0

    for _ in range(500):
        mujoco.mj_step(model, data)
        assert data.time > previous_time
        assert np.all(np.isfinite(data.qpos))
        assert np.all(np.isfinite(data.qvel))
        assert np.all(np.isfinite(data.qacc))
        max_speed = max(max_speed, float(np.max(np.abs(data.qvel))))
        previous_time = data.time

    assert data.time == pytest.approx(1.0)
    assert max_speed < 100.0


def test_task_layout_is_in_front_and_preserves_robot_relative_left_and_right():
    model = load_task_model()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    chassis = _body_position(model, data, "chassis")
    table = _body_position(model, data, "table")
    left_bin = _body_position(model, data, "left_bin")
    right_bin = _body_position(model, data, "right_bin")

    assert table[1] > chassis[1] + 0.4
    assert left_bin[0] < table[0] < right_bin[0]


@pytest.mark.parametrize(
    ("source", "destination"),
    [
        ("red_cup", "right_bin"),
        ("blue_bottle", "front_tray"),
    ],
)
def test_each_goal_has_one_arm_that_can_reach_source_and_destination(source, destination):
    model = load_task_model()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    source_position = _body_position(model, data, source)
    destination_position = _body_position(model, data, destination)
    common_shoulders = []
    measured_distances = {}

    for shoulder in ("Rotation_Pitch", "Rotation_Pitch_R"):
        shoulder_position = _body_position(model, data, shoulder)
        source_delta = source_position - shoulder_position
        destination_delta = destination_position - shoulder_position
        distances = (
            float(np.linalg.norm(source_delta[:2])),
            float(np.linalg.norm(source_delta)),
            float(np.linalg.norm(destination_delta[:2])),
            float(np.linalg.norm(destination_delta)),
        )
        measured_distances[shoulder] = distances
        if all(distance <= 0.42 for distance in distances):
            common_shoulders.append(shoulder)

    assert common_shoulders, measured_distances


def test_all_pickable_objects_are_within_one_arm_reach():
    model = load_task_model()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    shoulder_positions = (
        _body_position(model, data, "Rotation_Pitch"),
        _body_position(model, data, "Rotation_Pitch_R"),
    )

    for object_name in _task_object_names():
        object_position = _body_position(model, data, object_name)
        distances = [
            float(np.linalg.norm(object_position - shoulder_position))
            for shoulder_position in shoulder_positions
        ]

        assert min(distances) <= 0.42, (object_name, distances)


def test_task_entities_have_visible_separation():
    model = load_task_model()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    task_bodies = (
        "red_cup",
        "blue_cup",
        "green_cup",
        "red_bottle",
        "blue_bottle",
        "green_bottle",
        "red_block",
        "blue_block",
        "green_block",
        "left_bin",
        "right_bin",
        "front_tray",
    )

    for first, second in combinations(task_bodies, 2):
        first_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, first)
        second_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, second)
        first_geom = int(model.body_geomadr[first_body])
        second_geom = int(model.body_geomadr[second_body])
        distance = mujoco.mj_geomDistance(model, data, first_geom, second_geom, 10.0, None)

        assert distance >= 0.015, (first, second, distance)


def test_task_objects_start_clear_of_robot_collision_geometry():
    model = load_task_model()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    robot_geoms = _collision_geoms_in_subtree(model, "chassis")

    for object_name in _task_object_names():
        object_body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, object_name)
        object_geom = int(model.body_geomadr[object_body])
        clearances = [
            mujoco.mj_geomDistance(model, data, robot_geom, object_geom, 1.0, None)
            for robot_geom in robot_geoms
        ]

        assert min(clearances) >= 0.02, (object_name, min(clearances))


def test_task_objects_do_not_drift_while_settling():
    model = load_task_model()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    initial_positions = {
        name: _body_position(model, data, name).copy() for name in _task_object_names()
    }

    for _ in range(500):
        mujoco.mj_step(model, data)

    planar_drift = {
        name: float(np.linalg.norm(_body_position(model, data, name)[:2] - initial[:2]))
        for name, initial in initial_positions.items()
    }

    assert max(planar_drift.values()) <= 0.002, planar_drift


@pytest.mark.parametrize(
    ("kind", "missing_name"),
    [
        (mujoco.mjtObj.mjOBJ_ACTUATOR, "Rotation_L"),
        (mujoco.mjtObj.mjOBJ_CAMERA, "overview"),
    ],
)
def test_validation_rejects_missing_control_or_camera(monkeypatch, kind, missing_name):
    model = load_task_model()
    original_name2id = model_module.mujoco.mj_name2id

    def missing_required_name(candidate, query_kind, query_name):
        if query_kind == kind and query_name == missing_name:
            return -1
        return original_name2id(candidate, query_kind, query_name)

    monkeypatch.setattr(model_module.mujoco, "mj_name2id", missing_required_name)

    with pytest.raises(ValueError, match=missing_name):
        validate_task_model(model)


def _body_position(model, data, name):
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    return data.xpos[body_id]


def _task_object_names():
    return (
        "red_cup",
        "blue_cup",
        "green_cup",
        "red_bottle",
        "blue_bottle",
        "green_bottle",
        "red_block",
        "blue_block",
        "green_block",
    )


def _collision_geoms_in_subtree(model, root_name):
    root_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, root_name)
    geoms = []
    for geom_id in range(model.ngeom):
        body_id = int(model.geom_bodyid[geom_id])
        current_id = body_id
        while current_id > 0 and current_id != root_id:
            current_id = int(model.body_parentid[current_id])
        if current_id == root_id and (model.geom_contype[geom_id] or model.geom_conaffinity[geom_id]):
            geoms.append(geom_id)
    return geoms
