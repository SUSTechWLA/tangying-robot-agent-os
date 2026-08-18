import mujoco
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
        target_ref="green-cup",
        parameters={"destinationId": "right-bin"},
    )
    assert plan.payload["arm"] == "right"

    result = world.tools.execute("manipulation.pick", context, target_ref="green-cup")

    assert result.success
    assert world.robot_state()["active_tool"] == "right_arm"
