from __future__ import annotations

import pytest
from tangying_sim.tools import default_tool_registry
from tangying_sim.training.env import (
    ACTION_SPECS,
    ACTIONS,
    Goal,
    SemanticToolEnv,
    candidate_action_indices,
)

RED_CUP_TO_RIGHT_BIN = Goal("cup", "red", "storage_bin", "right_side")
BLUE_BOTTLE_TO_FRONT_TRAY = Goal("bottle", "blue", "delivery_tray", "front_side")


def _selector_actions(observation):
    object_slot = next(
        candidate.slot
        for candidate in observation.object_candidates
        if candidate.category == observation.goal.category
        and candidate.color == observation.goal.color
    )
    destination_slot = next(
        candidate.slot
        for candidate in observation.destination_candidates
        if candidate.category == observation.goal.destination_category
        and candidate.relation == observation.goal.destination_relation
    )
    return (
        f"resolve_targets?objectSlot={object_slot}",
        f"resolve_targets?destinationSlot={destination_slot}",
    )


def _correct_sequence(observation):
    object_action, destination_action = _selector_actions(observation)
    return (
        "observe_scene",
        object_action,
        destination_action,
        "plan_grasp",
        "manipulation.pick",
        "verify_grasp",
        "manipulation.place",
        "verify_placement",
    )


def test_correct_tool_sequence_reaches_verified_terminal_reward():
    env = SemanticToolEnv(seed=7)
    observation, info = env.reset(goal=RED_CUP_TO_RIGHT_BIN)
    sequence = _correct_sequence(observation)
    total = 0.0

    for action in sequence:
        observation, reward, terminated, truncated, info = env.step(action)
        total += reward

    assert terminated and not truncated
    assert info["success"] is True
    assert observation.placement_verified is True
    assert observation.object_id.startswith("entity-")
    assert observation.destination_id.startswith("entity-")
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
    env = SemanticToolEnv(seed=7, max_steps=1)
    env.reset(goal=RED_CUP_TO_RIGHT_BIN)

    _, reward, terminated, truncated, info = env.step("observe_scene")

    assert not terminated and truncated
    assert reward < 0
    assert info["code"] == "STEP_BUDGET_EXHAUSTED"
    with pytest.raises(RuntimeError, match="reset"):
        env.step("observe_scene")


def test_transient_failure_requires_recovery_before_progress_can_resume():
    env = SemanticToolEnv(seed=7, transient_failure_rate=1.0, max_steps=12)
    observation, _ = env.reset(goal=RED_CUP_TO_RIGHT_BIN)
    object_action, destination_action = _selector_actions(observation)
    env.step("observe_scene")
    env.step(object_action)
    env.step(destination_action)

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
    observation, _ = env.reset(goal=RED_CUP_TO_RIGHT_BIN)
    sequence = _correct_sequence(observation)
    for action in sequence[:6]:
        env.step(action)
    env.transient_failure_rate = 1.0
    failed, *_ = env.step("manipulation.place")
    env.transient_failure_rate = 0.0

    recovered, *_ = env.step("recover_to_safe_pose")
    assert failed.recovery_required is True
    assert recovered.phase == "plan"
    assert recovered.held == ""
    assert recovered.grasp_verified is False

    for action in sequence[3:]:
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


def test_pre_grounding_observation_has_candidates_without_resolved_truth_ids():
    env = SemanticToolEnv(seed=7)
    observation, _ = env.reset(goal=RED_CUP_TO_RIGHT_BIN)

    assert observation.object_id == ""
    assert observation.destination_id == ""
    assert observation.grounded_object_id == ""
    assert observation.grounded_destination_id == ""
    assert len(observation.object_candidates) == 9
    assert len(observation.destination_candidates) == 3
    assert all(
        candidate.opaque_id.startswith("entity-") for candidate in observation.object_candidates
    )
    assert "red-cup" not in "|".join(observation.state_key())
    assert "right-bin" not in "|".join(observation.state_key())

    observed, *_ = env.step("observe_scene")
    assert observed.object_id == observed.destination_id == ""


def test_state_key_distinguishes_goal_features_without_canonical_id_leakage():
    first = SemanticToolEnv(seed=7)
    second = SemanticToolEnv(seed=7)

    red_to_bin, _ = first.reset(goal=RED_CUP_TO_RIGHT_BIN)
    blue_to_tray, _ = second.reset(goal=BLUE_BOTTLE_TO_FRONT_TRAY)

    assert red_to_bin.phase == blue_to_tray.phase == "observe"
    assert red_to_bin.state_key() != blue_to_tray.state_key()
    assert "goal_object:cup:red" in red_to_bin.state_key()
    assert "goal_destination:storage_bin:right_side" in red_to_bin.state_key()
    assert "red-cup" not in "|".join(red_to_bin.state_key())
    assert "right-bin" not in "|".join(red_to_bin.state_key())


def test_opaque_aliases_and_candidate_slots_change_across_holdout_seeds():
    first = SemanticToolEnv(seed=7)
    holdout = SemanticToolEnv(seed=7007)

    first_observation, _ = first.reset(goal=RED_CUP_TO_RIGHT_BIN)
    holdout_observation, _ = holdout.reset(goal=RED_CUP_TO_RIGHT_BIN)
    first_aliases = {candidate.opaque_id for candidate in first_observation.object_candidates}
    holdout_aliases = {candidate.opaque_id for candidate in holdout_observation.object_candidates}
    first_order = [
        (candidate.category, candidate.color) for candidate in first_observation.object_candidates
    ]
    holdout_order = [
        (candidate.category, candidate.color) for candidate in holdout_observation.object_candidates
    ]

    assert first_aliases.isdisjoint(holdout_aliases)
    assert first_order != holdout_order


def test_wrong_grounded_object_and_destination_receive_explicit_negative_feedback():
    env = SemanticToolEnv(seed=7)
    observation, _ = env.reset(goal=RED_CUP_TO_RIGHT_BIN)
    grounding_observation, *_ = env.step("observe_scene")

    correct_object_action, correct_destination_action = _selector_actions(observation)
    wrong_object_slot = next(
        candidate.slot
        for candidate in observation.object_candidates
        if candidate.category != "cup" or candidate.color != "red"
    )
    wrong_destination_slot = next(
        candidate.slot
        for candidate in observation.destination_candidates
        if candidate.relation != "right_side"
    )
    available = {ACTIONS[index] for index in candidate_action_indices(grounding_observation)}
    assert len(available) == 9
    assert f"resolve_targets?objectSlot={wrong_object_slot}" in available
    wrong_object, object_reward, _, _, object_info = env.step(
        f"resolve_targets?objectSlot={wrong_object_slot}"
    )
    correct_object, correct_reward, *_ = env.step(correct_object_action)
    destination_available = {ACTIONS[index] for index in candidate_action_indices(correct_object)}
    wrong_destination, destination_reward, _, _, destination_info = env.step(
        f"resolve_targets?destinationSlot={wrong_destination_slot}"
    )

    assert wrong_object.phase == "ground_object"
    assert wrong_object.grounded_object_id.startswith("entity-")
    assert object_reward < 0 and object_info["code"] == "WRONG_OBJECT"
    assert object_reward == pytest.approx(env.STEP_COST + env.WRONG_GROUNDING_PENALTY)
    assert object_info["toolName"] == "resolve_targets"
    assert object_info["bindings"] == {"objectSlot": str(wrong_object_slot)}
    assert correct_object.phase == "ground_destination" and correct_reward > 0
    assert len(destination_available) == 3
    assert f"resolve_targets?destinationSlot={wrong_destination_slot}" in destination_available
    assert wrong_destination.phase == "ground_destination"
    assert wrong_destination.grounded_destination_id.startswith("entity-")
    assert destination_reward < 0
    assert destination_info["code"] == "WRONG_DESTINATION"
    assert correct_destination_action.startswith("resolve_targets?destinationSlot=")


def test_every_discrete_action_dispatches_a_registered_shared_tool():
    registered = set(default_tool_registry().capabilities)

    assert ACTION_SPECS
    assert all(spec.tool_name in registered for spec in ACTION_SPECS)
    assert all(not spec.tool_name.startswith("ground_") for spec in ACTION_SPECS)


def test_independent_unsafe_state_monitor_terminates_with_penalty():
    env = SemanticToolEnv(seed=7)
    env.reset(goal=RED_CUP_TO_RIGHT_BIN)
    assert env.world.pick("blue-cup").success

    _, reward, terminated, truncated, info = env.step("observe_scene")

    assert terminated and not truncated
    assert reward <= env.UNSAFE_STATE_PENALTY
    assert info["success"] is False
    assert info["code"] == "UNSAFE_STATE"
    assert info["unsafeReason"] == "WRONG_OBJECT_HELD"


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
        observation, _ = env.reset(goal=RED_CUP_TO_RIGHT_BIN)
        for action in _correct_sequence(observation):
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
