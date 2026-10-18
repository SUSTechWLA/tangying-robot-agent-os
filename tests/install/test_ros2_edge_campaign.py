"""Campaign bookkeeping tests; these mocks are never ROS2 experiment evidence."""
import importlib
import json
import sys
import time
from pathlib import Path

import pytest

from scripts import run_ros2_edge_campaign as campaign


def route_summary(kind="ugv"):
    return {"success": True, "state": "SUCCEEDED", "evidenceClass": campaign.EVIDENCE_CLASS,
            "navigationCommands": 11, "independentArrivalChecks": 11, "actualRecordedMoves": 11,
            "actualArrivalCheckGoals": 11, "actualMoveCommandIds": [f"move-{i}" for i in range(11)],
            "spatialPaths": 11, "returnedToObservedStart": True, "duplicateMotionCommands": 0,
            "sourceChangedDuringExperiment": [], "binaryChangedDuringExperiment": False,
            "profileChangedDuringExperiment": False, "taskId": f"task-{kind}"}


def fault_summaries():
    return [{"kind": kind, "success": True, "physicalFixtureMoves": 3, "independentArrivalChecks": 3,
             "replayNoDuplicate": True, "driverLossDisablesMotion": not stop,
             "safeStopProcedureAcknowledged": stop} for kind in campaign.KINDS for stop in (False, True)]


@pytest.fixture
def source_tree(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "go.mod").write_text("module fixture\n")
    binary = tmp_path / "local-agent"
    binary.write_bytes(b"unit-test placeholder; never executed")
    monkeypatch.setattr(campaign, "ROOT", root)

    def run_command(argv, log):
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir()
        log.write("Unit test stub. No DDS or robot execution.\n")
        if "run_ros2_edge_acceptance.py" in argv[1]:
            kind = argv[argv.index("--kind") + 1]
            summary = route_summary(kind)
            summary["taskId"] = f"task-{output.name}"
            campaign.save(output / "summary.json", summary)
        elif "ros2_profiled_smoke.py" in argv[1]:
            campaign.save(output / "summary.json", fault_summaries())
        else:
            (output / "deployment.json").write_text("{}")
        return 0

    monkeypatch.setattr(campaign, "run_command", run_command)
    return root, binary


def test_complete_matrix_freezes_sources_and_verifies_retained_results(tmp_path, source_tree):
    _, binary = source_tree
    output = tmp_path / "run"
    assert campaign.run(output, binary, 2) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["completedRoutes"] == 6
    assert summary["passedFaultCases"] == 12
    assert summary["recordedNavigationCommands"] == summary["independentArrivalChecks"] == 66
    assert not summary["physicalValidation"]
    assert campaign.verify(output)["passed"]


@pytest.mark.parametrize("field,value", [
    ("actualArrivalCheckGoals", 10), ("binaryChangedDuringExperiment", True),
    ("sourceChangedDuringExperiment", ["changed.py"]), ("state", "FAILED"),
    ("actualMoveCommandIds", ["duplicate"] * 11), ("returnedToObservedStart", False),
])
def test_incomplete_or_reused_motion_is_not_accepted(field, value):
    summary = route_summary()
    summary[field] = value
    assert not campaign.valid_route(summary)


def test_fault_aggregate_requires_every_kind_and_each_fault_once():
    rows = fault_summaries()
    assert campaign.fault_results_valid(rows)
    rows[-1] = rows[0]
    assert not campaign.fault_results_valid(rows)


def test_source_edit_stops_campaign_and_preserves_failed_summary(tmp_path, source_tree, monkeypatch):
    root, binary = source_tree
    original = campaign.run_command

    def mutate(argv, log):
        code = original(argv, log)
        if "run_ros2_edge_acceptance.py" in argv[1]:
            (root / "go.mod").write_text("module changed\n")
        return code

    monkeypatch.setattr(campaign, "run_command", mutate)
    output = tmp_path / "run"
    assert campaign.run(output, binary, 3) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert not summary["sourceStable"]
    assert len(summary["trials"]) == 1
    assert not campaign.verify(output)["passed"]


def test_verifier_rejects_missing_or_modified_original_file(tmp_path, source_tree):
    _, binary = source_tree
    output = tmp_path / "run"
    campaign.run(output, binary, 1)
    (output / "round-01-ugv/summary.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        campaign.verify(output)


def test_verifier_recomputes_claim_even_when_manifest_is_updated(tmp_path, source_tree):
    _, binary = source_tree
    output = tmp_path / "run"
    campaign.run(output, binary, 1)
    summary = json.loads((output / "summary.json").read_text())
    summary["completedRoutes"] = 999
    campaign.save(output / "summary.json", summary)
    manifest = json.loads((output / "manifest.json").read_text())
    manifest["files"]["summary.json"] = campaign.digest(output / "summary.json")
    campaign.save(output / "manifest.json", manifest)
    with pytest.raises(ValueError, match="summary disagrees"):
        campaign.verify(output)


def test_existing_evidence_cannot_be_overwritten(tmp_path, source_tree):
    _, binary = source_tree
    output = tmp_path / "run"
    output.mkdir()
    (output / "failed.json").write_text("{}")
    with pytest.raises(FileExistsError):
        campaign.run(output, binary, 1)
    assert (output / "failed.json").read_text() == "{}"


def test_command_timeout_stops_descendant_heartbeat(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    child = ("import time\nfrom pathlib import Path\np=Path(" + repr(str(heartbeat))
             + ")\nwhile True:\n with p.open('a') as f:f.write('x')\n time.sleep(.02)")
    parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);time.sleep(60)"
    with (tmp_path / "command.log").open("w") as log:
        assert campaign.run_command([sys.executable, "-c", parent], log, timeout=.4) == 124
    assert heartbeat.is_file()
    length = len(heartbeat.read_text())
    time.sleep(.1)
    assert len(heartbeat.read_text()) == length


def test_controller_crash_tail_is_retained_as_failure_without_hiding_valid_moves(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(campaign.ROOT / "scripts"))
    acceptance = importlib.import_module("scripts.run_ros2_edge_acceptance")
    ledger = tmp_path / "controller.jsonl"
    original = '{"type":"RECORDED_MOVE","commandId":"a"}\n{}\n[]\n{"type":'
    ledger.write_text(original)
    rows, malformed = acceptance.read_controller_ledger(ledger)
    assert rows == [{"type": "RECORDED_MOVE", "commandId": "a"}]
    assert malformed == [2, 3, 4]
    assert ledger.read_text() == original
