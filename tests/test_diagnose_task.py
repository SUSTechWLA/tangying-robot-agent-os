"""The incident record an AI diagnoses from: facts, a labelled guess, and tests.

An automated post-mortem is only as good as what the system can state. These cases
hold the three properties that make the record trustworthy: the classifier maps
codes to a family (never guessing outside its table), it names the regression
tests that already cover the family, and it refuses to act - a record that quietly
"fixed" something would be worse than no record at all.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"

_spec = importlib.util.spec_from_file_location("diagnose_task", REPO / "scripts" / "diagnose_task.py")
diagnose = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diagnose)


def fixture(name: str) -> dict:
    return diagnose.collect_fixture(FIXTURES / name)


def test_a_verification_failure_is_classified_with_causes_checks_and_tests():
    incident = fixture("incident-placement")
    assert incident["schemaVersion"] == "incident.v1"
    assert incident["task"]["state"] == "RECOVERABLE_FAILURE"
    assert incident["observedCodes"] == ["PLACEMENT_NOT_OBSERVED"]
    diagnosis = incident["diagnosis"]
    assert diagnosis["family"] == "verification_not_observed"
    assert diagnosis["matchedCodes"] == ["PLACEMENT_NOT_OBSERVED"]
    assert len(diagnosis["probableCauses"]) >= 3
    assert any("verification" in check for check in diagnosis["checks"])
    assert diagnosis["coveringTests"], "a family without tests cannot be regression-guarded"
    # The record carries the facts an operator needs to check the guess.
    assert incident["recovery"]["canResume"] is True
    assert incident["evidence"][0]["captureId"] == "robot/head-rgbd-1-2"
    assert incident["stepTimings"][0]["capability"] == "verify_placement"
    assert incident["environment"]["runtime"]["CatalogRevision"] == "catalog-v1"


def test_an_unknown_code_is_not_forced_into_a_family():
    incident = fixture("incident-unclassified")
    diagnosis = incident["diagnosis"]
    assert diagnosis["family"] == "unclassified"
    assert diagnosis["unmatchedCodes"] == ["SOMETHING_NEW_WE_HAVE_NOT_SEEN"]
    assert diagnosis["probableCauses"] == [], "no cause may be invented for an unknown code"
    assert diagnosis["needsHuman"] is True
    assert diagnosis["missingEvidence"], "the record must say what evidence would decide it"


def test_an_uncertain_physical_outcome_is_flagged_for_a_human():
    diagnosis = diagnose.classify(["PHYSICAL_OUTCOME_UNKNOWN"], terminal_state="RECOVERABLE_FAILURE",
                                  reconciliation=True)
    assert diagnosis["family"] == "physical_outcome_unknown"
    assert diagnosis["needsHuman"] is True
    assert "不会重放" in diagnosis["proposedResolution"] or "绝不重放" in diagnosis["proposedResolution"]


def test_every_family_names_real_test_files_that_exist():
    # A pointer to a test that does not exist is worse than no pointer: it sends
    # the next agent looking for evidence that was never there.
    for name, family in diagnose.FAMILIES.items():
        assert family["tests"], name
        for reference in family["tests"]:
            path, _, test_name = reference.partition("::")
            assert (REPO / path).exists(), f"{name}: {path} does not exist"
            if test_name:
                assert test_name in (REPO / path).read_text(), f"{name}: {test_name} missing from {path}"


def test_a_multi_fault_task_reports_the_second_code_instead_of_hiding_it():
    task = json.loads((FIXTURES / "incident-placement" / "task.json").read_text())
    task["events"].append({"sequence": 99, "type": "TOOL_ACTIVITY",
                           "payload": {"toolName": "navigation.navigate", "stepId": "navigate_02",
                                       "activityStatus": "FAILED", "error": "GOAL_NOT_CLEAR"}})
    incident = diagnose.build_incident(task, None, None, None)
    # The first matched family still explains the record...
    assert incident["diagnosis"]["family"] == "verification_not_observed"
    # ...and the other failure is visible rather than swallowed.
    assert "GOAL_NOT_CLEAR" in incident["diagnosis"]["unmatchedCodes"]
    text = diagnose.summarise(incident)
    assert "还有未归类的错误码" in text and "GOAL_NOT_CLEAR" in text


def test_the_record_never_claims_to_have_acted():
    for name in ("incident-placement", "incident-unclassified"):
        incident = fixture(name)
        assert incident["automation"] == {
            "acted": False,
            "reason": "本工具只产出记录与建议，不修改代码、不动机器人。",
        }
        assert incident["nextActions"], "a record without next actions is a dead end"
        assert "coveringTests" in json.dumps(incident["nextActions"], ensure_ascii=False) \
            or "回归测试" in json.dumps(incident["nextActions"], ensure_ascii=False)


def test_a_live_shaped_payload_without_timings_still_produces_a_record():
    # The latency endpoint and the recovery view are optional: a deployment
    # without them must still get a usable incident rather than an exception.
    incident = diagnose.build_incident(
        {"id": "t", "state": "FAILED_SAFE", "currentRevision": 1, "request": "x",
         "events": [{"type": "TOOL_ACTIVITY", "payload": {"error": "NAV_MODEL_COLLISION"}}]},
        None, {}, None, None)
    assert incident["diagnosis"]["family"] == "goal_or_localization_unclear"
    assert incident["stepTimings"] == [] and incident["recovery"]["canResume"] is None


def test_several_states_of_the_same_fault_land_in_one_family():
    for code in ("GOAL_NOT_CLEAR", "LOCALIZATION_NOT_CLEAR", "NAV_ROTATION_LIMIT", "NO_KNOWN_PATH"):
        assert diagnose.classify([code])["family"] == "goal_or_localization_unclear", code
    for code in ("STALE_CAPTURE", "CALIBRATION_CHANGED", "DEPTH_STARVED"):
        assert diagnose.classify([code])["family"] == "mapping_session_fault", code
