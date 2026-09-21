"""Evaluation validity tests, not additional agent performance measurements."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from orchestration.eval.system_eval.adapters import coverage, import_context, import_gvf
from orchestration.eval.system_eval.engine import (
    compare,
    digest,
    file_hash,
    matched,
    reliability,
    scorecard,
    validate,
    write,
)


def fixture_run(n=128):
    units = [
        {
            "unit_id": str(i),
            "case_id": str(i),
            "family_id": str(i),
            "cluster_id": str(i),
            "repeat_id": 0,
            "draw_id": str(i),
            "fresh_draw": True,
            "execution_status": "completed",
            "dimensions": {
                "stage": "goal",
                "module": "fixture",
                "tool": "none",
                "tool_family": "none",
                "fault": "none",
                "backend": "fixture",
                "tier": "decision",
                "truth": "fixture",
                "risk": "proposal",
            },
            "measurements": {"decision_exact": 1, "unsafe_proposal": 0},
            "not_applicable": [],
            "evidence_refs": ["fixture://" + str(i)],
            "failure": None,
        }
        for i in range(n)
    ]
    return {
        "schema_version": "system-eval-run.v1",
        "run_id": "fixture",
        "benchmark": {
            "id": "fixture",
            "version": "1",
            "dataset_sha256": "a" * 64,
            "grader_sha256": "b" * 64,
            "split": "confirmation",
            "primary_metric": "decision_exact",
            "risk_metric": "unsafe_proposal",
        },
        "components": {"policy": "baseline", "model": "fixed"},
        "controls": {
            "environment_id": "fixture",
            "budget": {"tokens": 100},
            "semantic_contract_id": "1",
            "source_mode": "new_execution",
        },
        "provenance": {
            "sources": [{"path": "fixture", "sha256": "c" * 64}],
            "claim_scope": "test fixture only",
        },
        "planned_units": [r["unit_id"] for r in units],
        "units": units,
    }


def profile():
    return {
        "version": "system-gate.v1",
        "purpose": "unit test thresholds only",
        "alpha": 0.05,
        "quality_floor": 0.5,
        "noninferiority_margin": 0.5,
        "risk_cluster_ceiling": 0.5,
        "min_clusters": 2,
        "critical_slices": [],
    }


def candidate(run):
    result = deepcopy(run)
    result["components"]["policy"] = "candidate"
    return result


def test_strict_planned_units_and_duplicate_repeats():
    r = fixture_run(2)
    r["units"].pop()
    with pytest.raises(ValueError, match="planned units"):
        validate(r)
    r = fixture_run(2)
    r["units"][1]["case_id"] = "0"
    with pytest.raises(ValueError, match="duplicate case repeat"):
        validate(r)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.5, -1, 2])
def test_invalid_binary_measurement(bad):
    r = fixture_run(1)
    r["units"][0]["measurements"]["decision_exact"] = bad
    with pytest.raises(ValueError):
        validate(r)


def test_timeouts_are_quality_failures_but_risk_unknown():
    r = fixture_run(2)
    for row in r["units"]:
        row["execution_status"] = "timeout"
        row["measurements"]["latency_ms"] = 0
    card = scorecard(r)
    assert card["overall"]["decision_exact"]["micro_mean"] == 0
    assert card["overall"]["unsafe_proposal"]["measured"] == 0
    assert card["overall"]["latency_ms"]["measured"] == 0
    assert compare(r, candidate(r), ["policy"], profile())["status"] == "FAIL"
    r["units"][0]["measurements"]["unsafe_proposal"] = 1
    assert scorecard(r)["overall"]["unsafe_proposal"]["micro_mean"] == 1


def test_inapplicable_differs_from_missing():
    r = fixture_run(3)
    r["units"][0]["not_applicable"] = ["unsafe_proposal"]
    r["units"][0]["measurements"]["unsafe_proposal"] = None
    r["units"][1]["measurements"]["unsafe_proposal"] = None
    s = scorecard(r)["overall"]["unsafe_proposal"]
    assert (s["planned"], s["eligible"], s["not_applicable"], s["measured"], s["missing"]) == (
        3,
        2,
        1,
        1,
        1,
    )
    assert s["coverage"] == 0.5


@pytest.mark.parametrize("field", ["dataset_sha256", "grader_sha256", "split"])
def test_benchmark_mismatch_rejected(field):
    a = fixture_run(2)
    b = candidate(a)
    b["benchmark"][field] = "historical" if field == "split" else "d" * 64
    with pytest.raises(ValueError, match="incomparable benchmark"):
        matched(a, b, ["policy"])


def test_budget_hidden_change_and_pair_cluster_rejected():
    a = fixture_run(2)
    b = candidate(a)
    b["controls"]["budget"]["tokens"] = 200
    with pytest.raises(ValueError, match="incomparable controls"):
        matched(a, b, ["policy"])
    b = candidate(a)
    b["components"]["model"] = "other"
    with pytest.raises(ValueError, match="actual changed"):
        matched(a, b, ["policy"])
    b = candidate(a)
    b["units"][0]["cluster_id"] = "different"
    with pytest.raises(ValueError, match="paired unit identity"):
        matched(a, b, ["policy"])


def test_perfect_small_sample_is_not_proof_but_large_can_meet_loose_fixture_gate():
    a = fixture_run(2)
    assert compare(a, candidate(a), ["policy"], profile())["status"] == "INCONCLUSIVE"
    a = fixture_run()
    assert compare(a, candidate(a), ["policy"], profile())["status"] == "PASS"
    a["benchmark"]["split"] = "historical"
    assert compare(a, candidate(a), ["policy"], profile())["status"] == "INCONCLUSIVE"


def test_missing_slice_or_risk_prevents_pass_and_observed_risk_fails():
    a = fixture_run()
    b = candidate(a)
    b["units"][0]["measurements"]["unsafe_proposal"] = None
    assert compare(a, b, ["policy"], profile())["status"] == "INCONCLUSIVE"
    b["units"][0]["measurements"]["unsafe_proposal"] = 1
    assert compare(a, b, ["policy"], profile())["status"] == "FAIL"
    p = profile()
    p["critical_slices"] = [{"stage": "recovery"}]
    assert compare(a, candidate(a), ["policy"], p)["status"] == "INCONCLUSIVE"


def test_low_baseline_does_not_excuse_low_candidate():
    a = fixture_run()
    for row in a["units"]:
        row["measurements"]["decision_exact"] = 0
    assert compare(a, candidate(a), ["policy"], profile())["status"] == "FAIL"


def test_all_k_not_pass_at_k_and_archive_is_not_fresh():
    r = fixture_run(4)
    for i, row in enumerate(r["units"]):
        row["case_id"] = "same"
        row["repeat_id"] = i
    r["units"][0]["measurements"]["decision_exact"] = 0
    assert reliability(r, 2)["all_k_success"] == 0.5
    for row in r["units"]:
        row["fresh_draw"] = False
    assert reliability(r, 2)["all_k_success"] is None


def test_immutable_outputs(tmp_path):
    path = tmp_path / "run.json"
    write(path, {"a": 1}, True)
    write(path, {"a": 1}, True)
    with pytest.raises(ValueError, match="immutable artifact"):
        write(path, {"a": 2}, True)


def test_context_adapter_checks_registered_dataset_and_never_invents_draws(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({"id": "x", "split": "test", "family": "goal/f1", "stage": "goal"}) + "\n"
    )
    (tmp_path / "protocol.json").write_text(
        json.dumps(
            {
                "version": "fixture",
                "dataset_sha256": file_hash(cases),
                "counts": {"test": 1},
                "source_sha256": {"orchestration/eval/context_eval/factorial_cases.py": "a" * 64},
                "renderer_sha256": "b" * 64,
                "model_calls": {"max_tokens": 512},
            }
        )
    )
    row = {
        "case_id": "x",
        "model": "fixture",
        "format": "json",
        "family": "goal/f1",
        "stage": "goal",
        "protocol_error": None,
        "decision_correct": True,
        "supported_correct": True,
        "schema_ok": True,
        "unsafe": False,
        "unsupported_assertion": False,
        "latency_s": 1,
        "total_tokens": 10,
        "request_hash": "c" * 64,
        "prompt_hash": "d" * 64,
        "score_version": "fixture",
    }
    (tmp_path / "test-results.jsonl").write_text(json.dumps(row) + "\n")
    r = import_context(tmp_path, "fixture", "json")
    assert r["benchmark"]["split"] == "historical"
    assert not r["units"][0]["fresh_draw"]
    cases.write_text(cases.read_text() + "\n")
    with pytest.raises(ValueError, match="registered hash"):
        import_context(tmp_path, "fixture", "json")


def test_gvf_is_verifier_evidence_not_task_success_or_public_tool_coverage(tmp_path):
    row = {
        "seed": 1,
        "task_id": "t",
        "step": 0,
        "kind": "manipulation.pick",
        "fault": "none",
        "truth": "VERIFIED",
        "action_s": 1,
        "original_report": {
            "verdict": "VERIFIED",
            "verifier_version": "fixture",
            "evidence_refs": [{"uri": "fixture://evidence"}],
        },
    }
    path = tmp_path / "trials.jsonl"
    path.write_text(json.dumps(row) + "\n")
    r = import_gvf(path)
    assert "task_verified_success" not in r["units"][0]["measurements"]
    assert r["units"][0]["not_applicable"] == ["false_verified", "unknown_preserved"]
    root = Path(__file__).resolve().parents[2]
    c = coverage([r], root / "tools.json")
    assert c["exact_tools"]["pick_object"] == []
    assert c["other_tool_identifiers"] == ["manipulation.pick"]
    assert c["capabilities"]["verification"]["observed_tiers"] == ["component"]
    assert digest(r) == digest(deepcopy(r))
