"""Actual mission-loop software captures and conservative unknown-result recovery."""
import json

import pytest

from scripts import run_spatial_coverage_fixture as fixture


def required_turn_request(tmp_path):
    request = json.loads((fixture.ROOT/"examples/coverage/mixed-air-ground.json").read_text())
    request["robots"] = [request["robots"][0]]
    # The target is immediately behind the initial +X camera. A real Runner
    # dispatch must change the observation orientation before capture can count.
    request["scope"]["targetVoxelIds"] = [(4*7+4)*10+5]
    path = tmp_path/"request.json"
    fixture.write_json(path, request)
    return path


def test_known_map_runner_captures_actual_scope_after_dispatch_and_refuses_overwrite(tmp_path):
    request, output = required_turn_request(tmp_path), tmp_path/"run"
    summary = fixture.run_fixture(request, output)
    assert summary["status"] == "completed" and summary["observedCoverage"] == 1.
    assert summary["commands"] == summary["succeededCommands"] == 1
    assert summary["physicalExecution"] is False and summary["physicalMeasurements"] is False
    assert summary["lastReportReason"] == "coverage_complete"
    assert fixture.verify_fixture(output)["verified"]
    with pytest.raises(FileExistsError):
        fixture.run_fixture(request, output)


def test_unknown_receipt_needs_logged_positive_fixture_stop_before_second_command(tmp_path):
    request, output = required_turn_request(tmp_path), tmp_path/"run"
    summary = fixture.run_fixture(request, output, unknown_step=1)
    assert summary["status"] == "completed"
    assert summary["unknownFaultApplied"] is True
    assert summary["unknownCommands"] == summary["stopAcknowledgements"] == 1
    assert summary["commands"] == 2 and summary["succeededCommands"] == 1
    assert fixture.verify_fixture(output)["verified"]
    events = [json.loads(line) for line in (output/"coordination.jsonl").read_text().splitlines()]
    states = [event["payload"]["state"] for event in events if event["kind"] == "task"]
    assert states == ["assigned", "running", "uncertain", "stopped", "assigned", "running", "succeeded"]


@pytest.mark.parametrize("mutation", ["unknown", "different-occupancy"])
def test_fixture_rejects_unknown_or_inconsistent_world_before_creating_evidence(tmp_path, mutation):
    request = json.loads((fixture.ROOT/"examples/coverage/mixed-air-ground.json").read_text())
    request["robots"][0]["volume"]["cells"][0] = -1 if mutation == "unknown" else 0
    path, output = tmp_path/"request.json", tmp_path/"run"
    fixture.write_json(path, request)
    with pytest.raises(ValueError, match="unknown|identical"):
        fixture.run_fixture(path, output)
    assert not output.exists()


def test_verify_rejects_modified_capture_bytes(tmp_path):
    output = tmp_path/"run"
    fixture.run_fixture(required_turn_request(tmp_path), output)
    path = output/"captures.jsonl"
    path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(ValueError, match="bytes changed"):
        fixture.verify_fixture(output)


def test_zero_remaining_scope_does_not_fabricate_requested_unknown_injection(tmp_path):
    request = fixture.ROOT/"examples/coverage/mixed-air-ground.json"
    summary = fixture.run_fixture(request, tmp_path/"run", unknown_step=1)
    assert summary["status"] == "completed" and summary["commands"] == 0
    assert summary["unknownFaultApplied"] is False and summary["unknownCommands"] == 0


def test_global_work_budget_retains_unobserved_scope_as_partial(tmp_path):
    path = required_turn_request(tmp_path)
    request = json.loads(path.read_text())
    # Target farther away than any initial in-place view. Six viewpoint
    # evaluations allow only the initial orientation set and no reposition.
    request["scope"]["targetVoxelIds"] = [(1*7+1)*10+1]
    request["config"].update(max_total_plan_calls=1, max_total_viewpoint_evaluations=6,
                             max_viewpoints=6)
    fixture.write_json(path, request)
    summary = fixture.run_fixture(path, tmp_path/"run")
    assert summary["status"] == "partial" and summary["observedCoverage"] == 0.
    assert summary["planningCalls"] <= 1 and summary["viewpointEvaluations"] <= 6
    assert fixture.verify_fixture(tmp_path/"run")["verified"]
