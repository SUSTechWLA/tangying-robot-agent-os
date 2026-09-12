"""The map manifest: what a built map is, and how to prove it is intact."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tangying_robot_gateway.map_manifest import (
    SCHEMA_VERSION,
    MapManifestError,
    build_manifest,
    file_sha256,
    load_manifest,
    manifest_hash,
    save_manifest,
    validate_manifest,
    verify_artifacts,
)

DIGEST = "a" * 64


def artifacts() -> dict:
    return {
        "cloud": {"href": "cloud/0.copc.laz", "bytes": 12, "sha256": DIGEST},
        "grid": {"href": "grid/occupancy.png", "bytes": 4, "sha256": DIGEST},
    }


def manifest(**overrides) -> dict:
    fields = {
        "map_id": "home-2026-09-12T10-31-00Z",
        "robot_id": "xlerobot-01",
        "source": "rtabmap",
        "mode": "mapping",
        "artifacts": artifacts(),
        "bounds": {"min": [-4.0, -2.0, -0.2], "max": [6.0, 8.0, 2.4]},
        "point_count": 4_823_910,
        "lod_levels": 5,
        "floors": [{"id": "ground", "zMin": -0.2, "zMax": 2.4}],
        "created_at_unix_ms": 1_789_000_000_000,
        "calibration_revision": "b" * 64,
    }
    fields.update(overrides)
    return build_manifest(**fields)


def test_a_complete_manifest_validates_and_carries_a_content_hash():
    document = manifest()
    assert document["schemaVersion"] == SCHEMA_VERSION
    assert len(document["hash"]) == 64
    assert document["hash"] == manifest_hash(document)
    assert set(document["artifacts"]) == {"cloud", "grid"}
    assert document["pointCount"] == 4_823_910
    assert document["calibrationRevision"] == "b" * 64


def test_the_map_id_must_be_one_safe_path_segment():
    # mapId is a directory name and a URL segment at once, so anything that can
    # address a parent directory is a traversal, not a map.
    for bad in ("../home", "home/../../etc", "/abs/path", "home/2026", "", ".hidden", "a" * 200):
        with pytest.raises(MapManifestError) as failure:
            manifest(map_id=bad)
        assert failure.value.code in {"INVALID_MAP_ID", "INVALID_TYPE"}


def test_an_artifact_href_cannot_escape_the_map_directory():
    for bad in ("/etc/passwd", "../other/cloud.laz", "cloud/../../x", "a//b"):
        broken = artifacts()
        broken["cloud"] = {"href": bad, "bytes": 1, "sha256": DIGEST}
        with pytest.raises(MapManifestError) as failure:
            manifest(artifacts=broken)
        assert failure.value.code == "INVALID_HREF"


def test_a_map_without_geometry_or_occupancy_is_not_a_map():
    with pytest.raises(MapManifestError) as failure:
        manifest(artifacts={"cloud": {"href": "c.laz", "bytes": 1, "sha256": DIGEST}})
    assert failure.value.code == "MISSING_FIELD"
    assert "grid" in failure.value.message


def test_unknown_fields_are_rejected_so_a_typo_cannot_do_nothing():
    document = manifest()
    document["pointcount"] = 5          # wrong case
    with pytest.raises(MapManifestError) as failure:
        validate_manifest(document)
    assert failure.value.code == "UNKNOWN_FIELD"


def test_editing_a_manifest_is_detected_by_its_own_hash():
    document = manifest()
    document["pointCount"] = 1
    with pytest.raises(MapManifestError) as failure:
        validate_manifest(document)
    assert failure.value.code == "HASH_MISMATCH"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"source": "photogrammetry"}, "INVALID_VALUE"),
        ({"mode": "explore"}, "INVALID_VALUE"),
        ({"floors": []}, "MISSING_FIELD"),
        ({"bounds": {"min": [0.0, 0.0, 0.0], "max": [0.0, 1.0, 1.0]}}, "OUT_OF_RANGE"),
        ({"point_count": -1}, "OUT_OF_RANGE"),
        ({"lod_levels": 0}, "OUT_OF_RANGE"),
        ({"calibration_revision": "short"}, "INVALID_HASH"),
    ],
)
def test_invalid_values_are_named(overrides, code):
    with pytest.raises(MapManifestError) as failure:
        manifest(**overrides)
    assert failure.value.code == code


def test_two_floors_with_the_same_id_are_rejected():
    with pytest.raises(MapManifestError) as failure:
        manifest(floors=[{"id": "ground", "zMin": 0, "zMax": 2},
                         {"id": "ground", "zMin": 2, "zMax": 4}])
    assert failure.value.code == "DUPLICATE_ID"


def test_saving_and_loading_round_trips(tmp_path: Path):
    document = save_manifest(tmp_path, manifest())
    assert (tmp_path / "manifest.json").is_file()
    assert load_manifest(tmp_path) == document
    assert not list(tmp_path.glob("*.tmp")), "no temporary file may survive a save"


def test_loading_reports_a_missing_or_truncated_manifest(tmp_path: Path):
    with pytest.raises(MapManifestError) as failure:
        load_manifest(tmp_path)
    assert failure.value.code == "NOT_FOUND"

    save_manifest(tmp_path, manifest())
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({**json.loads(path.read_text()), "pointCount": 7}))
    with pytest.raises(MapManifestError) as failure:
        load_manifest(tmp_path)
    assert failure.value.code == "HASH_MISMATCH"


def test_artifact_verification_reports_every_role_instead_of_stopping_at_the_first(tmp_path: Path):
    # A caller wants to know "the grid is fine but the cloud is truncated", not
    # just that something is wrong.
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    (cloud / "0.copc.laz").write_bytes(b"0123456789ab")
    grid = tmp_path / "grid"
    grid.mkdir()
    (grid / "occupancy.png").write_bytes(b"png!")

    document = manifest(artifacts={
        "cloud": {"href": "cloud/0.copc.laz", "bytes": 12, "sha256": file_sha256(cloud / "0.copc.laz")},
        "grid": {"href": "grid/occupancy.png", "bytes": 999, "sha256": DIGEST},
    })
    checks = {check.role: check for check in verify_artifacts(document, tmp_path)}
    assert checks["cloud"].ok is True
    assert checks["grid"].ok is False
    assert "大小不符" in checks["grid"].reason


def test_a_corrupted_artifact_is_caught_even_when_its_size_matches(tmp_path: Path):
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    (cloud / "0.copc.laz").write_bytes(b"0123456789ab")
    grid = tmp_path / "grid"
    grid.mkdir()
    (grid / "occupancy.png").write_bytes(b"png!")
    document = manifest(artifacts={
        "cloud": {"href": "cloud/0.copc.laz", "bytes": 12, "sha256": DIGEST},
        "grid": {"href": "grid/occupancy.png", "bytes": 4, "sha256": file_sha256(grid / "occupancy.png")},
    })
    checks = {check.role: check for check in verify_artifacts(document, tmp_path)}
    assert checks["cloud"].ok is False and checks["cloud"].reason == "内容哈希不符"
    assert checks["grid"].ok is True


def test_a_missing_artifact_is_reported_as_missing(tmp_path: Path):
    document = manifest(artifacts={
        "cloud": {"href": "cloud/0.copc.laz", "bytes": 12, "sha256": DIGEST},
        "grid": {"href": "grid/occupancy.png", "bytes": 4, "sha256": DIGEST},
    })
    checks = verify_artifacts(document, tmp_path)
    assert all(not check.ok for check in checks)
    assert all(check.reason == "文件不存在" for check in checks)


def test_an_optional_artifact_may_be_declared_without_being_required():
    document = manifest(artifacts={**artifacts(),
                                   "trajectory": {"href": "trajectory.geojson", "bytes": 10, "sha256": DIGEST}})
    assert "trajectory" in document["artifacts"]
    with pytest.raises(MapManifestError) as failure:
        manifest(artifacts={**artifacts(), "thumbnails": {"href": "t.png", "bytes": 1, "sha256": DIGEST}})
    assert failure.value.code == "UNKNOWN_FIELD"
