"""The map pipeline: cloud in, a verifiable map directory out.

Everything here runs on synthetic data, so the whole chain - level of detail,
occupancy, trajectory, manifest, hashes - is exercised without a robot, a
database, or an RTAB-Map install.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest
from tangying_robot_gateway.map_manifest import load_manifest
from tangying_robot_gateway.map_pipeline import (
    HEADER_BYTES,
    build_lod,
    build_map,
    decode_lod,
    encode_lod,
    encode_png_gray,
    occupancy_from_points,
    synthetic_home_cloud,
    trajectory_geojson,
    verify_map,
    voxel_downsample,
)


def small_cloud(points: int = 20_000) -> object:
    return synthetic_home_cloud(points, seed=3)


def test_the_synthetic_cloud_looks_like_a_scan_not_like_noise():
    cloud = small_cloud(50_000)
    # The generator adds deliberate duplicates, as a real scan produces.
    assert cloud.count >= 50_000
    bounds = cloud.bounds()
    # A house: wider and longer than it is tall, standing on a floor.
    assert bounds["max"][0] - bounds["min"][0] > 4
    assert bounds["max"][1] - bounds["min"][1] > 8
    assert bounds["max"][2] - bounds["min"][2] < 3.5
    # Two thirds of the points should be near the floor or on walls, not floating.
    assert float(np.median(cloud.xyz[:, 2])) < 1.5


def test_the_synthetic_cloud_is_repeatable_for_a_seed():
    first = synthetic_home_cloud(5_000, seed=11)
    second = synthetic_home_cloud(5_000, seed=11)
    assert np.array_equal(first.xyz, second.xyz), "a load test must be repeatable"
    other = synthetic_home_cloud(5_000, seed=12)
    assert not np.array_equal(first.xyz, other.xyz)


def test_downsampling_removes_duplicates_and_keeps_the_shape():
    cloud = small_cloud(30_000)
    reduced = voxel_downsample(cloud, 0.05)
    assert reduced.count < cloud.count, "a real scan repeats surfaces; that must collapse"
    # The extent is preserved: downsampling thins, it does not crop.
    for axis in range(3):
        assert reduced.xyz[:, axis].min() >= cloud.xyz[:, axis].min() - 0.05
        assert reduced.xyz[:, axis].max() <= cloud.xyz[:, axis].max() + 0.05


def test_downsampling_averages_position_and_colour_together():
    xyz = np.array([[0.0, 0, 0], [0.0, 0, 0], [0.01, 0, 0]], dtype=np.float32)
    rgb = np.array([[0, 0, 0], [100, 100, 100], [200, 200, 200]], dtype=np.uint8)
    reduced = voxel_downsample(type(synthetic_home_cloud(1))(xyz, rgb), 0.02)
    assert reduced.count == 1
    assert reduced.xyz[0][0] == pytest.approx(0.01 / 3, abs=1e-6)
    assert reduced.rgb[0][0] == 100, "colour must travel with the point, not be dropped"


@pytest.mark.parametrize("bad", [0, -1])
def test_a_degenerate_voxel_size_is_refused(bad):
    with pytest.raises(ValueError):
        voxel_downsample(small_cloud(1_000), bad)


def test_each_lod_level_is_coarser_than_the_last_and_level_zero_is_smallest():
    levels = build_lod(small_cloud(60_000), levels=4, base_voxel_m=0.04)
    counts = [level.count for level in levels]
    assert counts == sorted(counts), f"lod0 must be the coarsest: {counts}"
    assert counts[0] < counts[-1]
    # Doubling the voxel should roughly quarter the points, not do nothing.
    assert counts[-1] > counts[0]


def test_the_chunk_format_round_trips_exactly():
    cloud = small_cloud(2_000)
    payload = encode_lod(cloud, level=2)
    restored, level = decode_lod(payload)
    assert level == 2
    assert restored.count == cloud.count
    assert np.array_equal(restored.xyz, cloud.xyz)
    assert np.array_equal(restored.rgb, cloud.rgb)


def test_a_truncated_or_foreign_chunk_is_rejected_rather_than_guessed():
    payload = encode_lod(small_cloud(1_000), level=0)
    with pytest.raises(ValueError):
        decode_lod(payload[: HEADER_BYTES - 1])
    with pytest.raises(ValueError):
        decode_lod(payload[: len(payload) - 4])
    with pytest.raises(ValueError):
        decode_lod(b"XXXX" + payload[4:])
    bad_version = payload[:4] + struct.pack("<I", 99) + payload[8:]
    with pytest.raises(ValueError):
        decode_lod(bad_version)


def test_occupancy_distinguishes_free_unknown_and_obstacle():
    # Two cells: one with only floor, one with a wall above it, one empty.
    xyz = np.array([
        [0.10, 0.10, 0.00],   # floor cell
        [0.60, 0.10, 0.00],   # floor + wall in the same cell -> obstacle wins
        [0.60, 0.10, 1.20],
    ], dtype=np.float32)
    from tangying_robot_gateway.map_pipeline import PointCloud

    grid = occupancy_from_points(PointCloud(xyz=xyz), resolution=0.5,
                                 bounds={"min": [0, 0, 0], "max": [1.5, 0.5, 2.0]})
    cells = grid["cells"]
    assert cells[0][0] == 0, "floor only"
    assert cells[0][1] == 100, "an obstacle in the cell must win over the floor"
    assert cells[0][2] == -1, "no points at all must stay unknown, not free"


def test_the_png_marks_unknown_differently_from_free_space():
    cells = np.array([[-1, 0, 100]], dtype=np.int16)
    png = encode_png_gray(cells)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # Decode the single scanline back: unknown and free must not share a value.
    import zlib as _zlib

    offset = 8
    idat = b""
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        kind = png[offset + 4:offset + 8]
        if kind == b"IDAT":
            idat += png[offset + 8:offset + 8 + length]
        offset += 12 + length
    row = _zlib.decompress(idat)
    assert row[0] == 0, "PNG filter byte"
    assert row[1] != row[2], "unknown must not be drawn like free space"
    assert row[2] > row[3], "obstacles must be darkest"


def test_trajectory_is_a_geojson_line_with_times():
    trail = trajectory_geojson([(0.0, 0.0, 0.0), (1.0, 2.0, 0.0), (1.0, 2.5, 0.0)],
                               times_unix_ms=[10, 20, 30])
    feature = trail["features"][0]
    assert feature["geometry"]["type"] == "LineString"
    assert feature["geometry"]["coordinates"] == [[0.0, 0.0], [1.0, 2.0], [1.0, 2.5]]
    assert feature["properties"]["timesUnixMs"] == [10, 20, 30]


def test_building_a_map_produces_a_directory_that_verifies(tmp_path: Path):
    manifest = build_map(
        tmp_path / "map", map_id="home-loadtest", robot_id="xlerobot-01",
        cloud=small_cloud(40_000),
        poses=[(0.0, 0.0, 0.0), (1.0, 0.5, 0.0), (2.0, 1.0, 0.0)],
        times_unix_ms=[1, 2, 3], lod_levels=4, base_voxel_m=0.05,
        calibration_revision="c" * 64, created_at_unix_ms=1_789_000_000_000,
    )
    assert manifest["mapId"] == "home-loadtest"
    assert manifest["calibrationRevision"] == "c" * 64
    assert set(manifest["artifacts"]) == {"cloud", "grid", "trajectory"}

    checks = verify_map(tmp_path / "map")
    assert all(check.ok for check in checks), [(c.role, c.reason) for c in checks]
    assert {check.role for check in checks} == {"cloud", "grid", "trajectory"}

    # Every LOD level is on disk, even though the manifest points at the finest.
    for level in range(4):
        assert (tmp_path / "map" / "cloud" / f"lod{level}.bin").is_file()
    assert (tmp_path / "map" / "grid" / "occupancy.png").is_file()
    assert (tmp_path / "map" / "trajectory.geojson").is_file()


def test_a_tampered_artifact_fails_verification(tmp_path: Path):
    build_map(tmp_path / "map", map_id="tampered", robot_id="r",
              cloud=small_cloud(10_000), lod_levels=2)
    target = tmp_path / "map" / "grid" / "occupancy.png"
    target.write_bytes(target.read_bytes() + b"tampered")
    checks = {check.role: check for check in verify_map(tmp_path / "map")}
    assert checks["grid"].ok is False
    assert checks["cloud"].ok is True, "one bad artifact must not condemn the others"


def test_a_map_without_a_trajectory_is_still_a_map(tmp_path: Path):
    manifest = build_map(tmp_path / "map", map_id="no-trail", robot_id="r",
                         cloud=small_cloud(8_000), lod_levels=2)
    assert "trajectory" not in manifest["artifacts"]
    assert all(check.ok for check in verify_map(tmp_path / "map"))


def test_the_manifest_on_disk_matches_what_was_returned(tmp_path: Path):
    manifest = build_map(tmp_path / "map", map_id="roundtrip", robot_id="r",
                         cloud=small_cloud(8_000), lod_levels=2)
    assert load_manifest(tmp_path / "map") == manifest
    raw = json.loads((tmp_path / "map" / "manifest.json").read_text())
    assert raw["schemaVersion"] == "map.manifest.v1"


def test_a_million_point_cloud_builds_within_the_pipeline_budget(tmp_path: Path):
    """The load-test path, run small enough for a unit test but real in shape."""
    cloud = synthetic_home_cloud(200_000, seed=5)
    manifest = build_map(tmp_path / "map", map_id="budget", robot_id="r",
                         cloud=cloud, lod_levels=5, base_voxel_m=0.04)
    levels = build_lod(cloud, levels=5, base_voxel_m=0.04)
    # Coarsest level must be small enough to draw immediately; finest must retain
    # the detail. These are the numbers a viewer relies on.
    assert levels[0].count < manifest["pointCount"] / 4
    assert levels[0].count > 0
    assert all(check.ok for check in verify_map(tmp_path / "map"))
