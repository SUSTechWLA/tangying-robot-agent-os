"""Recorded geometry tests, not SLAM accuracy or hardware navigation claims."""
import hashlib
import itertools
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from tangying_robot_gateway.spatial_mapping import (
    RegisteredSpatialSlamProvider,
    load_spatial_map,
    read_spatial_json,
)


def provider(**changes):
    return RegisteredSpatialSlamProvider(**{
        "robot_id": "recorded-uav", "map_id": "room-volume", "map_revision": "a" * 64,
        "world_frame_revision": "world-epoch-1", "calibration_revision": "b" * 64,
        "resolution": 1., "origin": [0., 0., 0.], "shape": [5, 4, 4], **changes})


def frame(instance=None, **changes):
    instance = instance or provider()
    return {"schemaVersion": "slam.registered-frame.v1", **instance.identity,
            "sourceId": "lidar-front", "sourceType": "fixture", "registrationRevision": "external-solution-1",
            "observationId": "capture-1", "sequence": 1, "observedAtUnixMs": 1000,
            "pose": [.2, .2, .2, math.sqrt(.5), 0., math.sqrt(.5), 0.],
            "sensorOrigin": [.2, 1.2, 1.2], "points": [[3.2, 1.2, 1.2]], **changes}


def index(x, y, z):
    return (z * 4 + y) * 5 + x


def test_registered_se3_retains_tilt_altitude_and_actual_sensor_origin():
    p = provider()
    original = frame(p)
    p.add(original, now_unix_ms=1100)
    original["points"][0][0] = 4.2
    volume = p.volume()
    assert volume["cells"][index(3, 1, 1)] == 100
    assert [volume["cells"][index(x, 1, 1)] for x in range(3)] == [0, 0, 0]
    assert volume["cells"][index(0, 0, 0)] == -1, "robot pose is not the sensor optical origin"
    assert volume["cells"][index(4, 1, 1)] == -1, "space behind the measured endpoint stays unknown"
    assert p.provenance()["observations"][0]["pose"] == frame(p)["pose"]
    assert p.provenance()["provider"]["estimatesPoses"] is False


def test_later_near_surface_blocks_ray_free_carving_behind_it():
    p = provider()
    p.add(frame(p), now_unix_ms=1000)
    p.add(frame(p, sequence=2, observedAtUnixMs=1001, observationId="capture-2",
                points=[[1.2, 1.2, 1.2]]), now_unix_ms=1001)
    cells = p.volume()["cells"]
    assert cells[index(0, 1, 1)] == 0
    assert cells[index(1, 1, 1)] == cells[index(3, 1, 1)] == 100
    assert cells[index(2, 1, 1)] == -1


def test_diagonal_ray_does_not_clear_corner_adjacent_cells():
    p = provider()
    p.add(frame(p, sensorOrigin=[.2, .2, .2], points=[[2.2, 2.2, 2.2]]), now_unix_ms=1000)
    cells = p.volume()["cells"]
    assert cells[index(0, 0, 0)] == cells[index(1, 1, 1)] == 0
    assert cells[index(1, 0, 0)] == cells[index(0, 1, 0)] == -1
    assert cells[index(2, 2, 2)] == 100


@pytest.mark.parametrize("origin,end,free,zero_length", [
    ([2., 1.2, 1.2], [.2, 1.2, 1.2], (1, 1, 1), (2, 1, 1)),
    ([2., 2., 1.2], [.2, .2, 1.2], (1, 1, 1), (2, 2, 1)),
    ([2., 2., 2.], [.2, .2, .2], (1, 1, 1), (2, 2, 2)),
])
def test_negative_ray_starting_at_face_edge_corner_skips_zero_length_cell(origin, end, free, zero_length):
    p = provider()
    p.add(frame(p, sensorOrigin=origin, points=[end]), now_unix_ms=1000)
    cells = p.volume()["cells"]
    assert cells[index(*free)] == 0
    assert cells[index(*zero_length)] == -1
    assert cells[index(*(math.floor(v) for v in end))] == 100


def test_same_cell_hit_and_face_parallel_ray_do_not_invent_free_space():
    p = provider()
    p.add(frame(p, sensorOrigin=[1., 1.2, 1.2], points=[[1.4, 1.2, 1.2]]), now_unix_ms=1000)
    assert p.volume()["cells"].count(0) == 0
    p = provider()
    p.add(frame(p, sensorOrigin=[.2, 1., 1.2], points=[[3.2, 1., 1.2]]), now_unix_ms=1000)
    assert p.volume()["cells"].count(0) == 0
    assert p.volume()["cells"][index(3, 1, 1)] == 100


def test_identity_and_geometry_are_detached_and_profile_identifiers_are_accepted():
    p = provider(calibration_revision="calibration-v1", map_revision="external-map-v1")
    p.identity["worldFrameRevision"] = "modified"
    p.origin[0] = 100
    p.shape[0] = 1
    assert p.identity["worldFrameRevision"] == "world-epoch-1"
    assert p.origin == [0., 0., 0.] and p.shape == [5, 4, 4]
    p.add(frame(p), now_unix_ms=1000)
    assert p.volume()["calibrationRevision"] == "calibration-v1"


@pytest.mark.parametrize("changes", [
    {"robotId": "different"}, {"mapRevision": "c" * 64}, {"worldFrameRevision": "epoch-2"},
    {"calibrationRevision": "c" * 64}, {"frameId": "map"}, {"sensorOrigin": [5., 0., 0.]},
    {"points": [[3., 1., float("nan")]]}, {"points": [[3., 1., 4.]]}, {"points": []},
    {"pose": [0., 0., 0., 1., 0., 1., 0.]}, {"sequence": True}, {"sourceType": "unregistered"},
    {"observedAtUnixMs": 1201}, {"observedAtUnixMs": 1},
])
def test_rejects_bad_basis_pose_unbounded_and_stale_measurements(changes):
    p = provider(max_measurement_age_ms=200)
    with pytest.raises(ValueError):
        p.add(frame(p, **changes), now_unix_ms=1200)
    assert p.frame_count == 0


@pytest.mark.parametrize("changes", [
    {"sequence": 1, "observedAtUnixMs": 1001, "observationId": "capture-2"},
    {"sequence": 2, "observedAtUnixMs": 1000, "observationId": "capture-2"},
    {"sequence": 2, "observedAtUnixMs": 1001},
    {"sequence": 2, "observedAtUnixMs": 1001, "observationId": "capture-2", "registrationRevision": "changed"},
])
def test_rejects_replay_and_basis_drift_without_mutating_history(changes):
    p = provider()
    p.add(frame(p), now_unix_ms=1000)
    before = p.measurements()
    with pytest.raises(ValueError):
        p.add(frame(p, **changes), now_unix_ms=1001)
    assert before == p.measurements()


def test_budgets_include_derived_ray_work_and_empty_history():
    with pytest.raises(ValueError, match="cell budget"):
        provider(shape=[100, 100, 100])
    p = provider(max_points=1)
    with pytest.raises(ValueError, match="budget"):
        p.add(frame(p, points=[[3., 1., 1.], [2., 1., 1.]]), now_unix_ms=1000)
    with pytest.raises(ValueError, match="without registered"):
        p.volume()
    p = provider(max_ray_cells=1)
    p.add(frame(p), now_unix_ms=1000)
    with pytest.raises(ValueError, match="computation budget"):
        p.volume()


def test_package_roundtrip_restore_atomic_repeat_and_immutable_revision(tmp_path):
    p = provider()
    p.add(frame(p), now_unix_ms=1000)
    destination = tmp_path / "package"
    manifest = p.publish(destination)
    loaded = load_spatial_map(destination)
    assert loaded["volume"] == p.volume()
    assert loaded["provenance"] == p.provenance()
    restored = RegisteredSpatialSlamProvider.restore(loaded["measurements"])
    assert restored.publish(destination) == manifest
    restored.add(frame(p, sequence=2, observationId="capture-2", observedAtUnixMs=1001), now_unix_ms=1001)
    with pytest.raises(FileExistsError, match="immutable"):
        restored.publish(destination)
    assert load_spatial_map(destination)["manifest"] == manifest
    assert sorted(item.name for item in tmp_path.iterdir()) == ["package"]


def test_package_rejects_hash_tampering_and_even_rehashed_false_free_evidence(tmp_path):
    p = provider()
    p.add(frame(p), now_unix_ms=1000)
    p.publish(tmp_path / "package")
    target = tmp_path / "package" / "volume.json"
    target.write_text(target.read_text() + " ")
    with pytest.raises(ValueError, match="bounded|budget|hash"):
        load_spatial_map(tmp_path / "package")
    volume = p.volume()
    volume["cells"][index(4, 1, 1)] = 0
    payload = json.dumps(volume).encode()
    target.write_bytes(payload)
    manifest_path = target.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["volume"].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    del manifest["revision"]
    manifest["revision"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                                                    ensure_ascii=False).encode()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="measured provenance"):
        load_spatial_map(target.parent)


def test_package_rejects_symlink_and_publication_lock(tmp_path):
    p = provider()
    p.add(frame(p), now_unix_ms=1000)
    p.publish(tmp_path / "package")
    (tmp_path / "linked").symlink_to(tmp_path / "package", target_is_directory=True)
    with pytest.raises(ValueError, match="regular local directory"):
        load_spatial_map(tmp_path / "linked")
    (tmp_path / ".pending.publication.lock").touch()
    with pytest.raises(FileExistsError):
        p.publish(tmp_path / "pending")
    assert not (tmp_path / "pending").exists()


def test_cli_offline_replay_requires_explicit_time_basis_and_builds_verified_package(tmp_path):
    p = provider()
    p.add(frame(p), now_unix_ms=1000)
    source = tmp_path / "frames.json"
    source.write_text(json.dumps(p.measurements()))
    script = Path(__file__).resolve().parents[3] / "scripts" / "build_spatial_map.py"
    command = [sys.executable, str(script), "--input", str(source), "--output", str(tmp_path / "package")]
    rejected = subprocess.run(command, text=True, capture_output=True, check=False)
    assert rejected.returncode == 2 and "stale" in rejected.stderr
    completed = subprocess.run(command + ["--reference-time-unix-ms", "1000"], text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["hardwareValidated"] is False
    assert load_spatial_map(tmp_path / "package")["volume"] == p.volume()


def test_duplicate_json_keys_rejected_before_cli_publication(tmp_path):
    source = tmp_path / "frames.json"
    source.write_text('{"schemaVersion":"first","schemaVersion":"second"}')
    script = Path(__file__).resolve().parents[3] / "scripts" / "build_spatial_map.py"
    completed = subprocess.run([sys.executable, str(script), "--input", str(source), "--output", str(tmp_path / "package")],
                               text=True, capture_output=True, check=False)
    assert completed.returncode == 2 and "duplicate" in completed.stderr
    assert not (tmp_path / "package").exists()


def test_regular_file_input_budget_and_link_guard(tmp_path):
    target = tmp_path / "input.json"
    target.write_text('{"value":12345}')
    with pytest.raises(ValueError, match="bounded"):
        read_spatial_json(target, maximum=4)
    link = tmp_path / "linked.json"
    link.symlink_to(target)
    with pytest.raises(OSError):
        read_spatial_json(link)


def test_registered_room_rays_publish_reload_and_plan_altitude_changing_route(tmp_path):
    from tangying_robot_gateway.spatial_navigation import SpatialPathPlanner

    p = provider(shape=[11, 11, 7])
    points = [[x + .5, y + .5, z + .5] for x, y, z in itertools.product(range(11), range(11), range(7))
              if x in (0, 10) or y in (0, 10) or z in (0, 6)]
    p.add(frame(p, pose=[5.5, 5.5, 3.5, 1., 0., 0., 0.], sensorOrigin=[5.5, 5.5, 3.5], points=points), now_unix_ms=1000)
    p.publish(tmp_path / "recorded-room")
    volume = load_spatial_map(tmp_path / "recorded-room")["volume"]
    assert volume["cells"].count(0) == 405
    assert volume["cells"].count(100) == 442
    planner = SpatialPathPlanner(volume, {"mode": "volume3d", "bodyHalfExtents": [.1, .1, .1],
                                         "margin": .01, "maxExpanded": 1000, "maxWaypoints": 128})
    start, goal = [2.5, 5.5, 2.5, 1., 0., 0., 0.], [7.5, 5.5, 4.5, 1., 0., 0., 0.]
    planned = planner.plan(start, goal)
    assert planned["waypoints"][0] == start and planned["waypoints"][-1] == goal
    assert planned["mapRevision"] == p.identity["mapRevision"]
