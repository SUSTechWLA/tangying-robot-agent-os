"""Stage-specific labels, independent split identities, and routing statistics."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from orchestration.eval.context_eval.stage_cases import STAGES, build_cases, messages, score
from orchestration.eval.context_eval.stage_runner import freeze, statistics, winner


def test_stage_contracts_and_oracles():
    cases = build_cases()
    assert len(cases) == 640
    assert len({c["id"] for c in cases}) == 640
    assert {c["stage"] for c in cases} == set(STAGES)
    families = {
        split: {c["family"] for c in cases if c["split"] == split}
        for split in ["train", "dev", "test"]
    }
    assert not families["train"] & families["test"]
    assert not families["dev"] & families["test"]
    for c in cases:
        assert score(c, c["gold"])["ability"]
        assert "gold" not in json.dumps(messages(c, "rendered public input"))
        assert set(c["context"]).isdisjoint({"gold", "allowed_tools"})


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_rejects_missing_output(stage):
    c = next(c for c in build_cases() if c["stage"] == stage)
    assert not score(c, {})["ability"]
    assert not score(c, None)["schema_ok"]


def test_semantic_safety_is_scored_separately_from_format():
    cases = build_cases()
    c = next(c for c in cases if c["stage"] == "handoff" and c["gold"]["unresolved_step_ids"])
    answer = copy.deepcopy(c["gold"])
    answer["next_tool"] = "resume_planner"
    assert score(c, answer)["unsafe"]
    c = next(c for c in cases if c["stage"] == "recovery" and c["gold"]["tool"] != "execute_step")
    answer = copy.deepcopy(c["gold"])
    answer["tool"] = "execute_step"
    assert score(c, answer)["unsafe"]
    c = next(c for c in cases if c["stage"] == "planning" and c["gold"]["blocked_steps"])
    answer = copy.deepcopy(c["gold"])
    answer["ready_steps"].append(c["gold"]["blocked_steps"][0])
    assert score(c, answer)["unsafe"]


def test_different_stages_have_different_score_schemas():
    cases = build_cases()
    schemas = {
        stage: frozenset(next(c["gold"] for c in cases if c["stage"] == stage)) for stage in STAGES
    }
    assert len(set(schemas.values())) == len(STAGES)


def test_duplicate_evidence_id_is_not_extra_credit():
    c = next(c for c in build_cases() if c["stage"] == "verification")
    answer = copy.deepcopy(c["gold"])
    answer["excluded_records"] += answer["excluded_records"][:1]
    assert not score(c, answer)["schema_ok"]


def test_development_selection_prioritizes_ability_then_cost():
    rows = []
    for style, ability, tokens in [
        ("json", True, 1000),
        ("nl_sections", True, 990),
        ("nl_decision", False, 700),
        ("hybrid", True, 1100),
        ("annotated", True, 1200),
    ]:
        for seed in [104729, 130363, 155921, 196613, 262147]:
            rows.append(
                {
                    "format": style,
                    "seed": seed,
                    "ability": ability,
                    "schema_ok": True,
                    "unsafe": False,
                    "field_accuracy": 1.0,
                    "total_tokens": tokens,
                    "latency_s": 1.0,
                }
            )
    assert winner(rows) == "nl_sections"
    assert statistics(rows)["json"]["metrics"]["ability"]["mean"] == 1.0


def test_archived_scores_recompute_without_model():
    root = Path("artifacts/agent-context-eval/stage-routing-v1/optimized-run")
    path = root / "test-results.jsonl"
    if not path.exists():
        pytest.skip("held-out run not present")
    cases = {c["id"]: c for c in map(json.loads, (root / "cases.jsonl").read_text().splitlines())}
    for row in map(json.loads, path.read_text().splitlines()):
        assert all(row[k] == v for k, v in score(cases[row["case_id"]], row["answer"]).items())


def test_invalid_tool_object_does_not_crash_evaluator():
    c = next(c for c in build_cases() if c["stage"] == "recovery")
    answer = copy.deepcopy(c["gold"])
    answer["tool"] = {"name": "execute_step"}
    assert not score(c, answer)["schema_ok"]


@pytest.mark.parametrize("tampered", [False, True])
def test_reopening_archive_never_rewrites_frozen_inputs(tmp_path, tampered):
    (tmp_path / "source_snapshot").mkdir()
    files = {"cases.jsonl": "frozen cases", "context-render": "frozen binary"}
    for name, contents in files.items():
        (tmp_path / name).write_text(contents)
    digest = lambda name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    protocol = {
        "dataset_sha256": digest("cases.jsonl"),
        "renderer_binary_sha256": digest("context-render"),
        "source_sha256": {},
    }
    (tmp_path / "protocol.json").write_text(json.dumps(protocol))
    (tmp_path / "model-config.json").write_text('{"base_url":"https://example.invalid"}')
    if tampered:
        (tmp_path / "cases.jsonl").write_text("unexpected edit")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    if tampered:
        with pytest.raises(ValueError, match="dataset digest differs"):
            freeze(tmp_path)
    else:
        assert freeze(tmp_path) == protocol
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
