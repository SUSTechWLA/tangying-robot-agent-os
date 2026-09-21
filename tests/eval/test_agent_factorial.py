"""检测反事实泄漏、独立因子变化、不可区分性和统计符号错误。"""

import json
from pathlib import Path

import jsonschema
import pytest

from orchestration.eval.context_eval.factorial_analysis import effect
from orchestration.eval.context_eval.factorial_cases import (
    FACTORS,
    SEEDS,
    build_cases,
    intervention,
    make_pair,
    score,
)
from orchestration.eval.context_eval.stage_report import MARKER, replace_stage_section


def test_regenerating_second_report_keeps_third_experiment():
    original = "# Report\n\n## 11. Earlier\nkeep" + MARKER + "\nold\n\n## 13. Newer\nkeep third\n"
    replaced = replace_stage_section(original, MARKER + "\nupdated\n")
    assert "old" not in replaced and "updated" in replaced
    assert "## 11. Earlier\nkeep" in replaced and "## 13. Newer\nkeep third" in replaced


def test_all_counterfactual_pairs_have_distinct_answers_but_identical_erasure():
    cases = build_cases()
    assert len(cases) == 480
    for a, b in zip(cases[::2], cases[1::2], strict=True):
        assert a["pair_id"] == b["pair_id"] and a["gold"] != b["gold"]
        assert intervention(a, {"complete": False}) == intervention(b, {"complete": False})
        assert a["id"] not in json.dumps(a["packet"])
        assert a["pair_id"] not in json.dumps(a["packet"])


def test_schema_accepts_explicit_unknown_in_all_critical_fields():
    schema = json.loads(Path("core/agentcontext/decision-context.schema.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    for case in build_cases():
        validator.validate(case["packet"])
        validator.validate(intervention(case, {"complete": False}))


def test_erased_information_requires_abstention_not_lucky_guess():
    c = make_pair("recovery", "test", 1, SEEDS[0])[0]
    s = {"complete": False}
    result = score(c, s, {"abstain": False, "answer": c["gold"], "missing": []})
    assert (
        result["decision_correct"]
        and not result["supported_correct"]
        and result["unsupported_assertion"]
    )
    result = score(c, s, {"abstain": True, "answer": None, "missing": [c["witness"]]})
    assert result["supported_correct"] and not result["decision_correct"]


def test_hand_checked_witness_decisions():
    def pair(stage, family):
        return make_pair(stage, "test", family, SEEDS[0])

    a, b = pair("recovery", 4)
    assert a["gold"]["tool"] == "execute_step" and b["gold"]["tool"] == "escalate"
    a, b = pair("recovery", 1)
    assert a["gold"]["tool"] == "observe_scene" and b["gold"]["tool"] == "execute_step"
    a, b = pair("ops", 1)
    assert a["gold"]["diagnosis"] == "HEALTHY" and b["gold"]["diagnosis"] == "UNKNOWN_EXECUTION"
    a, b = pair("verification", 1)
    assert a["gold"]["verdict"] == "VERIFIED" and b["gold"]["verdict"] == "UNKNOWN"
    a, b = pair("tool_result", 1)
    assert a["gold"]["retry_allowed"] is True and b["gold"]["retry_allowed"] is False
    a, b = pair("goal", 3)
    assert (
        a["gold"]["clarification_required"] is False and b["gold"]["clarification_required"] is True
    )
    a, _ = pair("planning", 1)
    # The benchmark asks for direct dependencies, not transitive consistency.
    ready_step = a["packet"]["steps"][4]["id"]
    assert ready_step in a["gold"]["ready_steps"]
    a, b = pair("handoff", 1)
    step = a["packet"]["goal"]["current_step_id"]
    assert step in a["gold"]["unresolved_step_ids"] and step in b["gold"]["pending_steps"]


def test_full_producer_payload_contract_covers_every_catalog_field():
    schema = json.loads(Path("core/agentcontext/decision-payload.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    catalog = json.loads(Path("core/agentcontext/decision-field-catalog.json").read_text())
    names = {
        "goal": "goal",
        "steps[]": "step",
        "attempts[]": "attempt",
        "tools[]": "tool",
        "handoff": "handoff",
    }
    for kind in ["tool_return", "verification", "observation", "guard", "hypothesis"]:
        names[f"records[].payload({kind})"] = kind
    for group, fields in catalog["source_obligations"].items():
        definition = schema["$defs"][names[group]]
        assert set(fields) == set(definition["properties"]) == set(definition["required"])
        jsonschema.Draft202012Validator(definition).validate(dict.fromkeys(fields))


def test_factorial_contrasts_recover_known_effect_and_interaction():
    rows = []
    for family in ["a", "b", "c"]:
        for spec in FACTORS:
            x = 1 if spec["syntax"] == "cnl" else -1
            a = 1 if spec["annotation"] else -1
            y = 0.5 + 0.1 * x + 0.05 * a + 0.03 * x * a
            rows.append(
                {"case_id": family, "family": family, "factors": spec, "decision_correct": y}
            )
    assert effect(rows, ["syntax"])["estimate"] == pytest.approx(0.2)
    assert effect(rows, ["annotation"])["estimate"] == pytest.approx(0.1)
    assert effect(rows, ["syntax", "annotation"])["estimate"] == pytest.approx(0.12)
    assert effect(rows, ["order"])["estimate"] == pytest.approx(0)


@pytest.mark.parametrize(
    "bad", [None, [], {"abstain": False, "answer": {"tool": {}}, "missing": []}]
)
def test_invalid_responses_fail_without_crashing(bad):
    c = make_pair("recovery", "test", 1, SEEDS[0])[0]
    assert not score(c, {"complete": True}, bad)["decision_correct"]
