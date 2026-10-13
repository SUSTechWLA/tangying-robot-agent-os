"""Provider replacement and publication guards, with no external SLAM algorithm."""
import itertools
import json
import time
from dataclasses import replace

import numpy as np
import pytest
from tangying_robot_gateway.dense_slam_provider import DenseSlamProvider
from tangying_robot_gateway.geometry import PointCloud, transform
from tangying_robot_gateway.map_catalog import MapCatalog
from tangying_robot_gateway.robot_workflow import RobotWorkflow
from tangying_robot_gateway.service_registry import ServiceError
from tangying_robot_gateway.slam_provider import (
    InsufficientDepthError,
    SlamProgress,
    SlamProviderInfo,
    SlamSnapshot,
)
from tangying_robot_proto.robot.v1 import robot_pb2


class RecordedPlanarProvider:
    """Synthetic contract fixture: neither inherits nor constructs DenseSLAM."""
    info = SlamProviderInfo("test-recorded-planar", "fixture-v1", map_source="import")
    max_frames = 12

    def __init__(self):
        self.observations = []
        self.optimized = False
        self.snapshot_calls = 0

    @property
    def frame_count(self):
        return len(self.observations)

    def add(self, observation):
        self.observations.append(observation)
        return True

    def optimize(self):
        self.optimized = True

    def _poses(self):
        return np.array([[item.robot_state["base_pose"][0], 0., 0.] for item in self.observations])

    def progress(self):
        poses = self._poses()
        return SlamProgress(self.frame_count, 9 * self.frame_count, max(0, self.frame_count - 1), 0,
                            float(sum(np.linalg.norm(b[:2] - a[:2]) for a, b in itertools.pairwise(poses))),
                            tuple(poses[-1]) if len(poses) else None,
                            tuple(tuple(pose) for pose in poses))

    def snapshot(self):
        self.snapshot_calls += 1
        poses = self._poses()
        points = np.array([[x, y, 0.] for x in (0., .2, .4) for y in (-.2, 0., .2)], dtype=np.float32)
        points[-1, 2] = .5
        return SlamSnapshot(self.info, PointCloud(np.tile(points, (self.frame_count, 1))),
                            np.repeat(np.arange(self.frame_count), len(points)), poses, poses, poses,
                            np.array([item.wall_time_unix_ms for item in self.observations]),
                            {"schemaVersion": "slam.session.v1", "algorithm": "test-fixture",
                             "observations": [{"id": item.observation_id} for item in self.observations]})

    def keyframe_document(self, **identity):
        # Image previews are optional; this provider must not invent sensor images.
        return None


def observation(x=0., stamp=None):
    result = robot_pb2.Observation(observation_id=f"recorded-{x}",
                                  wall_time_unix_ms=stamp or int(time.time() * 1000))
    result.robot_state.update({"base_pose": [x, 0., 0., 1., 0., 0., 0.]})
    return result


def workflow_for(tmp_path, factory=RecordedPlanarProvider):
    positions = iter((0., .2, .4))
    captures = itertools.count(int(time.time() * 1000) - 100)
    released = []
    workflow = RobotWorkflow(robot_id="provider-unit", root=tmp_path,
        calibration_get=lambda: {"revision": "a" * 64}, calibration_run=lambda: None,
        calibration_save=lambda *args: None, capture=lambda: observation(next(positions), next(captures)),
        move=lambda *args, **kwargs: pytest.fail("contract fixture must not dispatch motion"),
        reserve=lambda: "held", release=released.append, survey_goals=list,
        semantic_workspaces=lambda anchor: [], world_frame_revision="test-world", slam_factory=factory)
    return workflow, released


def sampled_workflow(tmp_path, factory=RecordedPlanarProvider):
    workflow, released = workflow_for(tmp_path, factory)
    workflow.start({"mode": "manual", "name": "provider replacement"})
    workflow._worker.join(2)
    assert workflow.state == "recording", workflow.message
    workflow._sample()
    workflow._sample()
    return workflow, released


def test_non_dense_provider_samples_plans_publishes_and_activates_verified_map(tmp_path):
    workflow, released = sampled_workflow(tmp_path)
    assert workflow.slam.snapshot_calls == 0, "sampling must not assemble and copy the full cloud"
    assert not hasattr(workflow.slam, "frames") and not hasattr(workflow.slam, "estimator")
    assert workflow.status()["frameCount"] == 3
    grid, _ = workflow._live_grid()
    assert grid is not None
    workflow._build()
    assert workflow.state == "completed"
    assert workflow.slam.optimized
    assert released == ["held"]
    directory, manifest = MapCatalog(tmp_path).open(workflow.map_id, robot_id="provider-unit",
                                                   calibration_revision="a" * 64)
    assert manifest["source"] == "import"
    assert "slam_keyframes" not in manifest["artifacts"]
    metadata = json.loads((directory / "slam-session.json").read_text())
    assert metadata["provider"] == workflow.slam.info.document()
    assert metadata["navigationEvidenceVersion"] == 2
    assert metadata["worldFrameRevision"] == "test-world"
    assert workflow.active["mapRevision"] == manifest["hash"]


def valid_snapshot():
    provider = RecordedPlanarProvider()
    for index in range(3):
        provider.add(observation(index * .2, 1000 + index))
    return provider.snapshot()


@pytest.mark.parametrize("changes,match", [
    ({"sources": np.array([0] * 26)}, "source indices"),
    ({"sources": np.array([3] * 27)}, "source indices"),
    ({"sources": np.array([-1] * 27)}, "source indices"),
    ({"sources": np.array([0.] * 27)}, "source indices"),
    ({"timestamps": np.array([1000, 999, 1002])}, "timestamps"),
    ({"timestamps": np.array([1000, 1000, 1002])}, "timestamps"),
    ({"timestamps": np.array([1000., 1001., 1002.])}, "timestamps"),
    ({"odometry": np.zeros((2, 3))}, "observation count"),
    ({"poses": np.array([[0., 0., 0.], [.2, 0., 0.], [float("nan"), 0., 0.]])}, "finite"),
    ({"trajectory": np.ones((3, 3))}, "same optimized"),
    ({"provenance": {"schemaVersion": "wrong"}}, "slam.session.v1"),
    ({"provenance": {"schemaVersion": "slam.session.v1", "value": float("nan")}}, "JSON compliant"),
])
def test_snapshot_rejects_ambiguous_or_inconsistent_evidence(changes, match):
    with pytest.raises(ValueError, match=match):
        replace(valid_snapshot(), **changes)


@pytest.mark.parametrize("changes", [
    {"coordinate_frame": "map"}, {"units": "millimetres"}, {"pose_model": "se3"},
    {"observation_kind": "lidar"}, {"map_source": "invented"}, {"provider_id": ""},
])
def test_provider_rejects_unsupported_coordinate_and_sensor_contract_before_workflow_motion(tmp_path, changes):
    class WrongProvider(RecordedPlanarProvider):
        info = replace(RecordedPlanarProvider.info, **changes)
    with pytest.raises(ValueError):
        workflow_for(tmp_path, WrongProvider)


def test_snapshot_detaches_mutable_provider_arrays_and_provenance():
    source = valid_snapshot()
    cloud = np.array(source.cloud.xyz)
    odometry = np.array(source.odometry)
    provenance = {"schemaVersion": "slam.session.v1", "observations": ["original"]}
    frozen = replace(source, cloud=PointCloud(cloud), odometry=odometry, provenance=provenance)
    cloud[:] = 99
    odometry[:] = 99
    provenance["observations"].append("late")
    assert frozen.cloud.xyz[0, 0] == 0
    assert frozen.odometry[0, 0] == 0
    assert frozen.provenance["observations"] == ["original"]
    with pytest.raises(ValueError):
        frozen.sources[0] = 2


def test_invalid_snapshot_is_rejected_before_map_publication(tmp_path):
    workflow, _ = sampled_workflow(tmp_path)
    snapshot = workflow.slam.snapshot()
    workflow.slam.snapshot = lambda: replace(snapshot, sources=np.full(27, 999))
    with pytest.raises(ValueError, match="source indices"):
        workflow._build()
    assert not list(tmp_path.glob("*/manifest.json"))
    assert workflow.active is None


def test_provider_identity_cannot_change_between_acquisition_and_snapshot(tmp_path):
    workflow, _ = sampled_workflow(tmp_path)
    snapshot = workflow.slam.snapshot()
    workflow.slam.snapshot = lambda: replace(snapshot, info=replace(snapshot.info, version="other"))
    with pytest.raises(ValueError, match="matching identity"):
        workflow._build()
    assert workflow.active is None


def test_dense_adapter_preserves_original_odometry_default_and_budget():
    provider = DenseSlamProvider()
    assert provider.estimator.odometry_sigma.tolist() == [.025, .025, .015]
    assert provider.estimator.absolute_odometry_sigma is None
    assert provider.frame_count == 0 and provider.max_frames == 600
    assert provider.progress().frame_count == 0


def test_failing_new_session_factory_does_not_reserve_the_robot(tmp_path):
    calls = []
    def factory():
        calls.append("create")
        if len(calls) == 2:
            raise ValueError("driver provider unavailable")
        return RecordedPlanarProvider()
    workflow, released = workflow_for(tmp_path, factory)
    reservations = []
    workflow.reserve = lambda: reservations.append("held") or "held"
    with pytest.raises(ValueError, match="provider unavailable"):
        workflow.start({"mode": "manual"})
    assert reservations == [] and released == []
    assert workflow._reservation is None
    assert workflow.state == "idle"


def test_snapshot_measured_travel_cannot_be_replaced_by_progress_claim(tmp_path):
    workflow, _ = sampled_workflow(tmp_path)
    snapshot = workflow.slam.snapshot()
    workflow.slam.snapshot = lambda: replace(snapshot, odometry=np.zeros((3, 3)))
    with pytest.raises(ServiceError, match="0.15"):
        workflow._build()
    assert workflow.active is None


def test_provider_cannot_replace_both_live_and_snapshot_identity_mid_session(tmp_path):
    workflow, _ = sampled_workflow(tmp_path)
    workflow.slam.info = replace(workflow.slam.info, version="changed-live-provider")
    with pytest.raises(ValueError, match="matching identity"):
        workflow._build()
    workflow.capture = lambda: observation(.6)
    with pytest.raises(ValueError, match="identity changed"):
        workflow._sample()
    assert workflow.slam.frame_count == 3
    assert workflow.active is None


def test_non_dense_world_geometry_is_anchored_before_publication(tmp_path):
    workflow, _ = sampled_workflow(tmp_path)
    anchor = [2., 3., .2]
    snapshot = workflow.slam.snapshot()
    expected = PointCloud(transform(snapshot.cloud.xyz, anchor)).bounds()
    workflow._base_anchor = anchor
    workflow._build()
    directory, manifest = MapCatalog(tmp_path).open(workflow.map_id, robot_id="provider-unit",
                                                   calibration_revision="a" * 64)
    assert manifest["bounds"]["min"] == pytest.approx(expected["min"], abs=1e-6)
    assert manifest["bounds"]["max"] == pytest.approx(expected["max"], abs=1e-6)
    metadata = json.loads((directory / "slam-session.json").read_text())
    assert metadata["mapFromWorld"] == anchor


@pytest.mark.parametrize("field", ["provider_id", "version", "map_source", "coordinate_frame"])
def test_provider_identity_requires_strings(field):
    with pytest.raises(TypeError, match="must be a string"):
        replace(RecordedPlanarProvider.info, **{field: 42}).validate()


def test_dense_adapter_translates_only_retryable_sensor_shortage(monkeypatch):
    provider = DenseSlamProvider()
    def shortage(_):
        raise ValueError("可用深度点不足，请调整相机朝向或检查标定。")
    monkeypatch.setattr(provider.estimator, "add", shortage)
    with pytest.raises(InsufficientDepthError, match="深度点不足"):
        provider.add(observation())
    def malformed(_):
        raise ValueError("invalid metric RGB-D frame sizes")
    monkeypatch.setattr(provider.estimator, "add", malformed)
    with pytest.raises(ValueError, match="frame sizes") as error:
        provider.add(observation())
    assert not isinstance(error.value, InsufficientDepthError)
