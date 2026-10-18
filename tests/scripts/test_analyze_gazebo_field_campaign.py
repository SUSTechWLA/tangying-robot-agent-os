"""Small retained-evidence fixtures exercise campaign metric trust boundaries."""

import copy
import json

import pytest

from scripts.analyze_gazebo_field_campaign import analyze


def evidence(tmp_path, *, state="SUCCEEDED", success=True):
    campaign, hub = tmp_path / "campaign", tmp_path / "hub"
    (campaign / "ground-a").mkdir(parents=True)
    hub.mkdir()
    result = {"taskId": "task-1", "state": state}
    summary = {"startedAtUnixMs": 900, "endedAtUnixMs": 1200, "success": success,
               "localization": "Gazebo rigid-body measured state", "trials": [
                   {"seed": 3, "request": "去厨房", "tasks": {"ground-a": result}}]}
    desired = [1., 0., .15, 1., 0., 0., 0.]
    folder = campaign / "ground-a"
    (folder / "profile.json").write_text(json.dumps({"robotId": "ground-a", "adapterId": "field-a"}))
    (folder / "goals.json").write_text(json.dumps({"goals": {"kitchen": desired}, "arrivalToleranceM": .12}))
    task = {"id": "task-1", "request": "去厨房", "adapter": "field-a", "state": state,
            "approved": True, "events": [{"type": "TASK_APPROVED"}],
            "plan": {"capabilities": {"calls": [{"tool": "robot.task", "arguments": {"request": "去厨房"},
                    "legacyIntent": {"robotId": "ground-a", "routeRooms": ["kitchen"]}}]}}}
    for name in ("created-3.json", "final-3.json"):
        (folder / name).write_text(json.dumps(task))
    rows, events = [], []
    for sequence, capability in ((1, "navigation.navigate"), (2, "verify_arrival")):
        command = f"task-1/command-{sequence}"
        at = 1 + (sequence - 1) * .1
        own = {"position": [1., 0., .15], "quaternionWXYZ": [1., 0., 0., 0.],
               "linearVelocity": [.01, 0., 0.], "simulationTime": at, "radius": .33}
        peer = {**own, "position": [3., 1., .15], "linearVelocity": [0., 0., 0.]}
        rows.append({"sequence": sequence, "capturedAtUnixMs": 900 + sequence * 100,
                     "simulationTime": at, "states": {"ground-a": own, "ground-b": peer},
                     "commands": {"ground-a": {"commandId": command, "reason": "goal_stop", "diagnostics": {}},
                                  "ground-b": {"commandId": None, "reason": "idle", "diagnostics": {}}},
                     "contactHistory": [], "contactCounts": {"ground-a": sequence}})
        events.extend([{"type": "GOAL", "commandId": command, "capability": capability,
                        "goalPose": desired, "admissionSequence": sequence-1,
                        "admittedAtUnixMs": 900 + sequence * 100 - 10},
                       {"type": "MEASURED_ARRIVAL", "commandId": command,
                        "sourceSequence": sequence, "pose": own["position"]+own["quaternionWXYZ"],
                        "velocity": list(own["linearVelocity"]), "simulationTime": at,
                        "observationId": f"gazebo-ground-a-{sequence}", "distanceM": 0., "orientationErrorRad": 0.}])
    initial = copy.deepcopy(rows[0])
    initial.update(sequence=0, capturedAtUnixMs=950, simulationTime=.9, contactCounts={"ground-a": 0})
    for robot in initial["states"]:
        initial["states"][robot]["simulationTime"] = .9
    rows.insert(0, initial)
    (campaign / "summary.json").write_text(json.dumps(summary))
    (campaign / "ground-a/controller.jsonl").write_text("".join(json.dumps(x)+"\n" for x in events))
    (hub / "physics.jsonl").write_text("".join(json.dumps(x)+"\n" for x in rows))
    return campaign, hub, summary, events


def test_success_receipts_match_retained_source_frames_and_independent_checks(tmp_path):
    campaign, hub, _, _ = evidence(tmp_path)
    result = analyze(campaign, hub, tmp_path / "metrics")
    assert result["taskCampaignSuccess"] and result["successCount"] == result["taskCount"] == 1
    assert result["navigationCommands"] == result["independentArrivalChecks"] == 1
    assert result["arrivalReceiptsMatchedToPhysics"]
    assert result["tasks"][0]["observationIds"] == ["gazebo-ground-a-1", "gazebo-ground-a-2"]
    assert result["physicsCaptures"] == 3
    assert result["tasks"][0]["orderedApprovedRouteMatched"]
    assert not result["safetyMetricsPassed"]  # No world geometry was provided.
    assert result["boundingSphereGapBasis"] == "declared_radius_only_geometry_unverified"
    assert (tmp_path / "metrics/input-manifest.json").is_file()


@pytest.mark.parametrize("mutation", [
    {"sourceSequence": 2},
    {"pose": [9., 0., .15, 1., 0., 0., 0.]},
    {"velocity": [.5, 0., 0.]},
    {"simulationTime": 8.},
    {"observationId": "gazebo-ground-b-1"},
    {"commandId": "task-1/unissued-command"},
])
def test_tampered_arrival_cannot_be_published_as_verified_metrics(tmp_path, mutation):
    campaign, hub, _, events = evidence(tmp_path)
    tampered = copy.deepcopy(events)
    tampered[1].update(mutation)
    (campaign / "ground-a/controller.jsonl").write_text("".join(json.dumps(x)+"\n" for x in tampered))
    output = tmp_path / "metrics"
    with pytest.raises(ValueError):
        analyze(campaign, hub, output)
    assert not (output / "metrics.json").exists()


def test_nonterminal_campaign_is_not_analyzed_as_complete(tmp_path):
    campaign, hub, summary, _ = evidence(tmp_path)
    del summary["endedAtUnixMs"]
    (campaign / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="terminal"):
        analyze(campaign, hub, tmp_path / "metrics")


@pytest.mark.parametrize("reported_success", [False, True])
def test_terminal_failed_tasks_cannot_inherit_forged_campaign_success(tmp_path, reported_success):
    campaign, hub, _, _ = evidence(tmp_path, state="FAILED", success=reported_success)
    try:
        result = analyze(campaign, hub, tmp_path / "metrics")
    except ValueError:
        assert reported_success, "an honest terminal failure must retain its measured metrics"
        return  # Rejecting contradictory summaries is also a valid fail-closed contract.
    assert result["successCount"] == 0
    assert not result["taskCampaignSuccess"], "summary.success must not override terminal FAILED evidence"


@pytest.mark.parametrize("mutation", ["moving", "wrong_distance", "wrong_orientation", "nan_pose",
                                      "same_admission_sequence", "before_admission_time"])
def test_physics_matching_receipts_still_require_goal_and_post_admission_thresholds(tmp_path, mutation):
    campaign, hub, _, events = evidence(tmp_path)
    rows = [json.loads(line) for line in (hub / "physics.jsonl").read_text().splitlines()]
    arrival, goal, measured = events[1], events[0], rows[1]["states"]["ground-a"]
    if mutation == "moving":
        measured["linearVelocity"] = arrival["velocity"] = [.5, 0., 0.]
    elif mutation == "wrong_distance":
        measured["position"][0] = 2.
        arrival["pose"][0] = 2.
        arrival["distanceM"] = 1.  # Even an honest residual cannot bypass the threshold.
    elif mutation == "wrong_orientation":
        import math
        q = [math.cos(.2/2), 0., 0., math.sin(.2/2)]
        measured["quaternionWXYZ"] = q
        arrival["pose"][3:] = q
        arrival["orientationErrorRad"] = .2
    elif mutation == "nan_pose":
        measured["position"][0] = arrival["pose"][0] = float("nan")
    elif mutation == "same_admission_sequence":
        goal["admissionSequence"] = arrival["sourceSequence"]
    else:
        goal["admittedAtUnixMs"] = rows[1]["capturedAtUnixMs"] + 1
    (hub / "physics.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
    (campaign / "ground-a/controller.jsonl").write_text("".join(json.dumps(e)+"\n" for e in events))
    with pytest.raises(ValueError):
        analyze(campaign, hub, tmp_path / "metrics")


@pytest.mark.parametrize("mutation", ["missing_robot", "short_route", "wrong_order", "not_approved",
                                      "false_final_state", "missing_approval_event", "wrong_request"])
def test_success_requires_roster_and_retained_ordered_approved_plan(tmp_path, mutation):
    campaign, hub, _, events = evidence(tmp_path)
    folder = campaign / "ground-a"
    if mutation == "missing_robot":
        missing = campaign / "ground-b"
        missing.mkdir()
        (missing / "profile.json").write_text(json.dumps({"robotId": "ground-b", "adapterId": "field-b"}))
        (missing / "goals.json").write_text((folder / "goals.json").read_text())
    elif mutation == "short_route":
        for name in ("created-3.json", "final-3.json"):
            task = json.loads((folder / name).read_text())
            task["plan"]["capabilities"]["calls"][0]["legacyIntent"]["routeRooms"].append("kitchen")
            (folder / name).write_text(json.dumps(task))
    elif mutation == "wrong_order":
        events = events[2:] + events[:2]
        (folder / "controller.jsonl").write_text("".join(json.dumps(e)+"\n" for e in events))
    else:
        task = json.loads((folder / "final-3.json").read_text())
        if mutation == "not_approved":
            task["approved"] = False
        elif mutation == "missing_approval_event":
            task["events"] = []
        elif mutation == "wrong_request":
            task["request"] = "another task"
        else:
            task["state"] = "FAILED"
        (folder / "final-3.json").write_text(json.dumps(task))
    with pytest.raises(ValueError):
        analyze(campaign, hub, tmp_path / "metrics")


@pytest.mark.parametrize("counter", [None, 0, 10])
def test_no_contact_messages_is_unverified_instead_of_collision_free(tmp_path, counter):
    campaign, hub, _, _ = evidence(tmp_path)
    rows = [json.loads(line) for line in (hub / "physics.jsonl").read_text().splitlines()]
    for row in rows:
        row["contactCounts"] = {} if counter is None else {"ground-a": counter}
    (hub / "physics.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
    result = analyze(campaign, hub, tmp_path / "metrics")
    assert result["taskCampaignSuccess"]
    assert result["noObservedUnexpectedContacts"] and result["nonnegativeSampledGap"]
    assert result["contactCoverageMissingRobots"] == ["ground-a"]
    assert not result["safetyMetricsPassed"]
    assert result["safetyVerificationStatus"] == "not_fully_verified"


@pytest.mark.parametrize("separation", [2., .68])
def test_world_geometry_corrects_undersized_declared_spheres_without_changing_raw_evidence(tmp_path, separation):
    campaign, hub, _, _ = evidence(tmp_path)
    rows = [json.loads(line) for line in (hub / "physics.jsonl").read_text().splitlines()]
    for row in rows:
        row["states"]["ground-b"]["position"] = [1.+separation, 0., .15]
    (hub / "physics.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
    before = (hub / "physics.jsonl").read_bytes()
    world = tmp_path / "world.sdf"
    world.write_text('<sdf version="1.9"><world name="audit">' + ''.join(
        f'<model name="{r}"><pose>9 8 7 0 0 0</pose><link name="base_link">'
        '<collision name="body"><geometry><box><size>.5 .4 .3</size></box></geometry></collision>'
        '</link></model>' for r in ("ground-a", "ground-b")) + '</world></sdf>')
    result = analyze(campaign, hub, tmp_path / "metrics", world)
    assert (hub / "physics.jsonl").read_bytes() == before
    assert result["worldConservativeRadiiM"]["ground-a"] == pytest.approx(2**.5 / 4)
    assert result["minimumDeclaredRadiusGapM"] == pytest.approx(separation - .66)
    assert result["minimumMeasuredBoundingSphereGapM"] == pytest.approx(separation - 2**.5 / 2)
    assert result["minimumWorldGeometryGapM"] == result["minimumMeasuredBoundingSphereGapM"]
    assert result["safetyMetricsPassed"] is (separation == 2.)
    assert str(world) in json.loads((tmp_path / "metrics/input-manifest.json").read_text())
