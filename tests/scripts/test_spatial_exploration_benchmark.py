"""Offline experiment validity: pairing, sensor truth boundary, faults and replay."""
from __future__ import annotations

import copy
import hashlib
import json
import math

import pytest
from tangying_robot_gateway.spatial_exploration import SensorModel

from scripts import spatial_exploration_benchmark as bench


@pytest.fixture
def settings():
    value = json.loads((bench.ROOT / "examples/experiments/spatial_exploration_ablation.json").read_text())
    value.update(scenes=["vertical_complementarity"], seeds=[7], variants=["baseline"], maxRounds=2)
    value["exploration"].update(max_candidates=12, max_plan_calls=3, max_rays=25)
    return value


def test_scene_pairing_has_identical_inputs_and_actual_geometric_seed_variation():
    for name, builder in bench.SCENE_BUILDERS.items():
        first, second = builder(name, 7), builder(name, 7)
        assert first.truth == second.truth and first.starts == second.starts and first.faults == second.faults
        assert bench.occupancy_geometry_hash(first.truth) != bench.occupancy_geometry_hash(builder(name, 19).truth)
        assert len(first.target_ids) == math.prod(size - 2 for size in first.truth["shape"])
        assert first.policies["ugv"]["mode"] == "terrain25d"
        assert first.policies["uav"]["mode"] == "volume3d"


def test_ablation_switches_are_independent_and_never_disable_safety():
    assert len(bench.VARIANTS) == 7 and bench.VARIANTS["baseline"] == {}
    assert all(len(settings) == 1 for name, settings in bench.VARIANTS.items() if name != "baseline")
    assert all("collision" not in key and "safety" not in key for settings in bench.VARIANTS.values() for key in settings)


def test_assignment_and_coverage_guidance_can_be_ablated_independently(settings):
    base = bench.effective_variant(settings, "baseline")
    no_assignment = bench.effective_variant(settings, "no_assignment_overlap")
    no_guidance = bench.effective_variant(settings, "no_shared_coverage_guidance")
    joint = bench.effective_variant(settings, "no_coordination")
    assert no_assignment["exploration"] == no_guidance["exploration"] == base["exploration"]
    assert no_assignment["cooperation"] == {"assignment_overlap": False, "shared_coverage_guidance": True}
    assert no_guidance["cooperation"] == {"assignment_overlap": True, "shared_coverage_guidance": False}
    assert not any(joint["cooperation"].values())
    settings["cooperation"] = {"assignment_overlap": False, "shared_coverage_guidance": True}
    assert bench.effective_variant(settings, "baseline")["cooperation"] == settings["cooperation"]


def test_extended_scenes_have_distinct_structure_and_real_sealed_unknown_region():
    layouts = [bench.occupancy_geometry_hash(bench.build_extended_scene(name, 7).truth)
               for name in bench.EXTENDED_SCENES]
    # The recovery family intentionally reuses a doorway layout with a different
    # fault schedule; it is not counted as an independent physical map.
    assert len(set(layouts)) == 4
    scene = bench.build_extended_scene("sealed_chamber", 7)
    hidden = scene.evaluator_annotations["sealedUnobservableInteriorVoxelIds"]
    assert hidden and set(hidden) <= scene.target_ids
    assert all(scene.truth["cells"][voxel] == 0 for voxel in hidden)
    ports = bench.TruthPorts(scene, SensorModel())
    assert not set(hidden) & set(ports.observe(scene.starts["uav"]))


def test_new_design_splits_have_disjoint_seeds_and_actual_layouts(tmp_path):
    development = json.loads((bench.ROOT / "examples/experiments/spatial_exploration_development.json").read_text())
    holdout = json.loads((bench.ROOT / "examples/experiments/spatial_exploration_holdout.json").read_text())
    assert bench.validate_settings(development) and bench.validate_settings(holdout)
    assert bench.study_design_hash(development) == bench.study_design_hash(holdout)
    assert development["selectionFreeze"] is None and holdout["selectionFreeze"] is None
    _, registration = bench.freeze_design(development, tmp_path)
    for audit in registration["splitGeometryAudit"].values():
        assert audit["crossSplitGeometryOverlapN"] == 0
        assert audit["uniqueGeometryNBySplit"] == {"development": 4, "holdout": 6}
    assert registration["globalCrossSplitGeometryOverlapN"] == 0
    assert registration["declaredUniqueGeometryNBySplit"] == {"development": 16, "holdout": 24}


def test_split_audit_detects_geometry_alias_despite_different_seed_names(settings, tmp_path):
    settings.update(studyId="alias-test", split="development", seeds=[7],
                    seedPartitions={"development": [7], "holdout": [11]})
    assert bench.validate_settings(settings)
    with pytest.raises(ValueError, match="occupancy geometry overlaps"):
        bench.freeze_design(settings, tmp_path)


@pytest.mark.parametrize("named_split", [False, True])
def test_nondeterministic_scene_plugin_cannot_pass_split_audit(settings, tmp_path, monkeypatch, named_split):
    calls = []

    def nondeterministic_builder(scene_id, seed):
        scene = bench.build_scene("vertical_complementarity", seed)
        scene.scene_id = scene_id
        calls.append((scene_id, seed))
        if sum(item == (scene_id, seed) for item in calls) > 1:
            index = bench.flat(scene.truth["shape"], 8, 7, 3)
            scene.truth["cells"][index] = 100
        return scene

    monkeypatch.setitem(bench.SCENE_BUILDERS, "unstable_fixture", nondeterministic_builder)
    settings.update(scenes=["unstable_fixture"], seeds=[7])
    if named_split:
        settings.update(studyId="unstable-scene-test", split="development",
                        seedPartitions={"development": [7], "holdout": [19]})
    assert bench.validate_settings(settings)
    with pytest.raises(ValueError, match="not seed-deterministic"):
        bench.freeze_design(settings, tmp_path)
    assert not (tmp_path / "preregistration.json").exists()


def test_split_seed_overlap_and_noop_ablation_are_rejected(settings):
    settings.update(studyId="overlap-test", split="development",
                    seedPartitions={"development": [7], "holdout": [7, 11]})
    with pytest.raises(ValueError, match="seed partitions overlap"):
        bench.validate_settings(settings)
    settings.pop("seedPartitions")
    settings.pop("split")
    settings.update(cooperation={"assignment_overlap": False}, variants=["baseline", "no_assignment_overlap"])
    with pytest.raises(ValueError, match="does not change"):
        bench.validate_settings(settings)


def test_shared_guidance_is_an_explicit_received_channel_not_omniscient_map(settings, tmp_path, monkeypatch):
    calls = []

    class Recorder:
        def __init__(self, volume):
            self.volume = copy.deepcopy(volume)

        def propose(self, start, *, observed_targets, **kwargs):
            calls.append((self.volume, set(observed_targets)))
            return {"status": "no_candidate", "candidate": None, "candidates": [], "diagnostics": {}}

    monkeypatch.setitem(bench.BACKENDS, "fixture", lambda volume, *args: Recorder(volume))
    settings.update(backend="fixture", mapExchangeDelayRounds=2)
    enabled = bench.run_episode(bench.build_scene("vertical_complementarity", 7), "baseline", settings, tmp_path / "enabled")
    first_volume, first_guidance = calls[0]
    assert any(first_volume["cells"][voxel] == -1 for voxel in first_guidance)
    assert enabled["communicationByChannel"]["coverage_delta"]["messageCount"] > 0
    assert enabled["queuedMapPackets"] == 2
    calls.clear()
    disabled = bench.run_episode(bench.build_scene("vertical_complementarity", 7), "no_shared_coverage_guidance", settings, tmp_path / "disabled")
    first_volume, first_guidance = calls[0]
    assert all(first_volume["cells"][voxel] != -1 for voxel in first_guidance)
    assert "coverage_delta" not in disabled["communicationByChannel"]
    assert enabled["communicationByChannel"]["occupancy_relay"] == disabled["communicationByChannel"]["occupancy_relay"]


def test_blank_map_has_no_occupancy_or_terrain_truth():
    scene = bench.build_scene("vertical_complementarity", 7)
    known = bench.blank_volume(scene, "ugv")
    assert set(known["cells"]) == {-1}
    assert not any(known["terrain"]["supported"])
    assert all(value is None for value in known["terrain"]["heights"])
    known["cells"][0] = 0
    assert scene.truth["cells"][0] == 100


def test_sensor_hits_occluder_without_reporting_hidden_voxels():
    scene = bench.build_scene("vertical_complementarity", 7)
    shape = scene.truth["shape"]
    for z in range(shape[2]):
        for y in range(shape[1]):
            scene.truth["cells"][bench.flat(shape, 4, y, z)] = 100
    ports = bench.TruthPorts(scene, SensorModel(8.0, 0.01, 0.01))
    observed = ports.observe(bench.pose(2.5, 2.5, 2.5))
    assert bench.flat(shape, 4, 2, 2) in observed
    assert all(index % shape[0] <= 4 for index in observed)


def test_driver_independently_rejects_static_obstacle_and_parked_peer():
    scene = bench.build_scene("vertical_complementarity", 7)
    ports = bench.TruthPorts(scene, SensorModel())
    path = {"waypoints": [bench.pose(2.5, 1.5, 1.5), bench.pose(4.5, 1.5, 1.5)]}
    assert ports.path_clear(path, scene.policies["ugv"])
    assert not ports.path_clear(path, scene.policies["ugv"], [(bench.pose(3.5, 1.5, 1.5), scene.policies["uav"])])
    path["waypoints"][1] = bench.pose(6.5, 3.5, 1.5)
    assert not ports.path_clear(path, scene.policies["ugv"])


def test_measurements_preserve_unknown_and_do_not_refresh_old_terrain():
    scene = bench.build_scene("vertical_complementarity", 7)
    volume = bench.blank_volume(scene, "ugv")
    floor = bench.flat(scene.truth["shape"], 2, 2, 0)
    bench.merge_measurements(volume, {floor: 100}, 123)
    bench.merge_measurements(volume, {floor: 100}, 12)
    assert volume["terrain"]["observedAtUnixMs"] == 123
    assert volume["cells"].count(-1) == len(volume["cells"]) - 1


def test_event_chain_normalizes_integer_voxel_keys_and_detects_tampering(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = bench.Events(path)
    journal.add("observation", classified={2: 0, 11: 100})
    journal.add("episode_end", stopReason="fixture")
    journal.close()
    assert bench.read_events(path)[0]["classified"] == {"2": 0, "11": 100}
    path.write_text(path.read_text().replace('"2":0', '"2":100'))
    with pytest.raises(ValueError, match="event chain"):
        bench.read_events(path)


def test_evaluator_reports_partial_and_null_unmeasured_metrics():
    result = bench.independent_metrics([], {1, 2}, 1.0)
    assert result["status"] == "partial" and result["coverageFraction"] == 0
    assert result["energyJ"] is None and result["physicalCollisionCount"] is None
    assert result["planningMsP95"] is None and result["admissionMsP50"] is None


def test_posthoc_team_coverage_is_distinct_from_coordinator_received_evidence():
    events = [{"kind": "episode_start", "benchmarkProtocolVersion": 2},
              {"kind": "observation", "robotId": "uav", "evidenceId": "uav-1", "classified": {0: 0}},
              {"kind": "episode_end"}]
    result = bench.independent_metrics(events, {0}, 1.0, {"cells": [0]})
    assert result["coverageFraction"] == 1.0 and result["coordinatorCoverageFraction"] == 0


def test_evaluator_rejects_wrong_classification_and_checks_recovery_after_success():
    with pytest.raises(ValueError, match="evaluator truth"):
        bench.independent_metrics([{"kind": "observation", "robotId": "uav", "classified": {0: 100}}],
                                  {0}, 1.0, {"cells": [0]})
    events = [{"kind": "execution", "robotId": "uav", "outcome": "unknown", "admissionMs": 1},
              {"kind": "stop_acknowledged"}]
    assert bench.independent_metrics(events, {0}, 1.0)["recoveredUncertainLegs"] == 0
    events.append({"kind": "execution", "robotId": "uav", "outcome": "succeeded", "admissionMs": 1,
                   "path": {"waypoints": [[0, 0, 0], [1, 0, 0]]}})
    assert bench.independent_metrics(events, {0}, 1.0)["recoveredUncertainLegs"] == 1


def test_actual_production_episode_passes_only_measured_snapshots(settings, tmp_path, monkeypatch):
    snapshots = []
    original = bench.BACKENDS["production_voxel"]

    def record(volume, *args):
        snapshots.append(copy.deepcopy(volume))
        return original(volume, *args)

    monkeypatch.setitem(bench.BACKENDS, "production_voxel", record)
    metrics = bench.run_episode(bench.build_scene("vertical_complementarity", 7), "baseline", settings, tmp_path / "episode")
    assert snapshots and snapshots[0]["cells"].count(-1) > 0
    assert metrics["completedLegs"] > 0 and metrics["status"] == "partial"
    assert metrics["physicalExecution"] is False and metrics["hardwareSuccess"] is None
    events = bench.read_events(tmp_path / "episode/events.jsonl")
    assert all(event["truthSafetyAdmitted"] and event["knownSnapshotAdmitted"] for event in events
               if event["kind"] == "execution" and event["outcome"] == "succeeded")
    first_execution = next(index for index, event in enumerate(events) if event["kind"] == "execution")
    assert len([event for event in events[:first_execution] if event["kind"] == "assignment"]) == 2


def test_production_fault_episode_reconnects_and_resolves_unknown_before_continuing(settings, tmp_path):
    settings["maxRounds"] = 7
    settings["exploration"].update(max_candidates=32, max_plan_calls=6, max_rays=49)
    result = bench.run_episode(bench.build_scene("recovery_disconnect", 7), "baseline", settings, tmp_path / "episode")
    assert result["faultEvents"] == 3 and result["reconnectSnapshots"] == 1
    assert result["plannedFaults"] == 3 and result["appliedFaults"] == 3 and result["skippedFaults"] == 0
    assert result["rejectedLegs"] >= 1 and result["uncertainLegs"] == 1
    assert result["stopAcknowledgements"] == 1 and result["recoveredUncertainLegs"] == 1
    events = bench.read_events(tmp_path / "episode/events.jsonl")
    unknown = next(i for i, event in enumerate(events) if event["kind"] == "execution" and event["outcome"] == "unknown")
    acknowledged = next(i for i, event in enumerate(events) if event["kind"] == "stop_acknowledged")
    assert unknown < acknowledged
    assert not any(event["kind"] == "execution" for event in events[unknown + 1:acknowledged])
    assert any(event["kind"] == "uncertain_fence_retained" for event in events[unknown:acknowledged])


def test_unexercised_faults_are_skipped_not_counted_as_applied(settings, tmp_path):
    result = bench.run_episode(bench.build_scene("recovery_disconnect", 7), "baseline", settings, tmp_path / "episode")
    assert result["plannedFaults"] == 3 and result["appliedFaults"] == 0 and result["skippedFaults"] == 3
    assert result["faultEvents"] == 0
    assert all(item["reason"] == "episode_ended_before_scheduled_round" for item in result["faultSkips"])


def test_unknown_first_robot_does_not_expire_second_robot_assignment(settings, tmp_path):
    scene = bench.build_scene("recovery_disconnect", 7)
    scene.faults = [{"round": 0, "robotId": "ugv", "kind": "unknown_execution"}]
    result = bench.run_episode(scene, "baseline", settings, tmp_path / "episode")
    assert result["appliedFaults"] == 1 and result["uncertainLegs"] == 1
    events = bench.read_events(tmp_path / "episode/events.jsonl")
    executions = [event for event in events if event["kind"] == "execution"]
    assert executions[0]["outcome"] == "unknown" and executions[1]["outcome"] == "succeeded"
    timed = [event["nowMs"] for event in events if "nowMs" in event]
    assert timed == sorted(timed)


def test_geometry_identity_ignores_names_and_seed_labels():
    first = bench.build_scene("vertical_complementarity", 7)
    second = copy.deepcopy(first.truth)
    second["mapId"] = "different-label"
    assert bench.digest(first.truth) != bench.digest(second)
    assert bench.occupancy_geometry_hash(first.truth) == bench.occupancy_geometry_hash(second)


def test_campaign_freezes_sources_and_replay_checks_semantics(settings, tmp_path, monkeypatch):
    class EmptyPolicy:
        def propose(self, *args, **kwargs):
            return {"status": "no_candidate", "candidate": None, "candidates": [], "diagnostics": {}}

    monkeypatch.setitem(bench.BACKENDS, "fixture", lambda *args: EmptyPolicy())
    settings.update(backend="fixture", seeds=[7, 19], variants=["baseline", "nearest_frontier"])
    config, output = tmp_path / "input.json", tmp_path / "campaign"
    bench.write_json(config, settings)
    summary = bench.campaign(config, output)
    assert len(summary["episodes"]) == 4 and len(summary["pairedDeltas"]) == 2
    assert summary["pairedBlockN"] == 2 and summary["uncertainty"]["confidenceInterval"] is None
    assert summary["uniqueGeometryN"] == 2 and summary["uniqueGeometryNByScene"] == {"vertical_complementarity": 2}
    assert len(summary["paretoLabels"]) == 2
    assert bench.replay(output)["verifiedEpisodes"] == 4
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["state"] == "finalized" and len(manifest["sourceHashes"]) == 4
    assert all((output / "source" / source).is_file() for source in bench.SOURCES)
    assert (output / "preregistration.json").is_file() and (output / "scoring-contract.json").is_file()
    with pytest.raises(FileExistsError):
        bench.campaign(config, output)
    # A self-consistent byte manifest is not enough: independent replay also
    # checks that campaign summary entries match the episode evidence.
    summary["episodes"][0]["coverageFraction"] = 1.0
    bench.write_json(output / "summary.json", summary)
    payload = (output / "summary.json").read_bytes()
    manifest["files"]["summary.json"] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
    bench.write_json(output / "manifest.json", manifest)
    with pytest.raises(ValueError, match="summary contradicts"):
        bench.replay(output)


def test_truth_and_score_are_preregistered_before_first_policy_call(settings, tmp_path, monkeypatch):
    output = tmp_path / "campaign"
    checked = []

    class EmptyPolicy:
        def propose(self, *args, **kwargs):
            return {"status": "no_candidate", "candidate": None, "candidates": [], "diagnostics": {}}

    def backend(volume, *args):
        registration = json.loads((output / "preregistration.json").read_text())
        scoring = json.loads((output / "scoring-contract.json").read_text())
        assert all((output / block["path"]).is_file() for block in registration["blocks"])
        assert scoring["coverageThreshold"] == 1.0 and scoring["policyTruthAccess"] is False
        assert -1 in volume["cells"]
        checked.append(True)
        return EmptyPolicy()

    monkeypatch.setitem(bench.BACKENDS, "fixture", backend)
    settings["backend"] = "fixture"
    config = tmp_path / "input.json"
    bench.write_json(config, settings)
    bench.campaign(config, output)
    assert checked and bench.replay(output)["passed"]


@pytest.mark.parametrize("field,value", [("seeds", [7, 7]), ("seeds", [True]),
    ("variants", ["disable_collision"]), ("maxRounds", 0), ("coverageThreshold", float("nan"))])
def test_invalid_design_is_rejected(settings, field, value):
    settings[field] = value
    with pytest.raises(ValueError):
        bench.validate_settings(settings)
