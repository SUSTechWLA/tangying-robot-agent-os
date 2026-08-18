from __future__ import annotations

import json
from dataclasses import replace

import pytest
from tangying_sim.training.env import SemanticToolEnv
from tangying_sim.training.qlearning import (
    CheckpointError,
    catalog_fingerprint,
    evaluate,
    load_checkpoint,
    save_checkpoint,
    train,
)


class UnsafeEpisodeEnv(SemanticToolEnv):
    def reset(self, *, seed=None, goal=None):
        observation, info = super().reset(seed=seed, goal=goal)
        wrong_object = next(
            entity.entity_id
            for entity in self.world.entities()
            if entity.category in {"cup", "bottle", "block"}
            and (
                entity.category != observation.goal.category
                or entity.attributes.get("color") != observation.goal.color
            )
        )
        assert self.world.pick(wrong_object).success
        return self._observation(), info


class GroundingAuditEnv(SemanticToolEnv):
    wrong_grounding_actions = 0

    def step(self, action):
        transition = super().step(action)
        if transition[-1].get("code") in {"WRONG_OBJECT", "WRONG_DESTINATION"}:
            type(self).wrong_grounding_actions += 1
        return transition


def test_grounding_state_schema_version_rejects_pre_learning_contract(tmp_path):
    path = tmp_path / "policy.json"
    save_checkpoint(path, train(episodes=1, seed=7, transient_failure_rate=0.0))
    document = json.loads(path.read_text())
    document["stateSchemaVersion"] = 3
    path.write_text(json.dumps(document))

    with pytest.raises(CheckpointError, match="stateSchemaVersion"):
        load_checkpoint(path)


def test_epsilon_exploration_visits_penalized_wrong_grounding_slots():
    GroundingAuditEnv.wrong_grounding_actions = 0

    train(
        episodes=20,
        seed=7,
        transient_failure_rate=0.0,
        env_factory=GroundingAuditEnv,
    )

    assert GroundingAuditEnv.wrong_grounding_actions > 0


def test_qlearning_checkpoint_round_trip_and_seeded_evaluation(tmp_path):
    result = train(episodes=300, seed=11)
    path = tmp_path / "policy.json"

    save_checkpoint(path, result)
    policy = load_checkpoint(path)
    report = evaluate(policy, episodes=30, seed=29)
    opaque_holdout = evaluate(policy, episodes=60, seed=29029)

    assert report.success_rate >= 0.9
    assert opaque_holdout.success_rate >= 0.9
    assert report.by_goal_kind["fetch"]["episodes"] > 0
    assert report.by_goal_kind["pick_and_place"]["episodes"] > 0
    assert policy.tool_catalog_fingerprint == catalog_fingerprint()
    assert "red-cup" not in "".join(policy.q_table)
    assert "right-bin" not in "".join(policy.q_table)
    assert "entity-" not in "".join(policy.q_table)
    assert path.read_text().endswith("\n")
    assert not list(tmp_path.glob("*.tmp"))


def test_removing_learned_grounding_rows_breaks_opaque_holdout_evaluation():
    policy = train(episodes=300, seed=11)
    without_grounding = replace(
        policy,
        q_table={
            state: values
            for state, values in policy.q_table.items()
            if '"phase:ground_object"' not in state and '"phase:ground_destination"' not in state
        },
    )

    report = evaluate(without_grounding, episodes=60, seed=29029)

    assert report.success_rate < 0.5


def test_training_is_reproducible_for_seed_and_hyperparameters():
    first = train(episodes=80, seed=23, transient_failure_rate=0.0)
    second = train(episodes=80, seed=23, transient_failure_rate=0.0)

    assert first.q_table == second.q_table
    assert first.training_summary == second.training_summary


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schemaVersion", 99, "schemaVersion"),
        ("stateSchemaVersion", 99, "stateSchemaVersion"),
        ("actionSchemaVersion", 99, "actionSchemaVersion"),
        ("toolCatalogFingerprint", "stale", "tool catalog"),
    ],
)
def test_checkpoint_load_fails_closed_on_contract_mismatch(tmp_path, field, value, message):
    path = tmp_path / "policy.json"
    save_checkpoint(path, train(episodes=5, seed=3, transient_failure_rate=0.0))
    document = json.loads(path.read_text())
    document[field] = value
    path.write_text(json.dumps(document))

    with pytest.raises(CheckpointError, match=message):
        load_checkpoint(path)


def test_checkpoint_rejects_malformed_q_rows(tmp_path):
    path = tmp_path / "policy.json"
    save_checkpoint(path, train(episodes=5, seed=3, transient_failure_rate=0.0))
    document = json.loads(path.read_text())
    state = next(iter(document["qTable"]))
    document["qTable"][state] = [0.0]
    path.write_text(json.dumps(document))

    with pytest.raises(CheckpointError, match="Q-table row"):
        load_checkpoint(path)


def test_checkpoint_rejects_action_binding_catalog_mismatch(tmp_path):
    path = tmp_path / "policy.json"
    save_checkpoint(path, train(episodes=5, seed=3, transient_failure_rate=0.0))
    document = json.loads(path.read_text())
    document["actionCatalog"][1]["bindings"] = {"objectId": "tampered"}
    path.write_text(json.dumps(document))

    with pytest.raises(CheckpointError, match="action catalog"):
        load_checkpoint(path)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("hyperparameters", "maxSteps", 0),
        ("hyperparameters", "maxSteps", True),
        ("hyperparameters", "alpha", float("nan")),
        ("hyperparameters", "alpha", 1.1),
        ("hyperparameters", "gamma", -0.1),
        ("hyperparameters", "epsilonStart", float("inf")),
        ("hyperparameters", "epsilonEnd", -0.1),
        ("hyperparameters", "epsilonDecay", 0.0),
        ("hyperparameters", "transientFailureRate", 1.1),
        ("trainingSummary", "episodes", 0),
        ("trainingSummary", "episodes", True),
        ("trainingSummary", "successfulEpisodes", -1),
        ("trainingSummary", "successRate", float("nan")),
        ("trainingSummary", "meanReward", float("inf")),
    ],
)
def test_checkpoint_rejects_malformed_metadata(tmp_path, section, field, value):
    path = tmp_path / "policy.json"
    save_checkpoint(path, train(episodes=5, seed=3, transient_failure_rate=0.0))
    document = json.loads(path.read_text())
    document[section][field] = value
    path.write_text(json.dumps(document))

    with pytest.raises(CheckpointError, match="metadata"):
        load_checkpoint(path)


def test_failed_atomic_replace_preserves_existing_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    path.write_text("original checkpoint\n")
    policy = train(episodes=5, seed=3, transient_failure_rate=0.0)

    def fail_replace(_source, _destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("tangying_sim.training.qlearning.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failure"):
        save_checkpoint(path, policy)

    assert path.read_text() == "original checkpoint\n"
    assert not list(tmp_path.glob(".*.tmp"))


def test_invalid_in_memory_policy_cannot_replace_existing_checkpoint(tmp_path):
    path = tmp_path / "policy.json"
    policy = train(episodes=300, seed=11)
    save_checkpoint(path, policy)
    original = path.read_bytes()
    state = next(iter(policy.q_table))
    malformed = replace(
        policy,
        q_table={**policy.q_table, state: (0.0,)},
        hyperparameters={**policy.hyperparameters, "maxSteps": 0},
    )

    with pytest.raises(CheckpointError, match="metadata|Q-table"):
        save_checkpoint(path, malformed)

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_unsafe_terminated_episodes_never_count_as_training_or_evaluation_success():
    unsafe_policy = train(
        episodes=3,
        seed=7,
        transient_failure_rate=0.0,
        env_factory=UnsafeEpisodeEnv,
    )
    report = evaluate(
        unsafe_policy,
        episodes=5,
        seed=17,
        env_factory=UnsafeEpisodeEnv,
    )

    assert unsafe_policy.training_summary["successfulEpisodes"] == 0
    assert unsafe_policy.training_summary["successRate"] == 0.0
    assert report.successful_episodes == 0
    assert report.success_rate == 0.0


@pytest.mark.parametrize("episodes", [0, -1])
def test_train_and_evaluate_reject_empty_episode_counts(episodes):
    with pytest.raises(ValueError, match="episodes"):
        train(episodes=episodes, seed=7)

    policy = train(episodes=1, seed=7, transient_failure_rate=0.0)
    with pytest.raises(ValueError, match="episodes"):
        evaluate(policy, episodes=episodes, seed=7)
