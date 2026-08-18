from __future__ import annotations

import pytest
from tangying_sim.training.env import Goal, SemanticToolEnv

RED_CUP_TO_RIGHT_BIN = Goal("cup", "red", "storage_bin", "right_side")
BLUE_BOTTLE_TO_FRONT_TRAY = Goal("bottle", "blue", "delivery_tray", "front_side")
CORRECT_SEQUENCE = (
    "observe_scene",
    "resolve_targets",
    "plan_grasp",
    "manipulation.pick",
    "verify_grasp",
    "manipulation.place",
    "verify_placement",
)


def test_correct_tool_sequence_reaches_verified_terminal_reward():
    env = SemanticToolEnv(seed=7)
    observation, info = env.reset(goal=RED_CUP_TO_RIGHT_BIN)
    total = 0.0

    for action in CORRECT_SEQUENCE:
        observation, reward, terminated, truncated, info = env.step(action)
        total += reward

    assert terminated and not truncated
    assert info["success"] is True
    assert observation.placement_verified is True
    assert observation.object_id == "red-cup"
    assert observation.destination_id == "right-bin"
    assert total > 0


def test_wrong_tool_order_is_penalized_without_false_success():
    env = SemanticToolEnv(seed=7)
    env.reset(goal=BLUE_BOTTLE_TO_FRONT_TRAY)

    observation, reward, terminated, truncated, info = env.step("manipulation.place")

    assert reward < 0
    assert not terminated and not truncated
    assert info["success"] is False
    assert observation.phase == "observe"


def test_step_budget_truncates_and_rejects_steps_after_terminal():
    env = SemanticToolEnv(seed=7, max_steps=2)
    env.reset(goal=RED_CUP_TO_RIGHT_BIN)

    env.step("manipulation.place")
    _, _, terminated, truncated, info = env.step("manipulation.place")

    assert not terminated and truncated
    assert info["code"] == "STEP_BUDGET_EXHAUSTED"
    with pytest.raises(RuntimeError, match="reset"):
        env.step("observe_scene")


def test_transient_failure_requires_recovery_before_progress_can_resume():
    env = SemanticToolEnv(seed=7, transient_failure_rate=1.0, max_steps=12)
    env.reset(goal=RED_CUP_TO_RIGHT_BIN)
    env.step("observe_scene")
    env.step("resolve_targets")

    failed, reward, terminated, truncated, info = env.step("plan_grasp")
    blocked, blocked_reward, *_ = env.step("plan_grasp")
    recovered, recovery_reward, *_ = env.step("recover_to_safe_pose")

    assert failed.recovery_required is True
    assert reward < 0 and info["code"] == "TRANSIENT_TOOL_FAILURE"
    assert blocked.recovery_required is True and blocked_reward < 0
    assert recovered.recovery_required is False and recovery_reward > 0
    assert not terminated and not truncated


def test_recovery_after_interrupted_place_replans_and_can_finish_episode():
    env = SemanticToolEnv(seed=7, transient_failure_rate=0.0, max_steps=20)
    env.reset(goal=RED_CUP_TO_RIGHT_BIN)
    for action in CORRECT_SEQUENCE[:5]:
        env.step(action)
    env.transient_failure_rate = 1.0
    failed, *_ = env.step("manipulation.place")
    env.transient_failure_rate = 0.0

    recovered, *_ = env.step("recover_to_safe_pose")
    assert failed.recovery_required is True
    assert recovered.phase == "plan"
    assert recovered.held == ""
    assert recovered.grasp_verified is False

    for action in CORRECT_SEQUENCE[2:]:
        observation, _, terminated, truncated, info = env.step(action)

    assert terminated and not truncated
    assert info["success"] is True
    assert observation.placement_verified is True


def test_reset_reseeds_goal_sampling_and_returns_finite_state_encoding():
    first = SemanticToolEnv(seed=99)
    second = SemanticToolEnv(seed=1)

    first_observation, _ = first.reset(seed=1234)
    second_observation, _ = second.reset(seed=1234)

    assert first_observation.goal == second_observation.goal
    assert first_observation.state_key() == second_observation.state_key()
    assert all(isinstance(part, str) and part for part in first_observation.state_key())


def test_seeded_reset_randomizes_reachable_start_position_reproducibly():
    first = SemanticToolEnv(seed=7)
    second = SemanticToolEnv(seed=8)
    replay = SemanticToolEnv(seed=7)

    _, first_info = first.reset(goal=RED_CUP_TO_RIGHT_BIN)
    _, second_info = second.reset(goal=RED_CUP_TO_RIGHT_BIN)
    _, replay_info = replay.reset(goal=RED_CUP_TO_RIGHT_BIN)

    assert first_info["startPosition"] == replay_info["startPosition"]
    assert first_info["startPosition"] != second_info["startPosition"]
    for env in (first, second):
        for action in CORRECT_SEQUENCE:
            _, _, terminated, truncated, _ = env.step(action)
        assert terminated and not truncated


@pytest.mark.parametrize(
    "unsupported",
    [
        Goal("cup", "purple", "storage_bin", "right_side"),
        Goal("delivery_tray", "gray", "storage_bin", "right_side"),
        Goal("cup", "red", "storage_bin", "center"),
    ],
)
def test_unknown_action_and_unsupported_goal_fail_closed(unsupported):
    env = SemanticToolEnv(seed=7)
    env.reset(goal=RED_CUP_TO_RIGHT_BIN)
    with pytest.raises(ValueError, match="unknown semantic tool action"):
        env.step("transport.approve")

    with pytest.raises(ValueError, match="unsupported goal"):
        env.reset(goal=unsupported)
