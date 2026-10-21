"""Scoped peer advice changes planning preference, never measured completion."""
import copy

import pytest
from tangying_robot_gateway.spatial_mission import CoverageLedger, EventJournal
from tangying_robot_gateway.spatial_peer_exchange import (
    PeerAdvisedExplorer,
    PeerExchangeClient,
    coverage_query,
    publish_capture,
)


def ledger_query():
    ledger = CoverageLedger("mission", "world-1", [1, 2], geometry_id="grid-1")
    query = coverage_query(ledger, mission_revision="r1", basis={
        "worldId": "room", "frameId": "map", "transformRevision": "world-1",
        "mapId": "map", "mapRevision": "v1"})
    return ledger, query


class Explorer:
    def __init__(self):
        self.seen = None
        self.proposal = {"candidates": [
            {"targetId": "a", "score": 2., "expectedObservedVoxelIds": [1], "path": {"digest": "a"}},
            {"targetId": "b", "score": 1.9, "expectedObservedVoxelIds": [2], "path": {"digest": "b"}}]}

    def propose(self, start_pose, *, excluded_targets, observed_targets):
        self.seen = observed_targets
        return self.proposal


class Client:
    def __init__(self, query):
        self.view = {"schemaVersion": "agent.coverage.view.v1", "advisoryOnly": True,
                     "grantsExecution": False, "physicalCompletion": False, "completeRead": True,
                     "evaluatedAtMs": 1000, "cursor": "1", "proposedCoveredTargetIds": ["1"],
                     **{key: query[key] for key in ("missionId", "missionRevision", "geometryId", "basis")}}
        self.requests = []

    def coverage_view(self, query):
        return copy.deepcopy(self.view)

    def publish(self, request):
        self.requests.append(copy.deepcopy(request))
        return {"messageId": request["requestId"]}


def test_scoped_peer_advice_changes_rank_without_changing_ledger_or_candidates(tmp_path):
    ledger, query = ledger_query()
    explorer, client = Explorer(), Client(query)
    before = ledger.summary()
    journal = EventJournal(tmp_path / "advice.jsonl")
    wrapped = PeerAdvisedExplorer(explorer, client, query, clock_ms=lambda: 1000, journal=journal)
    result = wrapped.propose([0], observed_targets=ledger.observed_ids)
    assert [candidate["targetId"] for candidate in result["candidates"]] == ["b", "a"]
    assert ledger.summary() == before and explorer.seen == set()
    assert result["candidates"][1]["path"] == explorer.proposal["candidates"][0]["path"]
    assert explorer.proposal["candidates"][0]["score"] == 2.
    assert journal.events[-1]["kind"] == "peer_coverage_advice"
    assert journal.events[-1]["payload"]["cursor"] == "1"
    journal.close()
    assert ledger.summary()["coverage"] == 0.


@pytest.mark.parametrize("updates", [
    {"completeRead": False}, {"grantsExecution": True}, {"physicalCompletion": True},
    {"missionRevision": "r2"}, {"geometryId": "other"}, {"proposedCoveredTargetIds": ["3"]},
    {"proposedCoveredTargetIds": ["1", "1"]}, {"proposedCoveredTargetIds": [1]},
    {"evaluatedAtMs": -10000}, {"evaluatedAtMs": 3000},
])
def test_invalid_or_incomplete_views_leave_local_plan_unchanged(updates):
    _, query = ledger_query()
    explorer, client = Explorer(), Client(query)
    client.view.update(updates)
    result = PeerAdvisedExplorer(explorer, client, query, clock_ms=lambda: 1000).propose([0])
    assert result["candidates"] == explorer.proposal["candidates"]
    assert result["diagnostics"]["peerAdvice"]["available"] is False


def test_offline_advice_does_not_block_single_robot_planning():
    _, query = ledger_query()
    class Offline:
        def coverage_view(self, _query):
            raise OSError("offline")
    explorer = Explorer()
    result = PeerAdvisedExplorer(explorer, Offline(), query).propose([0])
    assert result["candidates"] == explorer.proposal["candidates"]


def test_all_targets_reported_by_peers_still_keep_all_candidates_and_zero_local_coverage():
    ledger, query = ledger_query()
    explorer, client = Explorer(), Client(query)
    client.view["proposedCoveredTargetIds"] = ["1", "2"]
    result = PeerAdvisedExplorer(explorer, client, query, weight=1, clock_ms=lambda: 1000).propose([0])
    assert len(result["candidates"]) == 2 and ledger.observed_ids == set()
    assert ledger.summary()["coverage"] == 0


def test_advice_weight_zero_is_explicit_ablation():
    _, query = ledger_query()
    explorer, client = Explorer(), Client(query)
    result = PeerAdvisedExplorer(explorer, client, query, weight=0, clock_ms=lambda: 1000).propose([0])
    assert result["candidates"] == explorer.proposal["candidates"]


def test_export_binds_capture_identity_and_excludes_unscoped_ids():
    ledger, query = ledger_query()
    client = Client(query)
    packet = {"sourceId": "camera", "evidenceId": "frame-1", "worldFrameRevision": "world-1",
              "geometryId": "grid-1", "observedAtMs": 1000, "classified": {1: 0, 999: 100}}
    one = publish_capture(client, query, packet, ["peer-a", "peer-b"])
    two = publish_capture(client, query, packet, ["peer-a", "peer-b"])
    assert one == two and one[0] != one[1]
    assert client.requests[0]["payload"]["observedTargetIds"] == ["1"]
    assert client.requests[0]["payload"]["observedAtMs"] == 1000
    assert ledger.observed_ids == set()
    with pytest.raises(ValueError, match="geometry"):
        publish_capture(client, query, {**packet, "geometryId": "wrong"}, ["peer-a"])
    with pytest.raises(ValueError, match="measured"):
        publish_capture(client, query, {**packet, "classified": {1: -1}}, ["peer-a"])


@pytest.mark.parametrize("url", ["http://localhost:8788", "http://example.com:8788", "http://127.0.0.1:8788/x",
                                "http://user:pass@127.0.0.1:8788", "http://127.0.0.1:8788?x=1"])
def test_admin_client_never_sends_credentials_off_loopback(url):
    with pytest.raises(ValueError):
        PeerExchangeClient(url, "x"*32)


def test_existing_runner_does_not_complete_from_peer_full_coverage_and_keeps_unknown_fence():
    # Reuse the existing measured-pose/controller receipt fixture, not a second
    # executor. A peer claiming every target still cannot bypass its fence.
    from tangying_robot_gateway.spatial_mission import (
        SpatialMissionRunner,
        TaskCoordinator,
        geometry_id,
    )

    from robot.gateway.tests.test_spatial_mission import Policy, Port
    port = Port()
    port.outcome = "unknown"
    ledger = CoverageLedger("mission", "world-1", [1, 2], geometry_id=geometry_id(port.document))
    query = coverage_query(ledger, mission_revision="r1", basis={
        "worldId": "room", "frameId": "world", "transformRevision": "world-1",
        "mapId": "room", "mapRevision": "snapshot-1"})
    client = Client(query)
    client.view["proposedCoveredTargetIds"] = ["1", "2"]
    coordinator = TaskCoordinator("mission", "world-1")
    runner = SpatialMissionRunner(
        lambda snapshot: PeerAdvisedExplorer(Policy(snapshot), client, query, clock_ms=lambda: port.now),
        port, port, port, ledger, coordinator, clock_ms=lambda: port.now)
    result = runner.step("robot-a")
    assert result["reason"] == "execution_uncertain"
    assert result["coverage"]["coverage"] == 0
    assert runner.step("robot-a")["reason"] == "execution_fenced"
    assert len(port.calls) == 1
