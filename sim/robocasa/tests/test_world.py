from __future__ import annotations

import mujoco
import pytest
from tangying_robocasa.composer import SceneConfig, compose_handoff_scene
from tangying_robocasa.world import (
    RoboCasaRobotView,
    RoboCasaSharedWorld,
    _grid_index_range,
)
from tangying_sim.rendering import SceneRenderer

pytestmark = pytest.mark.robocasa


@pytest.fixture(scope="module")
def shared_world() -> RoboCasaSharedWorld:
    pytest.importorskip("robocasa")
    return RoboCasaSharedWorld.from_scene(compose_handoff_scene(SceneConfig()), seed=7)


def _entity(view: RoboCasaRobotView, entity_id: str):
    return next(item for item in view.entities() if item.entity_id == entity_id)


def test_two_views_share_one_model_data_and_object_identity(shared_world) -> None:
    sender = RoboCasaRobotView(shared_world, "robot-1")
    receiver = RoboCasaRobotView(shared_world, "robot-2")

    assert sender.model is receiver.model
    assert sender.data is receiver.data
    assert sender.lock is receiver.lock
    assert _entity(sender, "red-block").position == _entity(receiver, "red-block").position


def test_joint_positions_include_articulated_arms_and_head(shared_world) -> None:
    positions = RoboCasaRobotView(shared_world, "robot-1").joint_positions()

    assert len(
        [
            name
            for name in positions
            if "Rotation_" in name
            or "Pitch_" in name
            or "Elbow_" in name
            or "Wrist_" in name
            or "Jaw_" in name
        ]
    ) == 12
    assert any(name.endswith("head_pan_joint") for name in positions)
    assert any(name.endswith("head_tilt_joint") for name in positions)


def test_robot_entities_publish_scene_model_identity(shared_world) -> None:
    robots = {
        entity.entity_id: entity
        for entity in shared_world.entities()
        if entity.category == "robot"
    }

    assert set(robots) == {"robot-1", "robot-2"}
    assert all(
        entity.attributes["scene_id"] == "robocasa-handoff-v1"
        for entity in robots.values()
    )
    assert all(
        entity.attributes["model_hash"] == shared_world.scene.model_hash
        for entity in robots.values()
    )


def test_world_publishes_mujoco_kitchen_fixtures_with_render_bounds(shared_world) -> None:
    fixtures = [
        entity
        for entity in shared_world.entities()
        if entity.attributes.get("model_source") == "mujoco"
    ]

    categories = {entity.category for entity in fixtures}
    assert {
        "cabinet",
        "counter",
        "dishwasher",
        "floor",
        "fridge",
        "microwave",
        "sink",
        "stove",
        "wall",
    } <= categories
    assert {fixture.entity_id for fixture in fixtures} == {
        entity_id for _body, entity_id, _category, _label in shared_world.FIXTURES
    }
    for fixture in fixtures:
        bounds = [float(value) for value in fixture.attributes["bounds"].split(",")]
        assert len(bounds) == 6
        assert bounds[0] < bounds[3]
        assert bounds[1] < bounds[4]
        assert bounds[2] < bounds[5]
        assert bounds[0] <= fixture.position[0] <= bounds[3]
        assert bounds[1] <= fixture.position[1] <= bounds[4]
        assert bounds[2] <= fixture.position[2] <= bounds[5]
        assert fixture.attributes["static"] == "true"
        body_name = next(
            body for body, entity_id, _category, _label in shared_world.FIXTURES
            if entity_id == fixture.entity_id
        )
        body_id = mujoco.mj_name2id(shared_world.model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        geom_centers = [
            shared_world.data.geom_xpos[geom_id]
            for geom_id in range(shared_world.model.ngeom)
            if int(shared_world.model.geom_bodyid[geom_id]) in shared_world._body_subtree(body_id)
        ]
        assert any(
            all(bounds[axis] <= center[axis] <= bounds[axis + 3] for axis in range(3))
            for center in geom_centers
        )


def test_reset_preserves_data_identity_for_long_lived_runtime_views(shared_world) -> None:
    sender = RoboCasaRobotView(shared_world, "robot-1")

    shared_world.reset()

    assert sender.data is shared_world.data


def test_only_current_custodian_can_pick(shared_world) -> None:
    shared_world.reset()
    receiver = RoboCasaRobotView(shared_world, "robot-2")

    result = receiver.pick("red-block")

    assert not result.success
    assert result.code == "RESOURCE_NOT_OWNED"


def test_sender_place_becomes_receiver_observation(shared_world) -> None:
    shared_world.reset()
    sender = RoboCasaRobotView(shared_world, "robot-1")
    receiver = RoboCasaRobotView(shared_world, "robot-2")

    assert sender.pick("red-block").success
    assert sender.verify_grasp("red-block").success
    assert sender.place("handoff-zone").success

    entity = _entity(receiver, "red-block")
    assert entity.relations["inside"] == "handoff-zone"
    assert shared_world.custodian == "robot-2"
    assert shared_world.owner == "environment"
    assert receiver.verify_inside("red-block", "handoff-zone").success


def test_receiver_finishes_same_physical_handoff(shared_world) -> None:
    shared_world.reset()
    sender = RoboCasaRobotView(shared_world, "robot-1")
    receiver = RoboCasaRobotView(shared_world, "robot-2")
    assert sender.pick("red-block").success
    assert sender.place("handoff-zone").success

    assert receiver.pick("red-block").success
    assert receiver.place("right-target-zone").success

    assert shared_world.completed
    assert _entity(sender, "red-block").relations["inside"] == "right-target-zone"
    assert receiver.verify_inside("red-block", "right-target-zone").success
    joint_id = mujoco.mj_name2id(
        shared_world.model, mujoco.mjtObj.mjOBJ_JOINT, "red-block-joint"
    )
    dof_address = int(shared_world.model.jnt_dofadr[joint_id])
    assert max(abs(value) for value in shared_world.data.qvel[dof_address : dof_address + 6]) < 1e-8


def test_occupancy_uses_world_frame_and_stable_resolution(shared_world) -> None:
    view = RoboCasaRobotView(shared_world, "robot-1")

    grid = view.occupancy_grid()

    assert grid["frame_id"] == "world"
    assert grid["transform_revision"] == "robocasa-world-v1"
    assert grid["cell_size_m"] == 0.1
    assert grid["width"] > 0
    assert grid["height"] > 0
    assert len(grid["cells"]) == grid["width"] * grid["height"]


def test_occupancy_rasterizes_mujoco_fixture_footprints(shared_world) -> None:
    grid = RoboCasaRobotView(shared_world, "robot-1").occupancy_grid()

    occupied = sum(value > 0 for value in grid["cells"])
    assert occupied > 150
    floor = _entity(RoboCasaRobotView(shared_world, "robot-1"), "floor")
    bounds = [float(value) for value in floor.attributes["bounds"].split(",")]
    assert grid["origin_xy"][0] <= bounds[0]
    assert grid["origin_xy"][1] <= bounds[1]
    assert grid["origin_xy"][0] + grid["width"] * grid["cell_size_m"] >= bounds[3]
    assert grid["origin_xy"][1] + grid["height"] * grid["cell_size_m"] >= bounds[4]


def test_occupancy_index_range_uses_half_open_upper_boundary() -> None:
    assert _grid_index_range(0.2, 0.5, 0.0, 0.1, 10) == range(2, 5)


def test_zones_publish_grounding_relations_used_by_natural_language(shared_world) -> None:
    view = RoboCasaRobotView(shared_world, "robot-2")

    assert _entity(view, "left-start-zone").category == "source_zone"
    assert _entity(view, "left-start-zone").relation == "left_side"
    assert _entity(view, "handoff-zone").relation == "between_robots"
    assert _entity(view, "right-target-zone").relation == "right_side"
    assert len(view.resolve_all(category="target_zone", relation="right_side")) == 1


def test_overview_camera_renders_png_from_shared_state(shared_world) -> None:
    renderer = SceneRenderer(width=160, height=120)
    try:
        frame = renderer.render(shared_world.model, shared_world.cached_render_data())
    finally:
        renderer.close()

    assert frame is not None
    assert frame.media_type == "image/png"
    assert frame.data.startswith(b"\x89PNG\r\n\x1a\n")
