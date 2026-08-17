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


def test_ambiguous_scene_requires_clarification():
    world = TabletopWorld.seeded(8, duplicate_red_cup=True)
    result = world.resolve_all(category="cup", color="red")
    assert len(result) == 2
