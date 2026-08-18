import mujoco
import numpy as np
import pytest
from tangying_sim.model import MODEL_REVISION
from tangying_sim.tools import ToolContext
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


def test_pick_rejects_an_object_outside_both_arms_reach():
    world = TabletopWorld.seeded(7)
    world._set_free_body_position("red_cup_free", (1.5, 1.5, 0.80))
    mujoco.mj_forward(world.model, world.data)

    result = world.pick("red-cup")

    assert not result.success
    assert result.code == "TARGET_UNREACHABLE"
    assert world.robot_state()["held"] == ""


def test_successful_pick_attaches_object_to_reported_end_effector(monkeypatch):
    world = TabletopWorld.seeded(7)
    attachment_distances = []
    original_set_position = world._set_free_body_position

    def record_attachment(joint_name, position):
        if world._held is not None:
            arm = world.active_arm
            end_effector = world.robot_state()["end_effectors"][arm]
            attachment_distances.append(
                float(np.linalg.norm(np.asarray(position) - np.asarray(end_effector)))
            )
        original_set_position(joint_name, position)

    monkeypatch.setattr(world, "_set_free_body_position", record_attachment)

    result = world.pick("red-cup")
    entity = next(item for item in world.entities() if item.entity_id == "red-cup")
    arm = world.active_arm
    end_effector = world.robot_state()["end_effectors"][arm]

    assert result.success
    assert attachment_distances
    assert max(attachment_distances) <= world.GRASP_TOLERANCE
    assert np.linalg.norm(np.asarray(entity.position) - np.asarray(end_effector)) <= world.GRASP_TOLERANCE


def test_pick_reaches_object_with_fixed_jaw_before_setting_held():
    held_transition_distances = []
    object_positions_at_transition = []

    class TrackingWorld(TabletopWorld):
        def __setattr__(self, name, value):
            if name == "_held" and value is not None and hasattr(self, "_pickable_joints"):
                joint = self._pickable_joints[value]
                object_position = self._joint_position(joint)
                end_effector = self.end_effector_position(self.active_arm)
                held_transition_distances.append(
                    float(np.linalg.norm(object_position - np.asarray(end_effector)))
                )
                object_positions_at_transition.append(tuple(object_position))
            super().__setattr__(name, value)

    world = TrackingWorld.seeded(7)
    initial_position = world.resolve(category="cup", color="red").position

    result = world.pick("red-cup")

    assert result.success
    assert len(held_transition_distances) == 1
    assert held_transition_distances[0] <= world.GRASP_TOLERANCE
    assert object_positions_at_transition[0] == pytest.approx(initial_position, abs=1e-3)


def test_pick_reports_grasp_not_reached_without_holding_or_moving_object(monkeypatch):
    world = TabletopWorld.seeded(7)
    before = world.resolve(category="cup", color="red").position
    monkeypatch.setattr(world.motion, "approach_body", lambda *_args, **_kwargs: False)

    result = world.pick("red-cup")

    assert not result.success
    assert result.code == "GRASP_NOT_REACHED"
    assert world.robot_state()["held"] == ""
    assert world.resolve(category="cup", color="red").position == pytest.approx(before, abs=1e-3)


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


def test_place_rejects_destination_unreachable_by_active_arm_without_side_effects():
    world = TabletopWorld.seeded(7)
    assert world.pick("blue-cup").success
    assert world.active_arm == "left"
    held_before = world.robot_state()["held"]
    object_before = next(item.position for item in world.entities() if item.entity_id == "blue-cup")
    joints_before = world.joint_positions()

    result = world.place("right-bin")

    assert not result.success
    assert result.code == "TARGET_UNREACHABLE"
    assert world.robot_state()["held"] == held_before
    assert next(item.position for item in world.entities() if item.entity_id == "blue-cup") == pytest.approx(
        object_before
    )
    assert world.joint_positions() == pytest.approx(joints_before)


@pytest.mark.parametrize(
    ("object_id", "destination_id"),
    [("red-cup", "right-bin"), ("blue-bottle", "front-tray")],
)
def test_place_reaches_destination_before_release_without_teleporting(
    monkeypatch, object_id, destination_id
):
    release_distances = []

    class TrackingWorld(TabletopWorld):
        def __setattr__(self, name, value):
            previous = getattr(self, "_held", None)
            if name == "_held" and previous is not None and value is None:
                object_position = np.asarray(
                    next(item.position for item in self.entities() if item.entity_id == previous)
                )
                destination_position = np.asarray(
                    next(item.position for item in self.entities() if item.entity_id == self._target)
                )
                release_distances.append(
                    (
                        float(np.linalg.norm(object_position[:2] - destination_position[:2])),
                        float(np.linalg.norm(object_position - destination_position)),
                    )
                )
            super().__setattr__(name, value)

    world = TrackingWorld.seeded(7)
    context = ToolContext(world)
    assert world.tools.execute(
        "plan_grasp",
        context,
        target_ref=object_id,
        parameters={"destinationId": destination_id},
    ).success
    assert world.pick(object_id).success
    attachment_distances = []
    original_set_position = world._set_free_body_position

    def record_attachment(joint_name, position):
        if world._held is not None:
            end_effector = world.end_effector_position(world.active_arm)
            attachment_distances.append(
                float(np.linalg.norm(np.asarray(position) - np.asarray(end_effector)))
            )
        original_set_position(joint_name, position)

    monkeypatch.setattr(world, "_set_free_body_position", record_attachment)

    result = world.place(destination_id)

    assert result.success
    assert release_distances
    assert release_distances[0][0] <= 0.02
    assert release_distances[0][1] <= 0.08
    assert max(attachment_distances) <= world.GRASP_TOLERANCE


def test_place_not_reached_keeps_object_held(monkeypatch):
    world = TabletopWorld.seeded(7)
    context = ToolContext(world)
    assert world.tools.execute(
        "plan_grasp",
        context,
        target_ref="red-cup",
        parameters={"destinationId": "right-bin"},
    ).success
    assert world.pick("red-cup").success
    monkeypatch.setattr(world.motion, "approach_body", lambda *_args, **_kwargs: False)

    result = world.place("right-bin")

    assert not result.success
    assert result.code == "PLACE_NOT_REACHED"
    assert world.robot_state()["held"] == "red-cup"


def test_verify_grasp_requires_closed_jaw_and_recovery_releases_object():
    world = TabletopWorld.seeded(7)
    assert world.pick("red-cup").success
    assert world.verify_grasp("red-cup").success

    assert world.recover_to_safe_pose().success
    verification = world.verify_grasp("red-cup")

    assert not verification.success
    assert verification.code == "GRASP_LOST"
    assert world.robot_state()["held"] == ""


def test_verify_grasp_rejects_a_held_object_outside_attachment_tolerance():
    world = TabletopWorld.seeded(7)
    assert world.pick("red-cup").success
    world._set_free_body_position("red_cup_free", (0.0, 0.0, 1.2))
    mujoco.mj_forward(world.model, world.data)

    verification = world.verify_grasp("red-cup")

    assert not verification.success
    assert verification.code == "GRASP_LOST"


def test_recover_returns_both_arms_and_jaws_home_even_when_one_is_active():
    world = TabletopWorld.seeded(7)
    world.motion.move_named("left", "LIFT", steps=2)
    world.motion.move_named("right", "PLACE", steps=2)
    world.set_active_arm("left")

    assert world.recover_to_safe_pose().success

    positions = world.joint_positions()
    for arm in ("left", "right"):
        for name, expected in world.motion.target_for(arm, "HOME").items():
            assert positions[name] == pytest.approx(expected)
    assert world.robot_state()["grippers"] == {"left": "open", "right": "open"}


def test_recover_resets_base_translation_and_heading_after_failed_motion():
    world = TabletopWorld.seeded(7)
    reached = world.motion.approach_body(
        "left", "Fixed_Jaw_2", (2.0, 2.0, 1.2), max_steps=2
    )
    assert not reached
    heading_id = mujoco.mj_name2id(
        world.model, mujoco.mjtObj.mjOBJ_JOINT, "hinge_joint_z"
    )
    heading_address = world.model.jnt_qposadr[heading_id]
    world.data.qpos[heading_address] = 0.4
    mujoco.mj_forward(world.model, world.data)
    assert any(
        abs(world.data.qpos[world.model.jnt_qposadr[world.model.joint(name).id]]) > 0
        for name in ("slide_joint_x", "slide_joint_y", "hinge_joint_z")
    )

    assert world.recover_to_safe_pose().success

    for name in ("slide_joint_x", "slide_joint_y", "hinge_joint_z"):
        joint_id = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_JOINT, name)
        address = world.model.jnt_qposadr[joint_id]
        assert world.data.qpos[address] == pytest.approx(0.0)


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


def test_duplicate_red_cup_has_independent_body_and_free_joint():
    world = TabletopWorld.seeded(8, duplicate_red_cup=True)
    first_before = world.resolve_all(category="cup", color="red")[0].position

    assert mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_BODY, "red_cup_2") >= 0
    assert mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_JOINT, "red_cup_2_free") >= 0
    assert world.pick("red-cup-2").success

    first_after = next(item.position for item in world.entities() if item.entity_id == "red-cup")
    assert first_after == pytest.approx(first_before, abs=1e-3)


def test_duplicate_body_is_parked_and_hidden_when_disabled():
    world = TabletopWorld.seeded(8, duplicate_red_cup=False)

    assert "red-cup-2" not in {item.entity_id for item in world.entities()}
    body_id = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_BODY, "red_cup_2")
    assert body_id >= 0
    assert np.linalg.norm(world.data.xpos[body_id, :2]) > 1.0


def test_reset_restores_enabled_duplicate_to_its_independent_reachable_pose():
    world = TabletopWorld.seeded(8, duplicate_red_cup=True)
    assert world.pick("red-cup-2").success

    world.reset()

    duplicate = next(item for item in world.entities() if item.entity_id == "red-cup-2")
    assert duplicate.position == pytest.approx(world._DUPLICATE_RED_CUP[-1], abs=1e-3)
    assert world.pick("red-cup-2").success


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
    assert world.tools.execute(
        "plan_grasp",
        ToolContext(world),
        target_ref=obj.entity_id,
        parameters={"destinationId": right.entity_id},
    ).success
    assert world.pick(obj.entity_id).success
    assert world.place(right.entity_id).success
    assert world.verify_inside(obj.entity_id, right.entity_id).success

    world = TabletopWorld.seeded(8)
    obj = world.resolve(category=category, color=color)
    tray = world.resolve(category="delivery_tray", relation="front_side")
    assert world.tools.execute(
        "plan_grasp",
        ToolContext(world),
        target_ref=obj.entity_id,
        parameters={"destinationId": tray.entity_id},
    ).success
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


@pytest.mark.parametrize(
    ("entity_id", "body_name"),
    [
        ("left-bin", "left_bin"),
        ("right-bin", "right_bin"),
        ("front-tray", "front_tray"),
    ],
)
def test_destination_entity_pose_comes_from_mujoco_body_state(entity_id, body_name):
    world = TabletopWorld.seeded(7)
    entity = next(item for item in world.entities() if item.entity_id == entity_id)
    body_id = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_BODY, body_name)

    assert entity.position == pytest.approx(world.data.xpos[body_id])


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
