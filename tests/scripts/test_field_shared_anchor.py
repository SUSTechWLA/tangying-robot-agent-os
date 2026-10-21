import copy

import numpy as np
import pytest

from sim.gazebo.field_shared_anchor import compose_shared_cad_anchor


def fixtures():
    scene = "a" * 64
    def sample(robot, x):
        basis = {"robotId": robot, "frameId": f"{robot}::base_link::lidar", "mapId": "field",
                 "mapRevision": "local-1", "sourceId": "lidar", "observationId": f"obs-{robot}"}
        matrix = np.eye(4)
        matrix[0, 3] = x
        result = {"schemaVersion": "simulation.known-scene-calibration.v1", "accepted": True,
                  "code": "ACCEPTED", "sceneRevision": scene, "referencePointsSha256": "b" * 64,
                  "estimatedSensorToWorld": matrix.tolist(),
                  "registration": {"sourceEvidence": basis.copy(), "targetEvidence": {
                      "robotId": "survey-station", "frameId": "world", "mapId": "field",
                      "mapRevision": scene, "sourceId": "surveyed-static-cad"}}}
        return result, basis
    source, sb = sample("ground-b", 2.)
    target, tb = sample("ground-a", -1.)
    return source, target, sb, tb, scene


def test_shared_anchor_composes_estimates_and_records_hashes_without_truth():
    source, target, sb, tb, scene = fixtures()
    result = compose_shared_cad_anchor(source, target, source_basis=sb,
                                       target_basis=tb, expected_scene_revision=scene)
    np.testing.assert_allclose(np.array(result["sourceToTarget"])[:3, 3], [3., 0., 0.])
    assert result["knownSurveyedEnvironmentRequired"]
    assert not result["simulatorTruthUsedForComposition"]
    assert not result["directCloudRegistrationSucceeded"]
    assert len(result["sourceHashes"]["sourceCalibrationCanonicalSha256"]) == 64


@pytest.mark.parametrize("mutation", ["rejected", "wrong_scene", "wrong_map", "wrong_frame", "wrong_robot", "wrong_cloud"])
def test_mismatched_or_rejected_calibrations_cannot_be_composed(mutation):
    source, target, sb, tb, scene = fixtures()
    broken = copy.deepcopy(source)
    if mutation == "rejected":
        broken["accepted"] = False
    elif mutation == "wrong_scene":
        broken["sceneRevision"] = "c" * 64
    elif mutation == "wrong_cloud":
        broken["referencePointsSha256"] = "c" * 64
    elif mutation == "wrong_map":
        broken["registration"]["targetEvidence"]["mapId"] = "elsewhere"
    elif mutation == "wrong_frame":
        broken["registration"]["targetEvidence"]["frameId"] = "odom"
    else:
        broken["registration"]["sourceEvidence"]["robotId"] = "someone-else"
    with pytest.raises(ValueError):
        compose_shared_cad_anchor(broken, target, source_basis=sb,
                                  target_basis=tb, expected_scene_revision=scene)
