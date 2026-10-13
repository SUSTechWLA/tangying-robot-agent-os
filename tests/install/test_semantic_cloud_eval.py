"""The replay harness uses production services with an explicitly synthetic map."""
import hashlib
import json

import numpy as np
import pytest
from tangying_robot_gateway.map_pipeline import PointCloud, build_map

from scripts import evaluate_semantic_cloud as suite


def source_map(tmp_path):
    source = tmp_path / "source" / "replay-fixture"
    grid = {"width": 60, "height": 60, "resolution": .1, "origin": [-3., -3., 0.],
            "cells": np.zeros((60, 60), dtype=np.int16)}
    points = np.array([[.20, .20, .01], [.25, .20, .04], [.3, .3, .08],
                       [1., 1., .5], [-1., -1., .7]], dtype=np.float32)
    build_map(source, map_id=source.name, robot_id="replay-unit", cloud=PointCloud(points),
              source="rgbd_slam", calibration_revision="a" * 64, occupancy_grid=grid,
              created_at_unix_ms=1_900_000_000_000,
              slam_metadata={"schemaVersion": "slam.session.v1", "navigationEvidenceVersion": 2,
                  "robotId": "replay-unit", "mapId": source.name, "calibrationRevision": "a" * 64,
                  "worldFrameRevision": "synthetic-replay-fixture", "mapFromWorld": [0., 0., 0.],
                  "footprintRadiusM": .30, "observations": [{"id": "synthetic-1", "stamp": 1_899_999_999_000,
                                                              "odometry": [0., 0., 0.], "baseZ": .035}]})
    return source


def test_full_replay_preserves_requests_failures_and_source_without_any_motion(tmp_path):
    source = source_map(tmp_path)
    original = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    output = tmp_path / "evidence"
    report = suite.evaluate(source, output, evidence_origin="synthetic_unit_fixture")
    assert report["passed"], report
    assert report["caseCount"] == report["passedCaseCount"] == 12
    assert report["physicalCommandAttempts"] == report["physicalCommandsExecuted"] == 0
    assert report["liveLocalizationTested"] is False and report["newGazeboMotionTested"] is False
    assert report["automaticSemanticRecognitionTested"] is False
    assert report["evidenceOriginDeclaration"] == "synthetic_unit_fixture"
    assert original == {str(path.relative_to(source)): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    captures = json.loads((output / "replayed-captures.json").read_text())
    assert captures and all(item["timestampInjected"] and not item["newSensorMeasurement"] for item in captures)
    cases = {case["id"]: case for case in report["cases"]}
    assert cases["S04-plan-replay"]["details"]["candidateCount"] > 0
    requests = json.loads((output / "requests.json").read_text())
    assert any(item["code"] == "SEMANTIC_REVISION_CONFLICT" for item in requests)
    assert any(item["code"] == "SEMANTIC_AMBIGUOUS" for item in requests)
    assert any(item["code"] == "SEMANTIC_STALE" for item in requests)
    manifest = json.loads((output / "manifest.json").read_text())
    for name, metadata in manifest["files"].items():
        payload = (output / name).read_bytes()
        assert len(payload) == metadata["bytes"]
        assert hashlib.sha256(payload).hexdigest() == metadata["sha256"]


def test_existing_output_is_never_overwritten(tmp_path):
    source = source_map(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("original evidence")
    with pytest.raises(FileExistsError):
        suite.evaluate(source, output)
    assert marker.read_text() == "original evidence"
    assert list(output.iterdir()) == [marker]


def test_corrupt_input_preserves_failed_setup_report_without_copying_or_modifying_it(tmp_path):
    source = source_map(tmp_path)
    cloud = source / "cloud" / "lod4.bin"
    cloud.write_bytes(cloud.read_bytes() + b"pre-existing-corruption")
    original = cloud.read_bytes()
    output = tmp_path / "failed-evidence"
    report = suite.evaluate(source, output, evidence_origin="synthetic_unit_fixture")
    assert report["passed"] is False and report["caseCount"] == 0
    assert "verification failed" in report["error"]
    assert report["physicalCommandAttempts"] == 0
    assert cloud.read_bytes() == original
    assert json.loads((output / "report.json").read_text())["passed"] is False
    assert (output / "manifest.json").exists()
