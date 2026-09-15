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

import pytest

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


# ── the handoff from the local agent ────────────────────────────────────────
# The agent records facts when a task ends abnormally (incident.bundle.v1); the
# fault-family knowledge lives here, in one place. These cases pin the handoff and
# the sweep an automated review runs over a directory of failures.

def write_bundle(directory: Path, **overrides) -> Path:
    bundle = {
        "schemaVersion": "incident.bundle.v1",
        "task": {"id": "task-bundle-1", "request": "把红色杯子放进右侧收纳盒", "adapter": "mujoco",
                 "revision": 3, "state": "RECOVERABLE_FAILURE",
                 "terminalCode": "skill verify_placement failed: PLACEMENT_NOT_OBSERVED 未观测到稳定关系",
                 "endedAt": "2026-09-15T14:00:00Z"},
        "recovery": {"canResume": True, "requiresReconciliation": False,
                     "reasonCode": "RESUME_AVAILABLE", "completedStepIds": ["pick", "place"],
                     "uncertainStepIds": []},
        "environment": {"robotId": "robot-1", "softwareVersion": "0.6.0",
                        "catalogRevision": "catalog-v1"},
        "timeline": [{"sequence": 1, "type": "TOOL_ACTIVITY", "stepId": "verify_place",
                      "toolName": "verify_placement", "status": "FAILED",
                      "error": "PLACEMENT_NOT_OBSERVED"}],
        "stepRuns": [{"stepId": "verify_place", "capability": "verify_placement", "status": "STARTED"}],
        "evidence": [{"id": "cap-1", "stepId": "verify_place", "rgbSha256": "aa"}],
        "timing": None, "collectedAt": "2026-09-15T14:00:01Z", "note": "本记录只包含事实",
    }
    bundle.update(overrides)
    path = directory / f"{bundle['task']['id']}.bundle.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False))
    return path


def test_a_bundle_written_by_the_agent_is_classified_here(tmp_path):
    incident = diagnose.collect_bundle(write_bundle(tmp_path))
    assert incident["diagnosis"]["family"] == "verification_not_observed"
    assert incident["task"]["terminalCode"].endswith("未观测到稳定关系")
    # The runner's prose message is parsed for the code the table matches on.
    assert incident["observedCodes"] == ["PLACEMENT_NOT_OBSERVED"]
    assert incident["source"]["kind"] == "bundle"
    assert incident["automation"]["acted"] is False
    # Facts survive the handoff unchanged.
    assert incident["environment"]["catalogRevision"] == "catalog-v1"
    assert incident["recovery"]["canResume"] is True
    assert incident["evidence"][0]["id"] == "cap-1"


def test_only_a_bundle_contract_is_accepted(tmp_path):
    path = write_bundle(tmp_path, schemaVersion="something.else.v1")
    with pytest.raises(ValueError):
        diagnose.collect_bundle(path)
    broken = tmp_path / "broken.bundle.json"
    broken.write_text("{not json")
    with pytest.raises(TypeError):
        diagnose.collect_bundle(broken)


def test_a_sweep_classifies_every_failure_and_reports_what_it_cannot(tmp_path, capsys):
    directory = tmp_path / "bundles"
    directory.mkdir()
    write_bundle(directory)
    # The second failure must be genuinely unknown: overriding only the task
    # fields would leave the first failure's error in the timeline, and the
    # classifier would rightly match on that.
    unknown = write_bundle(directory, task={"id": "task-bundle-2", "state": "FAILED_SAFE",
                                            "terminalCode": "skill pick failed: TOTALLY_NEW_CODE"},
                           timeline=[{"sequence": 1, "type": "TOOL_ACTIVITY", "stepId": "pick",
                                      "status": "FAILED", "error": "TOTALLY_NEW_CODE"}],
                           recovery={"canResume": False, "requiresReconciliation": False,
                                     "completedStepIds": [], "uncertainStepIds": []})
    output = tmp_path / "incidents"
    unresolved = diagnose.sweep(directory, output)
    # One classified, one not - and the unclassified one is counted, not hidden.
    assert unresolved == 1
    assert (output / "task-bundle-1-incident.json").exists()
    assert (output / "task-bundle-2-incident.json").exists()
    printed = capsys.readouterr().out
    assert "verification_not_observed" in printed and "unclassified" in printed
    assert "未归类 1 份" in printed
    assert unknown.exists()


def test_a_sweep_over_an_empty_directory_says_so(tmp_path, capsys):
    assert diagnose.sweep(tmp_path / "nothing", None) == 0
    assert "没有" in capsys.readouterr().out
