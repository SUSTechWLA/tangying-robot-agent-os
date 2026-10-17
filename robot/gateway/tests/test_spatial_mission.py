"""Evidence, lease and real closed-loop port contracts, without physical motion."""
import copy
import dataclasses
import hashlib
import json
import math

import pytest
from tangying_robot_gateway.spatial_mission import (
    CoverageLedger,
    DriverResult,
    EventJournal,
    MissionError,
    SpatialMissionRunner,
    SpatialSnapshot,
    TaskCoordinator,
    geometry_id,
)
from tangying_robot_gateway.spatial_navigation import SpatialPathPlanner


def volume(robot="robot-a"):
    return {"schemaVersion": "navigation.volume.v1", "robotId": robot, "mapId": "room",
            "mapRevision": "snapshot-1", "frameId": "world", "worldFrameRevision": "world-1",
            "calibrationRevision": "cal-1", "resolution": 1., "origin": [0., 0., 0.],
            "shape": [7, 7, 7], "cells": [0] * 343}


def pose(x=2.5, y=2.5, z=2.5):
    return [x, y, z, 1., 0., 0., 0.]


POLICY = {"mode": "volume3d", "bodyHalfExtents": [.1, .1, .1], "margin": .05,
          "maxExpanded": 100, "maxWaypoints": 100}


def candidate(robot="robot-a", target="goal-a", destination=None):
    goal = destination or pose(3.5)
    path = SpatialPathPlanner(volume(robot), POLICY).plan(pose(), goal)
    return {"targetId": target, "pose": goal, "path": path, "score": 3.,
            "visibleUnknownVoxelIds": [1, 2], "informationGain": 2,
            "pathLengthM": sum(math.dist(a[:3], b[:3]) for a, b in zip(path["waypoints"], path["waypoints"][1:]))}


def observe(ledger, **overrides):
    arguments = {"robot_id": "robot-a", "source_id": "camera", "sequence": 1,
                 "observed_at_ms": 1000, "now_ms": 1000, "world_revision": "world-1",
                 "classified": {1: 0, 2: 100}, "evidence_id": "frame-1"}
    return ledger.observe(**{**arguments, **overrides})


def test_coverage_replay_is_durable_and_duplicate_does_not_refresh_receipt(tmp_path):
    path = tmp_path / "coverage.jsonl"
    ledger = CoverageLedger("mission", "world-1", [1, 2, 3], journal_path=path)
    assert observe(ledger)["newlyObserved"] == 2
    before = ledger.summary()
    assert observe(ledger, now_ms=2000)["duplicate"]
    assert ledger.summary() == before
    ledger.journal.close()
    restored = CoverageLedger("mission", "world-1", [1, 2, 3], journal_path=path)
    assert restored.summary() == before
    assert restored.observed_ids == {1, 2}
    restored.journal.close()
    with pytest.raises(MissionError, match="scope"):
        CoverageLedger("different", "world-1", [1, 2, 3], journal_path=path)


@pytest.mark.parametrize("arguments,code", [
    ({"world_revision": "unregistered-world"}, "WORLD_MISMATCH"),
    ({"geometry_id": "other-grid"}, "WORLD_MISMATCH"),
    ({"now_ms": 7000}, "STALE_OBSERVATION"),
    ({"observed_at_ms": 3000}, "STALE_OBSERVATION"),
])
def test_invalid_observation_never_mutates_coverage(arguments, code):
    ledger = CoverageLedger("mission", "world-1")
    before = ledger.summary()
    with pytest.raises(MissionError) as failure:
        observe(ledger, **arguments)
    assert failure.value.code == code
    assert ledger.summary() == before


@pytest.mark.parametrize("classified", [{True: 0}, {250_000: 0}, {-1: 100}, {1: -1}, {1: True}, {1: float("nan")}])
def test_measurements_require_bounded_integer_addresses_and_real_classifications(classified):
    with pytest.raises(ValueError):
        observe(CoverageLedger("mission", "world-1"), classified=classified)


def test_conflicts_are_retained_and_not_counted_as_covered():
    ledger = CoverageLedger("mission", "world-1", [1, 2, 3])
    observe(ledger)
    report = observe(ledger, robot_id="robot-b", evidence_id="frame-b", classified={1: 100, 3: 0})
    assert report == {"accepted": True, "duplicate": False, "newlyObserved": 1,
                      "coverageDelta": 0, "repeated": 1, "conflicts": 1}
    assert ledger.observed_ids == {2, 3}
    assert ledger.summary()["coverage"] == 2 / 3
    assert ledger.summary()["conflictingVoxels"] == 1


def test_unknown_coverage_denominator_is_null_not_invented():
    ledger = CoverageLedger("mission", "world-1")
    observe(ledger)
    assert ledger.summary()["coverage"] is None
    assert ledger.summary()["targetVoxels"] is None


@pytest.mark.parametrize("updates,code", [
    ({"classified": {1: 100}}, "OBSERVATION_REPLAY"),
    ({"sequence": 2, "observed_at_ms": 999, "evidence_id": "frame-2"}, "OBSERVATION_REPLAY"),
    ({"sequence": 2}, "EVIDENCE_REPLAY"),
])
def test_source_replay_and_relabelled_evidence_are_rejected(updates, code):
    ledger = CoverageLedger("mission", "world-1")
    observe(ledger)
    with pytest.raises(MissionError) as failure:
        observe(ledger, **updates)
    assert failure.value.code == code


def test_corrupted_hash_chain_cannot_be_resumed(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path)
    journal.append("test", {"x": 1})
    journal.close()
    document = json.loads(path.read_text())
    document["payload"]["x"] = 2
    path.write_text(json.dumps(document) + "\n")
    with pytest.raises(MissionError, match="hash"):
        EventJournal(path)


def test_target_coordination_is_independent_of_execution_fence():
    coordinator = TaskCoordinator("mission", "world-1")
    a = coordinator.assign("robot-a", [candidate()], 1000)
    assert coordinator.assign("robot-b", [candidate("robot-b")], 1000) is None
    independent = TaskCoordinator("ablation", "world-1", use_coordination=False)
    first = independent.assign("robot-a", [candidate()], 1000)
    second = independent.assign("robot-b", [candidate("robot-b")], 1000)
    independent.start(first["taskId"], "robot-a", 1001)
    with pytest.raises(MissionError, match="execution"):
        independent.start(second["taskId"], "robot-b", 1001)
    assert a["state"] == "assigned"


def test_leases_expire_differently_before_and_after_dispatch(tmp_path):
    path = tmp_path / "tasks.jsonl"
    coordinator = TaskCoordinator("mission", "world-1", journal_path=path)
    unstarted = coordinator.assign("robot-a", [candidate()], 1000, 100)
    coordinator.expire(1100)
    assert coordinator.tasks[-1]["state"] == "expired"
    started = coordinator.assign("robot-a", [candidate()], 1101, 100)
    coordinator.start(started["taskId"], "robot-a", 1102)
    coordinator.journal.close()
    restored = TaskCoordinator("mission", "world-1", journal_path=path)
    restored.expire(1201)
    assert restored.tasks[-1]["state"] == "uncertain"
    assert restored.assign("robot-a", [candidate()], 1202) is None
    other = restored.assign("robot-b", [candidate("robot-b", "other")], 1202)
    with pytest.raises(MissionError) as failure:
        restored.start(other["taskId"], "robot-b", 1203)
    assert failure.value.code == "EXECUTION_FENCED"
    with pytest.raises(MissionError):
        restored.acknowledge_stop(started["taskId"], "robot-b", 1204)
    restored.acknowledge_stop(started["taskId"], "robot-a", 1204)
    restored.start(other["taskId"], "robot-b", 1205)
    assert restored.tasks[-1]["state"] == "running"
    assert unstarted["taskId"] != started["taskId"]


def test_late_success_remains_uncertain_and_cannot_dispatch_twice():
    coordinator = TaskCoordinator("mission", "world-1")
    task = coordinator.assign("robot-a", [candidate()], 1000, 100)
    coordinator.start(task["taskId"], "robot-a", 1001)
    with pytest.raises(MissionError):
        coordinator.start(task["taskId"], "robot-a", 1002)
    result = coordinator.finish(task["taskId"], "robot-a", "succeeded", 1100)
    assert result["state"] == "uncertain"


@pytest.mark.parametrize("field,value", [("score", float("nan")), ("pathLengthM", -1.),
                                          ("pose", pose(4.5))])
def test_plugins_cannot_bypass_proposal_receipt_validation(field, value):
    document = candidate()
    document[field] = value
    with pytest.raises(ValueError):
        TaskCoordinator("mission", "world-1").assign("robot-a", [document], 1000)


class Port:
    def __init__(self):
        self.now, self.sequence = 1000, 1
        self.position = pose()
        self.cancelled = False
        self.calls, self.stops, self.reports = [], [], []
        self.outcome, self.stop_ack = "succeeded", False
        self.duplicate_pose = self.bad_receipt = False
        self.document = volume()

    def snapshot(self, _robot_id):
        return SpatialSnapshot(copy.deepcopy(self.document), list(self.position), self.sequence,
                               self.now, "localizer")

    def capture(self, _robot_id):
        return {"sourceId": "camera", "sequence": self.sequence, "observedAtMs": self.now,
                "worldFrameRevision": "world-1", "geometryId": geometry_id(self.document),
                "classified": {10: 0}, "evidenceId": f"camera-{self.sequence}"}

    def check_cancel(self):
        if self.cancelled:
            raise MissionError("CANCELLED", "cancelled")

    def execute(self, _robot_id, path, task_id, _deadline_ms):
        self.calls.append(task_id)
        if not self.duplicate_pose:
            self.now += 1
            self.sequence += 1
            self.position = path["waypoints"][-1]
        digest = hashlib.sha256(json.dumps(path, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode()).hexdigest()
        return DriverResult(self.outcome, task_id, "wrong" if self.bad_receipt else digest)

    def stop(self, _robot_id, task_id):
        self.stops.append(task_id)
        return self.stop_ack

    def publish(self, event):
        self.reports.append(event)


class Policy:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def propose(self, _start_pose, **_kwargs):
        item = candidate(destination=pose(3.5))
        return {"candidates": [item], "diagnostics": {"test": True}}


def runner(port, *, tmp_path=None, **overrides):
    ledger = CoverageLedger("mission", "world-1", geometry_id=geometry_id(port.document))
    coordinator = TaskCoordinator("mission", "world-1", journal_path=tmp_path / "tasks.jsonl" if tmp_path else None)
    return SpatialMissionRunner(Policy, port, port, port, ledger, coordinator,
                                clock_ms=lambda: port.now, **overrides)


def test_closed_loop_requires_bound_receipt_and_new_goal_pose():
    port = Port()
    operation = runner(port)
    result = operation.step("robot-a")
    assert result["reason"] == "succeeded"
    assert operation.coordinator.tasks[-1]["state"] == "succeeded"
    assert port.position == pose(3.5)
    assert result["coverage"]["observedVoxels"] == 1


@pytest.mark.parametrize("flag,code", [("duplicate_pose", "ARRIVAL_NOT_PROVEN"),
                                      ("bad_receipt", "RECEIPT_MISMATCH")])
def test_false_controller_success_fences_execution_until_positive_stop(flag, code):
    port = Port()
    setattr(port, flag, True)
    operation = runner(port)
    result = operation.step("robot-a")
    assert result["reason"] == "execution_uncertain"
    assert result["failure"] == code
    assert result["stopAcknowledged"] is False
    assert operation.step("robot-a")["reason"] == "execution_fenced"
    assert len(port.calls) == 1
    operation.coordinator.acknowledge_stop(port.calls[0], "robot-a", port.now)
    assert operation.coordinator.tasks[-1]["state"] == "stopped"


def test_cancel_before_capture_and_no_unknown_space_in_motion():
    port = Port()
    port.cancelled = True
    operation = runner(port)
    with pytest.raises(MissionError, match="cancelled"):
        operation.step("robot-a")
    assert port.calls == []


def test_resume_keeps_cumulative_motion_budget(tmp_path):
    port = Port()
    operation = runner(port, tmp_path=tmp_path, max_steps=1)
    assert operation.step("robot-a")["reason"] == "succeeded"
    operation.coordinator.journal.close()
    resumed = runner(port, tmp_path=tmp_path, max_steps=1)
    assert resumed.step("robot-a")["reason"] == "budget_exhausted"
    assert len(port.calls) == 1
    resumed.coordinator.journal.close()
    with pytest.raises(MissionError, match="budgets"):
        runner(port, tmp_path=tmp_path, max_steps=2)


def test_localization_world_and_geometry_are_admitted_before_motion():
    port = Port()
    operation = runner(port)
    port.document["worldFrameRevision"] = "unregistered-world"
    with pytest.raises(MissionError) as failure:
        operation.step("robot-a")
    assert failure.value.code == "WORLD_MISMATCH"
    assert port.calls == []


def test_changed_map_requires_new_proposal_without_dispatch():
    port = Port()
    operation = runner(port)

    class ChangedPolicy(Policy):
        def propose(self, start_pose, **kwargs):
            result = super().propose(start_pose, **kwargs)
            port.document["mapRevision"] = "snapshot-2"
            return result

    operation.explorer_factory = ChangedPolicy
    with pytest.raises(MissionError) as failure:
        operation.step("robot-a")
    assert failure.value.code == "SNAPSHOT_CHANGED"
    assert operation.coordinator.tasks[-1]["state"] == "cancelled"
    assert port.calls == []


def test_degraded_localization_does_not_authorize_a_plan():
    port = Port()
    original = port.snapshot
    port.snapshot = lambda robot: dataclasses.replace(original(robot), localized=False)
    operation = runner(port)
    with pytest.raises(MissionError) as failure:
        operation.step("robot-a")
    assert failure.value.code == "LOCALIZATION_STALE"
    assert port.calls == []


def test_persistent_authority_requires_one_writer_and_closed_journal_rejects_writes(tmp_path):
    path = tmp_path / "journal.jsonl"
    owner = EventJournal(path)
    with pytest.raises(MissionError) as failure:
        EventJournal(path)
    assert failure.value.code == "JOURNAL_OWNED"
    owner.close()
    with pytest.raises(MissionError, match="closed"):
        owner.append("cannot_append", {})
    replacement = EventJournal(path)
    replacement.append("replacement", {})
    replacement.close()


def test_conflicting_shared_measurements_stop_runner_before_physical_dispatch():
    port = Port()
    operation = runner(port)
    operation._capture("robot-a")
    operation.ledger.observe("robot-b", "camera-b", 1, port.now, port.now, "world-1",
                             {10: 100}, "different-camera", geometry_id=geometry_id(port.document))
    with pytest.raises(MissionError) as failure:
        operation.step("robot-a")
    assert failure.value.code == "MAP_CONFLICT"
    assert port.calls == []


@pytest.mark.parametrize("mutation,code", [("mapRevision", "PROPOSAL_SNAPSHOT_MISMATCH"),
                                         ("start", "PROPOSAL_START_MISMATCH")])
def test_swapped_planner_proposals_cannot_dispatch_from_another_snapshot(mutation, code):
    port = Port()
    operation = runner(port)

    class BadPolicy(Policy):
        def propose(self, start_pose, **kwargs):
            result = super().propose(start_pose, **kwargs)
            path = result["candidates"][0]["path"]
            if mutation == "start":
                path["waypoints"][0] = pose(1.5)
            else:
                path[mutation] = "other-snapshot"
            return result

    operation.explorer_factory = BadPolicy
    with pytest.raises(MissionError) as failure:
        operation.step("robot-a")
    assert failure.value.code == code
    assert port.calls == []


def test_production_three_dimensional_explorer_runs_through_mission_ports():
    from tangying_robot_gateway.spatial_exploration import (
        ExplorationConfig,
        SensorModel,
        SpatialExplorer,
    )

    port = Port()
    # Current measured map has a genuinely unknown upper/right volume. The
    # policy receives no oracle; this test verifies dispatch/receipt integration.
    for z in range(7):
        for y in range(7):
            for x in range(5, 7):
                port.document["cells"][(z * 7 + y) * 7 + x] = -1
    operation = runner(port)
    operation.explorer_factory = lambda snapshot: SpatialExplorer(
        snapshot.volume, POLICY, SensorModel(range_m=4.),
        ExplorationConfig(max_candidates=12, max_plan_calls=3, max_rays=9))
    result = operation.step("robot-a")
    assert result["reason"] == "succeeded"
    dispatched = operation.coordinator.tasks[-1]["candidate"]
    assert dispatched["informationGain"] > 0
    assert dispatched["path"]["waypoints"][0] == pose()
    assert len(port.calls) == 1


def test_resume_cannot_reinterpret_old_targets_in_a_different_voxel_geometry(tmp_path):
    port = Port()
    operation = runner(port, tmp_path=tmp_path)
    operation.coordinator.journal.close()
    port.document["origin"] = [.5, 0., 0.]
    with pytest.raises(MissionError) as failure:
        runner(port, tmp_path=tmp_path)
    assert failure.value.code == "MISSION_CONTEXT_MISMATCH"


def test_integer_and_float_geometry_encodings_share_the_same_grid_identity():
    document = volume()
    integer_encoded = {**document, "origin": [0, 0, 0], "resolution": 1}
    assert geometry_id(document) == geometry_id(integer_encoded)


def test_quaternion_norm_tolerance_cannot_falsely_prove_goal_orientation():
    port = Port()
    execute = port.execute

    def drifted_pose(*args):
        result = execute(*args)
        port.position = [*port.position[:3], 1.0004 * math.cos(.11 / 2),
                         0., 0., 1.0004 * math.sin(.11 / 2)]
        return result

    port.execute = drifted_pose
    result = runner(port).step("robot-a")
    assert result["reason"] == "execution_uncertain"
    assert result["failure"] == "ARRIVAL_NOT_PROVEN"


def test_journal_io_failure_is_sticky_and_does_not_commit_memory(tmp_path, monkeypatch):
    import os

    from tangying_robot_gateway import spatial_mission

    # A writer must never create a file too large for its own recovery reader.
    bounded = EventJournal(tmp_path / "bounded.jsonl")
    with monkeypatch.context() as capacity:
        capacity.setattr(spatial_mission, "MAX_JOURNAL_BYTES", 10)
        with pytest.raises(MissionError) as failure:
            bounded.append("too_large", {})
        assert failure.value.code == "JOURNAL_CAPACITY"
        assert bounded.events == []
        assert bounded.path.stat().st_size == 0
    bounded.close()
    journal = EventJournal(tmp_path / "events.jsonl")

    def disk_full(_descriptor):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", disk_full)
    with pytest.raises(OSError):
        journal.append("not_committed", {})
    assert journal.events == []
    with pytest.raises(MissionError) as failure:
        journal.append("cannot_continue", {})
    assert failure.value.code == "JOURNAL_UNAVAILABLE"
    journal.close()


def test_failed_journal_after_dispatch_still_calls_controller_stop_and_retains_fence():
    port = Port()
    port.stop_ack = True
    operation = runner(port)
    execute = port.execute

    def execution_with_disk_failure(*args):
        result = execute(*args)
        operation.coordinator.journal._failed = True
        return result

    port.execute = execution_with_disk_failure
    result = operation.step("robot-a")
    assert result["reason"] == "execution_journal_failed"
    assert len(port.stops) == 1
    assert result["stopAcknowledged"] is False
    assert operation.coordinator.tasks[-1]["state"] == "running"
    assert operation.step("robot-a")["reason"] == "execution_fenced"
    assert len(port.calls) == 1
