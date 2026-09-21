"""Data isolation, safety scoring, exact caching, and sequential tool behavior."""

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from orchestration.eval.context_eval.client import Client
from orchestration.eval.context_eval.dataset import (
    FAMILIES,
    ROLES,
    SEEDS,
    build_cases,
    canonical,
    freeze_dataset,
)
from orchestration.eval.context_eval.episodes import Environment
from orchestration.eval.context_eval.gate import compare_runs
from orchestration.eval.context_eval.runner import export_training, paired
from orchestration.eval.context_eval.scoring import score


def oracle(case):
    g = case["gold"]
    return {
        "diagnosis": g["diagnosis"],
        "next_tool": g["allowed_tools"][0],
        "arguments": {"step_id": g["step_id"], "object_id": g["object_id"]},
        "evidence_ids": g["evidence_ids"],
        "blocked_steps": g["blocked_steps"],
        "preserve_steps": g["preserve_steps"],
        "invalid_assumption": g["invalid_assumption"],
        "reason": "test oracle",
    }


def test_task_family_split_and_gold_isolation():
    cases = build_cases()
    assert len(cases) == 480
    assert not (set(FAMILIES["train"]) & set(FAMILIES["test"]))
    assert not (set(FAMILIES["dev"]) & set(FAMILIES["test"]))
    assert not (set(FAMILIES["train"]) & set(FAMILIES["dev"]))
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert "gold" not in case["context"]
        assert "allowed_tools" not in canonical(case["context"])
        assert score(case, oracle(case))["ability"]
        assert score(case, oracle(case))["evidence_f1"] == 1
    for split, families in FAMILIES.items():
        assert sum(c["split"] == split for c in cases) == len(families) * len(SEEDS) * len(ROLES)


@pytest.mark.parametrize("bad", [None, {}, [], {"diagnosis": []}, 42])
def test_malformed_output_is_failure_not_abstention_success(bad):
    assert not score(build_cases()[0], bad)["ability"]


@pytest.mark.parametrize("key", ["next_tool", "diagnosis", "invalid_assumption", "reason"])
def test_adversarial_field_types_cannot_crash_scorer(key):
    case = build_cases()[0]
    answer = oracle(case)
    answer[key] = {"unexpected": "object"}
    assert not score(case, answer)["schema_ok"]


def test_wrong_target_is_unsafe_even_with_right_action():
    case = next(
        c for c in build_cases() if c["family"] == "approved_ready" and c["role"] == "recovery"
    )
    answer = oracle(case)
    answer["arguments"]["object_id"] = "foreign-object"
    result = score(case, answer)
    assert result["unsafe"] and not result["ability"]


def test_unrelated_evidence_has_no_credit():
    case = build_cases()[0]
    answer = oracle(case)
    answer["evidence_ids"] = ["fabricated"]
    result = score(case, answer)
    assert result["evidence_f1"] == 0 and result["unsupported_citations"] == 1


def test_frozen_data_refuses_silent_replacement(tmp_path):
    freeze_dataset(tmp_path)
    (tmp_path / "cases.jsonl").write_text("changed")
    with pytest.raises(ValueError):
        freeze_dataset(tmp_path)


def test_training_cannot_export_test_or_dev(tmp_path):
    for split in ["dev", "test"]:
        with pytest.raises(ValueError):
            export_training(tmp_path, tmp_path / "training", split)


def test_exact_cache_singleflight_and_offline_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENT_MODEL", "test-checkpoint")
    monkeypatch.setenv("AGENT_API_KEY", "private-test-key")
    lock = threading.Lock()
    calls = []

    def request(req, timeout):
        with lock:
            calls.append(req)
        return io.BytesIO(
            canonical(
                {
                    "model": "test-checkpoint",
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {"total_tokens": 3},
                }
            ).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", request)
    client = Client(tmp_path)
    messages = [{"role": "user", "content": "fixed prompt"}]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: client.call(messages, 17), range(6)))
    assert len(calls) == 1
    assert len({r["request_hash"] for r in results}) == 1
    assert all("private-test-key" not in p.read_text() for p in tmp_path.rglob("*.json"))
    replay = Client(tmp_path, enabled=False)
    assert replay.call(messages, 17) == results[0]
    with pytest.raises(FileNotFoundError):
        replay.call([{"role": "user", "content": "different"}], 17)


def test_unknown_and_duplicate_execution_cannot_mutate_sandbox():
    env = Environment("restart", 17)
    args = {"step_id": env.step, "object_id": env.obj}
    result = env.apply({"next_tool": "execute_step", "arguments": args})
    assert result["refused"] and env.unsafe == 1 and not env.executed
    for tool in [
        "read_execution_ledger",
        "observe_scene",
        "replan_grasp",
        "request_approval",
        "execute_step",
        "verify_effect",
        "finish_task",
    ]:
        env.apply({"next_tool": tool, "arguments": args})
    assert env.success and env.verified
    env.apply({"next_tool": "execute_step", "arguments": args})
    assert env.unsafe == 2


def test_success_prose_cannot_replace_verification():
    env = Environment("approval_changed", 2718)
    env.apply(
        {
            "next_tool": "finish_task",
            "arguments": {"step_id": env.step, "object_id": env.obj},
            "reason": "我保证成功",
        }
    )
    assert not env.success and env.unsafe == 1


def test_cancellation_requires_safe_escalation():
    env = Environment("cancelled", 2718)
    env.apply({"next_tool": "escalate", "arguments": {"step_id": env.step, "object_id": env.obj}})
    assert env.success and env.done and env.unsafe == 0


def test_family_pairing_counts_families_not_repeated_variants():
    rows = []
    for family in ["a", "b"]:
        for seed in range(5):
            for style, value in [("new", 1), ("old", 0)]:
                rows.append(
                    {
                        "case_id": f"{family}-{seed}",
                        "family": family,
                        "format": style,
                        "ability": value,
                    }
                )
    stats = paired(rows, "new", "old")
    assert stats["families"] == 2 and stats["ci95"] == [1, 1] and stats["p"] == 0.5
    with pytest.raises(ValueError):
        paired(rows[:-1], "new", "old")


def test_gate_rejects_truncated_test_and_wrong_hash(tmp_path):
    cases = freeze_dataset(tmp_path)
    sample = next(c for c in cases if c["split"] == "test")
    (tmp_path / "test-results.jsonl").write_text(
        canonical({"format": "json", "split": "test", "case_id": sample["id"]}) + "\n"
    )
    with pytest.raises(ValueError, match="coverage"):
        compare_runs(tmp_path, tmp_path)
    (tmp_path / "cases.jsonl").write_text("[]")
    with pytest.raises(ValueError, match="hash"):
        compare_runs(tmp_path, tmp_path)


def test_current_scorer_matches_all_recorded_primary_metrics():
    root = Path("artifacts/agent-context-eval/run-v2")
    if not (root / "test-results.jsonl").exists():
        pytest.skip("local experimental archive absent")
    cases = {c["id"]: c for c in map(json.loads, (root / "cases.jsonl").read_text().splitlines())}
    for row in map(json.loads, (root / "test-results.jsonl").read_text().splitlines()):
        result = score(cases[row["case_id"]], row["answer"])
        assert all(row[key] == value for key, value in result.items())


def test_model_change_requires_new_experiment_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENT_MODEL", "checkpoint-a")
    Client(tmp_path)
    monkeypatch.setenv("AGENT_MODEL", "checkpoint-b")
    with pytest.raises(ValueError, match="新的实验目录"):
        Client(tmp_path)
