"""CPU contract tests for ROS2 acquisition policy, without mocking ROS transport."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("spatial_mapping_node_under_test",
    ROOT / "robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/spatial_mapping_node.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def config(tmp_path, **changes):
    return {"schemaVersion": "slam.spatial-mapper.v1", "robotId": "r1", "mapId": "room", "mapRevision": "r1",
            "worldFrameRevision": "epoch1", "calibrationRevision": "cal1", "resolution": 1,
            "origin": [0, 0, 0], "shape": [5, 4, 3], "sourceIds": ["recorded-lidar"],
            "outputDirectory": str(tmp_path / "new-package"), **changes}


def frame(session, **changes):
    return {"schemaVersion": "slam.registered-frame.v1", **session.provider.identity,
            "sourceId": "recorded-lidar", "sourceType": "fixture", "registrationRevision": "reg1",
            "observationId": "o1", "sequence": 1, "observedAtUnixMs": 1000,
            "pose": [.2, .2, .2, 1, 0, 0, 0], "sensorOrigin": [.2, .2, .2],
            "points": [[3.2, .2, .2]], **changes}


def test_stream_session_saves_freezes_and_replays_save_ack_without_later_mutation(tmp_path):
    session = MODULE.SpatialMappingSession(config(tmp_path))
    session.accept(json.dumps(frame(session)), now_unix_ms=1000)
    first = session.save()
    assert session.status()["state"] == "saved"
    assert session.save() == first
    assert not session.accept(json.dumps(frame(session, sequence=2, observationId="o2", observedAtUnixMs=1001)), now_unix_ms=1001)
    assert session.status()["acceptedFrames"] == 1
    with pytest.raises(FileExistsError):
        MODULE.SpatialMappingSession(config(tmp_path))


def test_rejected_foreign_source_does_not_poison_next_good_measurement(tmp_path):
    session = MODULE.SpatialMappingSession(config(tmp_path))
    with pytest.raises(ValueError, match="commissioned"):
        session.accept(json.dumps(frame(session, sourceId="foreign")), now_unix_ms=1000)
    assert session.accept(json.dumps(frame(session)), now_unix_ms=1000)
    assert session.status()["acceptedFrames"] == session.status()["rejectedFrames"] == 1


@pytest.mark.parametrize("payload", ['{"sourceId":"a","sourceId":"b"}', 'NaN', '[]', 'x' * 101])
def test_malformed_and_byte_budget_reject_and_fault_at_bounded_rejection_limit(tmp_path, payload):
    session = MODULE.SpatialMappingSession(config(tmp_path, maxRejected=1, maxFrameBytes=100))
    with pytest.raises(ValueError):
        session.accept(payload, now_unix_ms=1000)
    assert session.status()["state"] == "faulted"
    assert not session.accept(payload, now_unix_ms=1000)
    assert session.status()["messages"] == 1
    with pytest.raises(ValueError, match="faulted"):
        session.save()


def test_total_message_budget_stops_flood_and_save_failure_freezes_snapshot(tmp_path, monkeypatch):
    session = MODULE.SpatialMappingSession(config(tmp_path, maxMessages=1, maxRejected=1))
    session.accept(json.dumps(frame(session)), now_unix_ms=1000)
    with pytest.raises(ValueError, match="message budget"):
        session.accept(json.dumps(frame(session)), now_unix_ms=1000)
    assert session.status()["state"] == "faulted"
    session = MODULE.SpatialMappingSession(config(tmp_path))
    session.accept(json.dumps(frame(session)), now_unix_ms=1000)
    original = session.provider.publish
    def unavailable(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(session.provider, "publish", unavailable)
    with pytest.raises(OSError):
        session.save()
    assert session.status()["state"] == "save_failed"
    assert not session.accept(json.dumps(frame(session)), now_unix_ms=1000)
    monkeypatch.setattr(session.provider, "publish", original)
    assert session.save()["revision"]


@pytest.mark.parametrize("changes", [
    {"sourceIds": []}, {"sourceIds": ["a", "a"]}, {"sourceIds": ["bad source"]},
    {"outputDirectory": "relative"}, {"maxFrameBytes": True}, {"maxMessages": 0},
    {"frameTopic": "topic with space"}, {"namespace": "relative"}, {"nodeName": "bad/name"},
    {"saveService": "same", "statusService": "same"}, {"unknownOption": True},
])
def test_invalid_stream_configuration_cannot_start(tmp_path, changes):
    with pytest.raises((ValueError, TypeError)):
        MODULE.SpatialMappingSession(config(tmp_path, **changes))
