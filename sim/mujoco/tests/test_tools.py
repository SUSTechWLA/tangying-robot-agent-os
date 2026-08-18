import mujoco
import numpy as np
import pytest
from tangying_sim.motion import MotionController, MotionLimitError
from tangying_sim.tools import ToolContext
from tangying_sim.world import TabletopWorld


def test_motion_clamps_joint_targets_and_rejects_invalid_step_counts():
    world = TabletopWorld.seeded(7)
    motion = MotionController(world.model, world.data)

    motion.interpolate("left", {"Rotation": 99.0}, steps=3)

    joint_id = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_JOINT, "Rotation_R")
    address = world.model.jnt_qposadr[joint_id]
    assert world.data.qpos[address] == pytest.approx(world.model.jnt_range[joint_id, 1])
    with pytest.raises(MotionLimitError, match="steps"):
        motion.move_named("left", "HOME", steps=0)
    with pytest.raises(MotionLimitError, match="steps"):
        motion.move_named("left", "HOME", steps=motion.MAX_STEPS + 1)


def test_registry_has_one_tool_per_capability_and_rejects_unknown_skills():
    world = TabletopWorld.seeded(7)

    assert set(world.tools.capabilities) == {
        "observe_scene",
        "resolve_targets",
        "plan_grasp",
        "manipulation.pick",
        "verify_grasp",
        "manipulation.place",
        "verify_placement",
        "recover_to_safe_pose",
    }
    result = world.tools.execute("not.allowed", ToolContext(world))

    assert not result.success
    assert result.code == "SKILL_NOT_ALLOWED"


def test_recover_moves_selected_arm_to_home():
    world = TabletopWorld.seeded(7)
    world.motion.move_named("left", "LIFT", steps=4)

    result = world.tools.execute("recover_to_safe_pose", ToolContext(world), parameters={"arm": "left"})

    assert result.success
    for name, expected in world.motion.target_for("left", "HOME").items():
        assert world.joint_positions()[name] == pytest.approx(expected)
    assert world.robot_state()["active_tool"] == ""


def test_pick_honors_arm_planned_for_both_source_and_destination():
    world = TabletopWorld.seeded(7)
    context = ToolContext(world)

    plan = world.tools.execute(
        "plan_grasp",
        context,
        target_ref="green-bottle",
        parameters={"destinationId": "right-bin"},
    )
    assert plan.payload["arm"] == "right"

    result = world.tools.execute("manipulation.pick", context, target_ref="green-bottle")

    assert result.success
    assert world.robot_state()["active_tool"] == "right_arm"
    assert world.tools.execute(
        "manipulation.place", context, target_ref="right-bin"
    ).success


@pytest.mark.parametrize(
    ("parameters", "code"),
    [
        ({"objectId": "missing", "destinationId": "right-bin"}, "OBJECT_NOT_FOUND"),
        ({"objectId": "red-cup", "destinationId": "missing"}, "DESTINATION_NOT_FOUND"),
    ],
)
def test_resolve_targets_validates_object_and_destination(parameters, code):
    world = TabletopWorld.seeded(7)

    result = world.tools.execute("resolve_targets", ToolContext(world), parameters=parameters)

    assert not result.success
    assert result.code == code


def test_resolve_targets_returns_both_grounded_ids_and_allows_empty_observation():
    world = TabletopWorld.seeded(7)
    context = ToolContext(world)

    result = world.tools.execute(
        "resolve_targets",
        context,
        parameters={"objectId": "red-cup", "destinationId": "right-bin"},
    )
    empty = world.tools.execute("resolve_targets", context)

    assert result.success
    assert result.payload == {"object_id": "red-cup", "destination_id": "right-bin"}
    assert empty.success


def test_resolve_targets_accepts_legacy_target_ref_for_either_entity_kind():
    world = TabletopWorld.seeded(7)
    context = ToolContext(world)

    object_result = world.tools.execute("resolve_targets", context, target_ref="red-cup")
    destination_result = world.tools.execute(
        "resolve_targets", context, target_ref="right-bin"
    )

    assert object_result.payload == {"object_id": "red-cup"}
    assert destination_result.payload == {"destination_id": "right-bin"}


def test_plan_grasp_rejects_unknown_destination():
    world = TabletopWorld.seeded(7)

    result = world.tools.execute(
        "plan_grasp",
        ToolContext(world),
        target_ref="red-cup",
        parameters={"destinationId": "missing"},
    )

    assert not result.success
    assert result.code == "DESTINATION_NOT_FOUND"


def test_plan_grasp_rejects_source_without_a_common_reachable_arm():
    world = TabletopWorld.seeded(7)
    world._set_free_body_position("red_cup_free", (1.5, 1.5, 0.80))
    mujoco.mj_forward(world.model, world.data)

    result = world.tools.execute(
        "plan_grasp",
        ToolContext(world),
        target_ref="red-cup",
        parameters={"destinationId": "right-bin"},
    )

    assert not result.success
    assert result.code == "TARGET_UNREACHABLE"


def test_pick_rechecks_reach_after_a_successful_plan():
    world = TabletopWorld.seeded(7)
    context = ToolContext(world)
    assert world.tools.execute("plan_grasp", context, target_ref="red-cup").success
    world._set_free_body_position("red_cup_free", (1.5, 1.5, 0.80))
    mujoco.mj_forward(world.model, world.data)

    result = world.tools.execute("manipulation.pick", context, target_ref="red-cup")

    assert not result.success
    assert result.code == "TARGET_UNREACHABLE"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_motion_rejects_non_finite_interpolation_targets_without_mutation(invalid):
    world = TabletopWorld.seeded(7)
    before = world.data.qpos.copy()

    with pytest.raises(MotionLimitError, match="finite") as raised:
        world.motion.interpolate("left", {"Rotation": invalid}, steps=2)

    assert raised.value.code == "MOTION_LIMIT"
    assert world.data.qpos == pytest.approx(before)


def test_motion_rejects_non_finite_start_tolerance_jacobian_and_delta(monkeypatch):
    world = TabletopWorld.seeded(7)
    rotation_id = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_JOINT, "Rotation_R")
    rotation_address = world.model.jnt_qposadr[rotation_id]
    world.data.qpos[rotation_address] = np.nan
    with pytest.raises(MotionLimitError, match="finite"):
        world.motion.interpolate("left", {"Rotation": 0.0}, steps=2)

    world = TabletopWorld.seeded(7)
    with pytest.raises(MotionLimitError, match="tolerance"):
        world.motion.approach_body("left", "Fixed_Jaw_2", (0.0, 0.0, 0.0), tolerance=np.inf)
    with pytest.raises(MotionLimitError, match="target"):
        world.motion.approach_body("left", "Fixed_Jaw_2", (np.nan, 0.0, 0.0))

    original_jacobian = mujoco.mj_jacBody

    def non_finite_jacobian(model, data, jacp, jacr, body_id):
        original_jacobian(model, data, jacp, jacr, body_id)
        jacp[0, 0] = np.nan

    monkeypatch.setattr(mujoco, "mj_jacBody", non_finite_jacobian)
    with pytest.raises(MotionLimitError, match="Jacobian"):
        world.motion.approach_body("left", "Fixed_Jaw_2", (0.2, 0.2, 1.0))

    monkeypatch.setattr(mujoco, "mj_jacBody", original_jacobian)
    monkeypatch.setattr(
        np.linalg,
        "solve",
        lambda *_args, **_kwargs: np.full(3, np.inf),
    )
    with pytest.raises(MotionLimitError, match="delta"):
        world.motion.approach_body("left", "Fixed_Jaw_2", (0.2, 0.2, 1.0))


def test_motion_limit_error_is_mapped_to_structured_tool_failure(monkeypatch):
    world = TabletopWorld.seeded(7)

    def fail_motion(*_args, **_kwargs):
        raise MotionLimitError("target must be finite")

    monkeypatch.setattr(world.motion, "approach_body", fail_motion)
    result = world.tools.execute(
        "manipulation.pick", ToolContext(world), target_ref="red-cup"
    )

    assert not result.success
    assert result.code == "MOTION_LIMIT"
    assert "finite" in result.message


def test_verify_tools_accept_parameter_only_and_legacy_target_references():
    world = TabletopWorld.seeded(7)
    context = ToolContext(world)
    assert world.pick("red-cup").success
    assert world.tools.execute(
        "verify_grasp", context, parameters={"objectId": "red-cup"}
    ).success
    assert world.tools.execute("verify_grasp", context, target_ref="red-cup").success
    assert world.place("right-bin").success
    assert world.tools.execute(
        "verify_placement",
        context,
        parameters={"objectId": "red-cup", "destinationId": "right-bin"},
    ).success
    assert world.tools.execute(
        "verify_placement",
        context,
        target_ref="right-bin",
        parameters={"objectId": "red-cup"},
    ).success


@pytest.mark.parametrize(
    ("skill", "target_ref", "parameters"),
    [
        ("verify_grasp", "blue-cup", {"objectId": "red-cup"}),
        (
            "verify_placement",
            "left-bin",
            {"objectId": "red-cup", "destinationId": "right-bin"},
        ),
    ],
)
def test_verify_tools_reject_conflicting_parameter_and_legacy_references(
    skill, target_ref, parameters
):
    world = TabletopWorld.seeded(7)

    result = world.tools.execute(
        skill,
        ToolContext(world),
        target_ref=target_ref,
        parameters=parameters,
    )

    assert not result.success
    assert result.code == "TARGET_REFERENCE_CONFLICT"
