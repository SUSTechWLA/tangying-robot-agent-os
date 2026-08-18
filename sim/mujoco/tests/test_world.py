import pytest
from tangying_sim.world import TabletopWorld


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
