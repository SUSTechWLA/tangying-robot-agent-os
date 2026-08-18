import mujoco
import pytest
from tangying_sim.model import MODEL_REVISION
from tangying_sim.world import TabletopWorld


def test_world_loads_pinned_task_model_without_repositioning_objects():
    world = TabletopWorld.seeded(7)

    assert mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_BODY, "chassis") >= 0
    assert world.robot_state()["model_revision"] == MODEL_REVISION
    assert world.resolve(category="cup", color="red").position == pytest.approx(
        (0.34, 0.49, 0.80), abs=1e-3
    )


def test_reset_reuses_compiled_model_and_starts_a_clean_episode():
    world = TabletopWorld.seeded(7)
    compiled_model = world.model
    assert world.pick("red-cup").success

    result = world.reset()

    assert result is world
    assert world.model is compiled_model
    assert world.robot_state()["episode"] == 2
    assert world.robot_state()["held"] == ""
    assert world.robot_state()["placements"] == {}


def test_pick_place_changes_mujoco_scene_and_verifies_destination():
    world = TabletopWorld.seeded(7)
    cup = world.resolve(category="cup", color="red")
    target = world.resolve(category="storage_bin", relation="right_side")

    before_steps = world.step_count
    assert world.pick(cup.entity_id).success
    assert world.place(target.entity_id).success
    verification = world.verify_inside(cup.entity_id, target.entity_id)

    assert world.step_count > before_steps
    assert verification.success
    assert verification.confidence >= 0.9


def test_pick_actuates_official_arm_and_exposes_robot_entity_and_state():
    world = TabletopWorld.seeded(7)
    before = world.joint_positions()

    result = world.pick("red-cup")
    after = world.joint_positions()

    assert result.success
    assert any(after[name] != pytest.approx(value) for name, value in before.items())
    assert world.robot_state()["held"] == "red-cup"
    assert world.robot_state()["active_tool"] in {"left_arm", "right_arm"}
    assert {entity.entity_id for entity in world.entities()} >= {"xlerobot", "table", "floor"}


def test_place_records_verified_placement_in_rich_robot_state():
    world = TabletopWorld.seeded(7)
    assert world.pick("red-cup").success

    assert world.place("right-bin").success
    verification = world.verify_inside("red-cup", "right-bin")
    state = world.robot_state()

    assert verification.success
    assert state["placements"] == {"red-cup": "right-bin"}
    assert state["target"] == "right-bin"
    assert state["verification_confidence"] >= 0.9
    assert state["grippers"]["left"] in {"open", "closed"}
    assert state["grippers"]["right"] in {"open", "closed"}
    assert set(state["end_effectors"]) == {"left", "right"}


def test_fetch_places_object_on_front_delivery_tray():
    world = TabletopWorld.seeded(7)
    cup = world.resolve(category="cup", color="red")
    tray = world.resolve(category="delivery_tray", relation="front_side")

    assert world.pick(cup.entity_id).success
    assert world.place(tray.entity_id).success
    verification = world.verify_inside(cup.entity_id, tray.entity_id)

    assert verification.success
    assert verification.confidence >= 0.9


def test_ambiguous_scene_requires_clarification():
    world = TabletopWorld.seeded(8, duplicate_red_cup=True)
    result = world.resolve_all(category="cup", color="red")
    assert len(result) == 2


@pytest.mark.parametrize(
    ("category", "color"),
    [
        ("cup", "red"),
        ("cup", "blue"),
        ("cup", "green"),
        ("bottle", "red"),
        ("bottle", "blue"),
        ("bottle", "green"),
        ("block", "red"),
        ("block", "blue"),
        ("block", "green"),
    ],
)
def test_all_advertised_objects_support_pick_place_and_fetch(category, color):
    world = TabletopWorld.seeded(7)
    obj = world.resolve(category=category, color=color)
    right = world.resolve(category="storage_bin", relation="right_side")
    assert world.pick(obj.entity_id).success
    assert world.place(right.entity_id).success
    assert world.verify_inside(obj.entity_id, right.entity_id).success

    world = TabletopWorld.seeded(8)
    obj = world.resolve(category=category, color=color)
    tray = world.resolve(category="delivery_tray", relation="front_side")
    assert world.pick(obj.entity_id).success
    assert world.place(tray.entity_id).success
    assert world.verify_inside(obj.entity_id, tray.entity_id).success


def test_left_bin_is_grounded_and_verifiable():
    world = TabletopWorld.seeded(7)
    obj = world.resolve(category="block", color="green")
    left = world.resolve(category="storage_bin", relation="left_side")
    assert left.entity_id == "left-bin"
    assert world.pick(obj.entity_id).success
    assert world.place(left.entity_id).success
    assert world.verify_inside(obj.entity_id, left.entity_id).success


def test_sequential_manipulation_changes_two_objects_in_one_world():
    world = TabletopWorld.seeded(7)
    red_cup = world.resolve(category="cup", color="red")
    right = world.resolve(category="storage_bin", relation="right_side")
    assert world.pick(red_cup.entity_id).success
    assert world.place(right.entity_id).success
    assert world.verify_inside(red_cup.entity_id, right.entity_id).success

    blue_cup = world.resolve(category="cup", color="blue")
    tray = world.resolve(category="delivery_tray", relation="front_side")
    assert world.pick(blue_cup.entity_id).success
    assert world.place(tray.entity_id).success
    assert world.verify_inside(blue_cup.entity_id, tray.entity_id).success
