"""Study analysis invariants, paired geometry clustering and fixed selection."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from scripts import analyze_spatial_study as study


def row(variant, scene="plain", seed=1, geometry="geometry-1", *, status="partial", coverage=0.5,
        path=10.0, planning=5.0, messages=100):
    return {"variant": variant, "sceneId": scene, "seed": seed, "occupancyGeometryHash": geometry,
            "status": status, "coverageFraction": coverage, "pathLengthM": path,
            "planningMsTotal": planning, "messageBytes": messages}


def test_selection_is_fixed_and_never_filters_partial_episodes():
    rows = [row("a", status="completed", coverage=1.0, path=50), row("a", seed=2, path=0),
            row("b", status="completed", coverage=1.0, path=20), row("b", seed=2, path=20)]
    assert study.select_variant(rows) == "b"
    summary = {item["variant"]: item for item in study.summarize_variants(rows)}
    assert summary["a"]["N"] == 2 and summary["a"]["meanSuccessfulPathLengthM"] == 25
    assert summary["a"]["completedFraction"] == 0.5


@pytest.mark.parametrize("a,b,expected", [
    ({"status": "completed", "coverage": 0.5}, {"coverage": 0.99}, "a"),
    ({"coverage": 0.6, "path": 20}, {"coverage": 0.5, "path": 1}, "a"),
    ({"path": 9, "messages": 1000}, {"path": 10, "messages": 1}, "a"),
    ({"messages": 99}, {"messages": 100}, "a"),
    ({"planning": 99999}, {"planning": 0}, "a"),
])
def test_selection_priority_and_lexicographic_last_tie(a, b, expected):
    assert study.select_variant([row("b", **b), row("a", **a)]) == expected


def test_missing_measurement_is_rejected_instead_of_imputed():
    with pytest.raises(ValueError, match="measured finite"):
        study.select_variant([row("a", path=None)])
    with pytest.raises(ValueError, match="empty study"):
        study.select_variant([])


def test_pairing_rejects_unbalanced_duplicate_or_changed_geometry():
    with pytest.raises(ValueError, match="blocks differ"):
        study.paired_differences([row("baseline"), row("a", seed=2)])
    with pytest.raises(ValueError, match="duplicate"):
        study.paired_differences([row("baseline"), row("baseline"), row("a")])
    with pytest.raises(ValueError, match="geometries differ"):
        study.paired_differences([row("baseline"), row("a", geometry="different")])


def test_relative_delta_zero_baseline_stays_null():
    diffs = study.paired_differences([row("baseline", path=0), row("a", path=1)])
    assert next(item for item in diffs if item["metric"] == "pathLengthM")["relativeDelta"] is None


def test_bootstrap_normalizes_shared_geometry_conditions_and_is_reproducible():
    rows = []
    for scene, seed, geometry, difference in [("plain", 1, "g1", 0.2), ("fault", 1, "g1", 0.2), ("plain", 2, "g2", -0.2)]:
        rows += [row("baseline", scene, seed, geometry), row("a", scene, seed, geometry, coverage=0.5 + difference)]
    diffs = study.paired_differences(rows)
    effects = study.cluster_bootstrap(diffs, seed=17, replicates=500)
    assert effects == study.cluster_bootstrap(diffs, seed=17, replicates=500)
    effect = next(item for item in effects if item["metric"] == "coverageFraction")
    assert effect["pairedEpisodeN"] == 3 and effect["geometryClusterN"] == 2
    assert effect["clusterSizes"] == {"g1": 2, "g2": 1}
    assert effect["episodeWeightedMeanDelta"] == pytest.approx(0.2 / 3)
    assert effect["clusterNormalizedMeanDelta"] == pytest.approx(0)
    assert effect["descriptive95PercentileCI"] == pytest.approx([-0.2, 0.2])


def test_single_geometry_has_no_degenerate_precision_claim():
    diffs = study.paired_differences([row("baseline"), row("a", coverage=0.6)])
    assert all(effect["descriptive95PercentileCI"] is None for effect in study.cluster_bootstrap(diffs, replicates=100))


def test_analysis_verifies_before_creating_output_and_records_integrity(tmp_path, monkeypatch):
    campaign, output = tmp_path / "campaign", tmp_path / "analysis"
    campaign.mkdir()
    rows = [row("baseline"), row("a", coverage=0.6),
            row("baseline", seed=2, geometry="g2"), row("a", seed=2, geometry="g2", coverage=0.7)]
    summary = {"episodes": rows}
    study.write_json(campaign / "summary.json", summary)
    manifest = {"state": "finalized", "configHash": "fixture-config", "sourceHashes": {"fixture.py": "fixture-source"},
                "files": {"summary.json": {"sha256": hashlib.sha256((campaign / "summary.json").read_bytes()).hexdigest()}},
                "split": "development"}
    study.write_json(campaign / "manifest.json", manifest)
    called = []

    def replay(path):
        assert path == campaign and not output.exists()
        called.append(True)
        return {"passed": True, "verifiedEpisodes": 4}

    monkeypatch.setattr(study.benchmark, "replay", replay)
    result = study.analyze(campaign, output, seed=7, replicates=100)
    assert called and result["selectedVariant"] == "a"
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["inputManifestHash"] == hashlib.sha256((campaign / "manifest.json").read_bytes()).hexdigest()
    for name, record in provenance["files"].items():
        payload = (output / name).read_bytes()
        assert len(payload) == record["bytes"] and hashlib.sha256(payload).hexdigest() == record["sha256"]
    assert (output / "paired-effects.csv").is_file()
    monkeypatch.setattr(study.benchmark, "replay", lambda _: {"passed": True})
    with pytest.raises(FileExistsError):
        study.analyze(campaign, output, replicates=100)


def test_failed_replay_does_not_leave_an_analysis_directory(tmp_path, monkeypatch):
    campaign, output = tmp_path / "campaign", tmp_path / "analysis"
    campaign.mkdir()
    study.write_json(campaign / "manifest.json", {"state": "running"})

    def fail(_):
        raise ValueError("campaign not finalized")

    monkeypatch.setattr(study.benchmark, "replay", fail)
    with pytest.raises(ValueError, match="not finalized"):
        study.analyze(campaign, output, replicates=100)
    assert not output.exists()


def test_changed_frozen_input_hash_is_rejected():
    first, second = row("baseline"), row("a")
    first["pairedInputHash"], second["pairedInputHash"] = "one", "two"
    with pytest.raises(ValueError, match="frozen inputs differ"):
        study.paired_differences([first, second])
    changed = copy.deepcopy(first)
    changed["variant"] = "a"
    assert study.paired_differences([first, changed])
