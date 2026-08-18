from __future__ import annotations

import hashlib
import json
import math
import os
import random
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tangying_sim.tools import default_tool_registry

from .env import (
    ACTION_SPECS,
    ACTIONS,
    SemanticObservation,
    SemanticToolEnv,
    candidate_action_indices,
)

SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 3
ACTION_SCHEMA_VERSION = 4


class CheckpointError(ValueError):
    """Raised when a learned policy artifact does not match runtime contracts."""


@dataclass(frozen=True)
class SemanticPolicy:
    q_table: dict[str, tuple[float, ...]]
    hyperparameters: dict[str, float | int]
    seed: int
    training_summary: dict[str, float | int]
    tool_catalog_fingerprint: str

    def action(self, observation: SemanticObservation) -> str:
        values = self.q_table.get(_encode_state(observation.state_key()))
        candidates = candidate_action_indices(observation)
        if values is None:
            return ACTIONS[candidates[0]]
        return ACTIONS[_argmax(values, candidates)]


@dataclass(frozen=True)
class EvaluationReport:
    episodes: int
    successful_episodes: int
    success_rate: float
    mean_reward: float
    by_goal_kind: dict[str, dict[str, int | float]]


def catalog_fingerprint() -> str:
    tools = default_tool_registry().capabilities
    if not all(spec.tool_name in tools for spec in ACTION_SPECS):
        raise CheckpointError("training action catalog does not match runtime tool catalog")
    catalog = {"actions": _action_catalog(), "tools": tools}
    encoded = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def train(
    *,
    episodes: int,
    seed: int,
    alpha: float = 0.25,
    gamma: float = 0.95,
    epsilon_start: float = 1.0,
    epsilon_end: float = 0.05,
    epsilon_decay: float = 0.99,
    max_steps: int = 20,
    transient_failure_rate: float = 0.02,
    env_factory: Callable[..., SemanticToolEnv] = SemanticToolEnv,
) -> SemanticPolicy:
    if episodes <= 0:
        raise ValueError("episodes must be positive")
    if not 0.0 < alpha <= 1.0:
        raise ValueError("alpha must be between zero and one")
    if not 0.0 <= gamma <= 1.0:
        raise ValueError("gamma must be between zero and one")
    if not 0.0 <= epsilon_end <= epsilon_start <= 1.0:
        raise ValueError("epsilon values must satisfy 0 <= end <= start <= 1")
    if not 0.0 < epsilon_decay <= 1.0:
        raise ValueError("epsilon_decay must be between zero and one")

    random_source = random.Random(seed)
    env = env_factory(
        seed=seed,
        max_steps=max_steps,
        transient_failure_rate=transient_failure_rate,
    )
    q_table: dict[str, list[float]] = {}
    successes = 0
    rewards: list[float] = []

    for episode in range(episodes):
        observation, _ = env.reset()
        episode_reward = 0.0
        epsilon = max(epsilon_end, epsilon_start * epsilon_decay**episode)
        while True:
            state = _encode_state(observation.state_key())
            values = q_table.setdefault(state, [0.0] * len(ACTIONS))
            candidates = candidate_action_indices(observation)
            if random_source.random() < epsilon:
                action_index = random_source.choice(candidates)
            else:
                action_index = _argmax(values, candidates)
            next_observation, reward, terminated, truncated, info = env.step(ACTIONS[action_index])
            next_state = _encode_state(next_observation.state_key())
            next_values = q_table.setdefault(next_state, [0.0] * len(ACTIONS))
            next_candidates = candidate_action_indices(next_observation)
            next_best = (
                0.0
                if terminated or truncated
                else max(next_values[index] for index in next_candidates)
            )
            values[action_index] += alpha * (reward + gamma * next_best - values[action_index])
            episode_reward += reward
            observation = next_observation
            if terminated or truncated:
                successes += int(bool(info.get("success")))
                rewards.append(episode_reward)
                break

    summary = {
        "episodes": episodes,
        "successfulEpisodes": successes,
        "successRate": successes / episodes,
        "meanReward": sum(rewards) / episodes,
    }
    hyperparameters: dict[str, float | int] = {
        "alpha": alpha,
        "gamma": gamma,
        "epsilonStart": epsilon_start,
        "epsilonEnd": epsilon_end,
        "epsilonDecay": epsilon_decay,
        "maxSteps": max_steps,
        "transientFailureRate": transient_failure_rate,
    }
    return SemanticPolicy(
        q_table={key: tuple(values) for key, values in q_table.items()},
        hyperparameters=hyperparameters,
        seed=seed,
        training_summary=summary,
        tool_catalog_fingerprint=catalog_fingerprint(),
    )


def evaluate(
    policy: SemanticPolicy,
    *,
    episodes: int,
    seed: int,
    max_steps: int | None = None,
    transient_failure_rate: float = 0.0,
    env_factory: Callable[..., SemanticToolEnv] = SemanticToolEnv,
) -> EvaluationReport:
    if episodes <= 0:
        raise ValueError("episodes must be positive")
    if policy.tool_catalog_fingerprint != catalog_fingerprint():
        raise CheckpointError("policy tool catalog does not match runtime tool catalog")
    budget = int(max_steps or policy.hyperparameters.get("maxSteps", 20))
    env = env_factory(
        seed=seed,
        max_steps=budget,
        transient_failure_rate=transient_failure_rate,
    )
    successes = 0
    total_reward = 0.0
    by_kind: dict[str, dict[str, int | float]] = {}
    for _ in range(episodes):
        observation, _ = env.reset()
        kind = observation.goal.kind
        row = by_kind.setdefault(kind, {"episodes": 0, "successfulEpisodes": 0})
        row["episodes"] = int(row["episodes"]) + 1
        while True:
            observation, reward, terminated, truncated, info = env.step(policy.action(observation))
            total_reward += reward
            if terminated or truncated:
                if bool(info.get("success")):
                    successes += 1
                    row["successfulEpisodes"] = int(row["successfulEpisodes"]) + 1
                break
    for row in by_kind.values():
        row["successRate"] = int(row["successfulEpisodes"]) / int(row["episodes"])
    return EvaluationReport(
        episodes=episodes,
        successful_episodes=successes,
        success_rate=successes / episodes,
        mean_reward=total_reward / episodes,
        by_goal_kind=by_kind,
    )


def save_checkpoint(path: str | Path, policy: SemanticPolicy) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "stateSchemaVersion": STATE_SCHEMA_VERSION,
        "actionSchemaVersion": ACTION_SCHEMA_VERSION,
        "toolCatalogFingerprint": policy.tool_catalog_fingerprint,
        "actions": list(ACTIONS),
        "actionCatalog": _action_catalog(),
        "qTable": {key: list(values) for key, values in sorted(policy.q_table.items())},
        "hyperparameters": policy.hyperparameters,
        "seed": policy.seed,
        "trainingSummary": policy.training_summary,
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(
                document,
                handle,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        _fsync_directory(destination.parent)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def load_checkpoint(path: str | Path) -> SemanticPolicy:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CheckpointError(f"cannot read policy checkpoint: {error}") from error
    _require_version(document, "schemaVersion", SCHEMA_VERSION)
    _require_version(document, "stateSchemaVersion", STATE_SCHEMA_VERSION)
    _require_version(document, "actionSchemaVersion", ACTION_SCHEMA_VERSION)
    if document.get("toolCatalogFingerprint") != catalog_fingerprint():
        raise CheckpointError("policy tool catalog fingerprint does not match runtime")
    if document.get("actions") != list(ACTIONS):
        raise CheckpointError("policy action catalog does not match runtime")
    if document.get("actionCatalog") != _action_catalog():
        raise CheckpointError("policy action catalog bindings do not match runtime")

    raw_table = document.get("qTable")
    if not isinstance(raw_table, dict):
        raise CheckpointError("Q-table must be an object")
    q_table: dict[str, tuple[float, ...]] = {}
    for state, raw_values in raw_table.items():
        if not isinstance(state, str) or not isinstance(raw_values, list):
            raise CheckpointError("Q-table rows must map state strings to arrays")
        if len(raw_values) != len(ACTIONS):
            raise CheckpointError("Q-table row has the wrong action count")
        try:
            values = tuple(float(value) for value in raw_values)
        except (TypeError, ValueError) as error:
            raise CheckpointError("Q-table row contains a non-number") from error
        if not all(math.isfinite(value) for value in values):
            raise CheckpointError("Q-table row contains a non-finite value")
        q_table[state] = values

    hyperparameters = document.get("hyperparameters")
    training_summary = document.get("trainingSummary")
    seed = document.get("seed")
    if not isinstance(hyperparameters, dict) or not isinstance(training_summary, dict):
        raise CheckpointError("checkpoint metadata is malformed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise CheckpointError("checkpoint seed is malformed")
    _validate_metadata(hyperparameters, training_summary)
    return SemanticPolicy(
        q_table=q_table,
        hyperparameters=hyperparameters,
        seed=seed,
        training_summary=training_summary,
        tool_catalog_fingerprint=str(document["toolCatalogFingerprint"]),
    )


def _encode_state(state: tuple[str, ...]) -> str:
    return json.dumps(state, separators=(",", ":"))


def _action_catalog() -> list[dict[str, object]]:
    return [
        {
            "actionId": spec.action_id,
            "toolName": spec.tool_name,
            "bindings": spec.parameters(),
        }
        for spec in ACTION_SPECS
    ]


def _argmax(values: tuple[float, ...] | list[float], candidates: tuple[int, ...]) -> int:
    best = max(values[index] for index in candidates)
    return next(index for index in candidates if values[index] == best)


def _require_version(document: object, field: str, expected: int) -> None:
    if not isinstance(document, dict) or document.get(field) != expected:
        raise CheckpointError(f"unsupported {field}")


def _validate_metadata(
    hyperparameters: dict[str, object], training_summary: dict[str, object]
) -> None:
    expected_hyperparameters = {
        "alpha",
        "gamma",
        "epsilonStart",
        "epsilonEnd",
        "epsilonDecay",
        "maxSteps",
        "transientFailureRate",
    }
    if set(hyperparameters) != expected_hyperparameters:
        raise CheckpointError("checkpoint metadata hyperparameters are malformed")
    max_steps = hyperparameters["maxSteps"]
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
        raise CheckpointError("checkpoint metadata maxSteps is malformed")
    alpha = _finite_number(hyperparameters["alpha"], "alpha")
    gamma = _finite_number(hyperparameters["gamma"], "gamma")
    epsilon_start = _finite_number(hyperparameters["epsilonStart"], "epsilonStart")
    epsilon_end = _finite_number(hyperparameters["epsilonEnd"], "epsilonEnd")
    epsilon_decay = _finite_number(hyperparameters["epsilonDecay"], "epsilonDecay")
    transient_rate = _finite_number(hyperparameters["transientFailureRate"], "transientFailureRate")
    if not 0.0 < alpha <= 1.0:
        raise CheckpointError("checkpoint metadata alpha is out of range")
    if not 0.0 <= gamma <= 1.0:
        raise CheckpointError("checkpoint metadata gamma is out of range")
    if not 0.0 <= epsilon_end <= epsilon_start <= 1.0:
        raise CheckpointError("checkpoint metadata epsilon values are out of range")
    if not 0.0 < epsilon_decay <= 1.0:
        raise CheckpointError("checkpoint metadata epsilonDecay is out of range")
    if not 0.0 <= transient_rate <= 1.0:
        raise CheckpointError("checkpoint metadata transientFailureRate is out of range")

    expected_summary = {"episodes", "successfulEpisodes", "successRate", "meanReward"}
    if set(training_summary) != expected_summary:
        raise CheckpointError("checkpoint metadata training summary is malformed")
    episodes = training_summary["episodes"]
    successes = training_summary["successfulEpisodes"]
    if not isinstance(episodes, int) or isinstance(episodes, bool) or episodes <= 0:
        raise CheckpointError("checkpoint metadata episodes is malformed")
    if (
        not isinstance(successes, int)
        or isinstance(successes, bool)
        or not 0 <= successes <= episodes
    ):
        raise CheckpointError("checkpoint metadata successfulEpisodes is malformed")
    success_rate = _finite_number(training_summary["successRate"], "successRate")
    _finite_number(training_summary["meanReward"], "meanReward")
    if not 0.0 <= success_rate <= 1.0:
        raise CheckpointError("checkpoint metadata successRate is out of range")
    if not math.isclose(success_rate, successes / episodes, abs_tol=1e-12):
        raise CheckpointError("checkpoint metadata successRate is inconsistent")


def _finite_number(value: object, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CheckpointError(f"checkpoint metadata {field} is not numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise CheckpointError(f"checkpoint metadata {field} is not finite")
    return parsed


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
