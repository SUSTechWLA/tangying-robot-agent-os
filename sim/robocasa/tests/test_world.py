from __future__ import annotations

import mujoco
import pytest
from tangying_robocasa.composer import SceneConfig, compose_handoff_scene
from tangying_robocasa.world import RoboCasaRobotView, RoboCasaSharedWorld
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
