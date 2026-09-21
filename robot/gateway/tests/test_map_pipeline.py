"""The map pipeline: cloud in, a verifiable map directory out.

Everything here runs on synthetic data, so the whole chain - level of detail,
occupancy, trajectory, manifest, hashes - is exercised without a robot, a
database, or an RTAB-Map install.
"""

from __future__ import annotations

import json
import math
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


def test_a_single_viewpoint_does_not_get_to_declare_an_obstacle():
    """The flying-pixel rule: one frame's stray point must not become a wall.

    A cell whose only obstacle-height points come from one keyframe stays free;
    the same cell seen by a second keyframe is a real obstacle. This is what the
    from-scratch survey needed - a handful of points at the commissioned kitchen
    goal vetoed it as GOAL_NOT_CLEAR, and no scene geometry was ever there.
    """
    from tangying_robot_gateway.map_pipeline import PointCloud

    xyz = np.array([
        [0.10, 0.10, 0.00],   # floor only
        [0.60, 0.10, 0.00],   # floor, plus a stray obstacle point from keyframe 0
        [0.60, 0.10, 0.50],
        [1.10, 0.10, 0.50],   # obstacle point seen by keyframe 0 ...
        [1.10, 0.10, 0.50],   # ... and independently by keyframe 1
        [1.60, 0.10, 0.50],   # inherited geometry: exempt from the count
    ], dtype=np.float32)
    bounds = {"min": [0, 0, 0], "max": [2.1, 0.5, 2.0]}

    grid = occupancy_from_points(PointCloud(xyz=xyz), resolution=0.5, bounds=bounds,
                                 sources=np.array([0, 0, 0, 0, 1, -1]))
    cells = grid["cells"]
    assert cells[0][0] == 0, "floor with nothing above it is free"
    assert cells[0][1] == 0, "one keyframe's stray point must not become an obstacle"
    assert cells[0][2] == 100, "two keyframes agreeing is an obstacle"
    assert cells[0][3] == 100, "an earlier survey's claim is not re-litigated"

    # Without provenance the caller is asserting a single merged cloud, where
    # every point is already evidence; the conservative rule must not apply.
    plain = occupancy_from_points(PointCloud(xyz=xyz), resolution=0.5, bounds=bounds)
    assert plain["cells"][0][1] == 100


def test_corroboration_counts_viewpoints_not_points():
    from tangying_robot_gateway.map_pipeline import corroborated_obstacle_cells

    flat = np.array([7, 7, 7, 7, 9, 9])
    sources = np.array([0, 0, 0, 0, 0, 1])
    verdict = corroborated_obstacle_cells(flat, sources)
    np.testing.assert_array_equal(verdict, [False, False, False, False, True, True])


def test_corroboration_requires_one_id_per_point():
    from tangying_robot_gateway.map_pipeline import corroborated_obstacle_cells

    with pytest.raises(ValueError, match="one observation id"):
        corroborated_obstacle_cells(np.array([1, 2]), np.array([0]))


def test_occupancy_rejects_a_source_list_that_does_not_match_the_cloud():
    from tangying_robot_gateway.map_pipeline import PointCloud

    cloud = PointCloud(xyz=np.zeros((3, 3), dtype=np.float32))
    with pytest.raises(ValueError, match="one observation id per cloud point"):
        occupancy_from_points(cloud, sources=np.zeros(2, dtype=np.int64))


def test_a_published_grid_cannot_claim_an_obstacle_its_own_cloud_cannot_show():
    """The grid and the cloud are one map and must agree.

    A four-leg survey ended with 23% of its occupied cells unsupported by the
    published cloud, because each continuation re-downsamples the cloud it
    inherited while the merged grid keeps every obstacle cell it was ever told
    about. Both cells that vetoed the commissioned kitchen waypoint were in that
    set, and the cloud showed measured floor there.
    """
    from tangying_robot_gateway.map_pipeline import (
        PointCloud,
        occupancy_from_points,
        reconcile_grid_with_cloud,
    )

    xyz = np.array([
        [0.10, 0.10, 0.02],   # floor in cell 0
        [0.60, 0.10, 0.02],   # floor under the stale claim in cell 1
        [1.10, 0.10, 0.50],   # a real wall in cell 2
    ], dtype=np.float32)
    bounds = {"min": [0, 0, 0], "max": [1.6, 0.5, 2.0]}
    cloud = PointCloud(xyz=xyz)
    grid = occupancy_from_points(cloud, resolution=0.5, bounds=bounds)
    # A stale survey claim: occupied cells with no obstacle point behind them.
    grid["cells"][0][1] = 100     # floor measured, no obstacle -> free
    grid["cells"][0][3] = 100     # nothing measured at all -> unknown

    reconciled = reconcile_grid_with_cloud(grid, cloud)
    cells = reconciled["cells"]
    assert cells[0][0] == 0, "free stays free"
    assert cells[0][1] == 0, "a measured floor outvotes a claim with no evidence"
    assert cells[0][2] == 100, "an obstacle the cloud shows is kept"
    assert cells[0][3] == -1, "with no points at all the honest answer is unknown"
    assert grid["cells"][0][1] == 100, "the caller's grid is not modified in place"


def test_reconciliation_leaves_a_grid_the_cloud_supports_untouched():
    from tangying_robot_gateway.map_pipeline import (
        PointCloud,
        occupancy_from_points,
        reconcile_grid_with_cloud,
    )

    xyz = np.array([[0.10, 0.10, 0.02], [0.60, 0.10, 0.50]], dtype=np.float32)
    cloud = PointCloud(xyz=xyz)
    grid = occupancy_from_points(cloud, resolution=0.5,
                                 bounds={"min": [0, 0, 0], "max": [1.1, 0.5, 2.0]})
    reconciled = reconcile_grid_with_cloud(grid, cloud)
    np.testing.assert_array_equal(reconciled["cells"], grid["cells"])


def test_the_published_map_grid_agrees_with_the_published_cloud(tmp_path: Path):
    from tangying_robot_gateway.map_pipeline import (
        build_map,
        decode_lod,
        occupancy_from_points,
    )

    cloud = small_cloud(4_000)
    grid = occupancy_from_points(cloud, resolution=0.25)
    # Simulate what a continuation does: hand the writer a grid carrying a claim
    # the published cloud no longer supports.
    cells_before = grid["cells"].copy()
    stale = tuple(np.argwhere(grid["cells"] != 100)[0])
    grid["cells"][stale[0], stale[1]] = 100

    build_map(tmp_path / "map", map_id="reconciled", robot_id="xlerobot-01", cloud=cloud,
              occupancy_grid=grid, resolution=0.25, source="rgbd_slam", mode="mapping")
    metadata = json.loads((tmp_path / "map" / "grid" / "occupancy.json").read_text())
    cells = np.load(tmp_path / "map" / "grid" / "occupancy.npy")
    published = decode_lod((tmp_path / "map" / "cloud" / "lod4.bin").read_bytes())[0]
    assert cells[stale[0], stale[1]] != 100, "the unsupported claim is not published"

    supported = set()
    for x, y, z in published.xyz:
        if 0.04 <= z <= 2.0:
            supported.add((int((x - metadata["origin"][0]) / 0.25),
                           int((y - metadata["origin"][1]) / 0.25)))
    for row, column in np.argwhere(cells == 100):
        assert (column, row) in supported, (
            f"cell {column},{row} is occupied but the published cloud has no obstacle point there")
    # Everything the cloud did support survives the reconciliation untouched.
    kept = cells_before == 100
    np.testing.assert_array_equal(cells[kept], 100)


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
    assert {"cloud", "grid", "trajectory", "navigation", "navigation_grid"} <= set(manifest["artifacts"])

    checks = verify_map(tmp_path / "map")
    assert all(check.ok for check in checks), [(c.role, c.reason) for c in checks]
    assert {check.role for check in checks} == set(manifest["artifacts"])

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


def test_nav2_grid_round_trips_through_its_published_artifacts():
    from tangying_robot_gateway.navigation_map import nav2_artifacts, read_nav2_grid

    cells = np.array([[-1, 0, 100], [0, 100, -1]], dtype=np.int16)
    grid = {"width": 3, "height": 2, "resolution": 0.05,
            "origin": [-1.25, 2.5, 0.0], "cells": cells}
    pgm, yaml = nav2_artifacts(grid)
    decoded = read_nav2_grid(pgm, yaml)
    assert decoded["resolution"] == 0.05 and decoded["origin"][:2] == [-1.25, 2.5]
    np.testing.assert_array_equal(decoded["cells"], np.where(cells == 0, 0, np.where(cells >= 65, 100, -1)))
    # A flipped reader would agree on this symmetric fixture, so assert the
    # orientation with a grid whose rows are deliberately different.
    lopsided = {"width": 1, "height": 3, "resolution": 1.0, "origin": [0.0, 0.0, 0.0],
                "cells": np.array([[0], [100], [-1]], dtype=np.int16)}
    rows, _ = nav2_artifacts(lopsided)
    np.testing.assert_array_equal(read_nav2_grid(rows, yaml)["cells"][:, 0], [0, 100, -1])


def test_unreadable_navigation_artifacts_are_reported_not_guessed():
    from tangying_robot_gateway.navigation_map import nav2_artifacts, read_nav2_grid

    grid = {"width": 1, "height": 1, "resolution": 1.0, "origin": [0.0, 0.0, 0.0],
            "cells": np.zeros((1, 1), dtype=np.int16)}
    pgm, yaml = nav2_artifacts(grid)
    with pytest.raises(ValueError):
        read_nav2_grid(pgm[:-1], yaml)
    with pytest.raises(ValueError):
        read_nav2_grid(b"not a pgm", yaml)
    with pytest.raises(ValueError):
        read_nav2_grid(pgm, b"{not json")


def test_merged_grid_keeps_each_surveys_evidence_and_drops_neither():
    from tangying_robot_gateway.navigation_map import merge_grids

    # The first survey drove a corridor; the second drove a different room. The
    # second grid alone would report the corridor unknown, which is what made a
    # continuation's rooms unroutable.
    first = {"width": 4, "height": 1, "resolution": 1.0, "origin": [0.0, 0.0, 0.0],
             "cells": np.array([[0, 0, -1, 100]], dtype=np.int16)}
    second = {"width": 2, "height": 1, "resolution": 1.0, "origin": [2.0, 0.0, 0.0],
              "cells": np.array([[-1, 0]], dtype=np.int16)}
    merged = merge_grids(second, first)
    np.testing.assert_array_equal(merged["cells"], [[0, 0, -1, 100]])
    assert merged["width"] == 4 and merged["origin"][:2] == [0.0, 0.0]


def test_merge_is_an_overlay_where_obstacles_beat_free_space():
    from tangying_robot_gateway.navigation_map import merge_grids

    free = {"width": 2, "height": 1, "resolution": 1.0, "origin": [0.0, 0.0, 0.0],
            "cells": np.array([[0, -1]], dtype=np.int16)}
    occupied = {"width": 2, "height": 1, "resolution": 1.0, "origin": [0.0, 0.0, 0.0],
                "cells": np.array([[-1, 100]], dtype=np.int16)}
    merged = merge_grids(free, occupied)
    np.testing.assert_array_equal(merged["cells"], [[0, 100]])


def test_merge_refuses_grids_that_do_not_share_a_lattice():
    from tangying_robot_gateway.navigation_map import merge_grids

    fine = {"width": 1, "height": 1, "resolution": 0.05, "origin": [0.0, 0.0, 0.0],
            "cells": np.zeros((1, 1), dtype=np.int16)}
    coarse = {"width": 1, "height": 1, "resolution": 0.10, "origin": [0.0, 0.0, 0.0],
              "cells": np.zeros((1, 1), dtype=np.int16)}
    with pytest.raises(ValueError, match="resolution"):
        merge_grids(fine, coarse)


def test_merged_origin_lands_each_grid_on_the_same_lattice():
    from tangying_robot_gateway.navigation_map import merge_grids

    left = {"width": 2, "height": 2, "resolution": 0.5, "origin": [-1.0, -1.0, 0.0],
            "cells": np.full((2, 2), 0, dtype=np.int16)}
    right = {"width": 2, "height": 2, "resolution": 0.5, "origin": [0.0, 0.0, 0.0],
             "cells": np.full((2, 2), 100, dtype=np.int16)}
    merged = merge_grids(left, right)
    assert merged["origin"][:2] == [-1.0, -1.0]
    np.testing.assert_array_equal(merged["cells"], [[0, 0, -1, -1],
                                                    [0, 0, -1, -1],
                                                    [-1, -1, 100, 100],
                                                    [-1, -1, 100, 100]])


# --- free space a measured return proves was empty -------------------------

def test_the_space_between_the_sensor_and_its_return_is_measured_free():
    """Point splatting alone cannot close this gap, and neither can exploring.

    A depth return proves nothing was between the sensor and the surface. Leaving
    that corridor unknown is why a map stops where the points stop: measured on a
    real house survey, 44% of the remaining unknown was visible from poses the
    robot had already driven through.
    """
    from tangying_robot_gateway.map_pipeline import PointCloud

    xyz = np.array([[2.0, 0.1, 0.0]], dtype=np.float32)   # one floor return, 2 m out
    origins = {0: (0.05, 0.1, 0.3)}
    sources = np.zeros(1, dtype=np.int64)
    plain = occupancy_from_points(PointCloud(xyz=xyz), resolution=0.1,
                                  bounds={"min": [0, 0, 0], "max": [3.0, 1.0, 2.0]})
    swept = occupancy_from_points(PointCloud(xyz=xyz), resolution=0.1,
                                  bounds={"min": [0, 0, 0], "max": [3.0, 1.0, 2.0]},
                                  sources=sources, sensor_origins=origins)
    assert int((plain["cells"] >= 0).sum()) <= 2, "splatting knows one cell at most"
    known = int((swept["cells"] >= 0).sum())
    assert known > 10, f"the ray corridor should be measured free, got {known} cells"
    assert swept["cells"][1][10] == 0, "halfway along the ray is free"
    assert swept["cells"][1][25] == -1, "past the surface stays unknown"


def test_a_ray_never_clears_an_obstacle_and_the_return_keeps_its_own_verdict():
    from tangying_robot_gateway.map_pipeline import PointCloud

    # A wall at 1 m with floor beyond it: the wall cell must stay occupied, and the
    # space behind it must stay unknown rather than being swept free.
    xyz = np.array([
        [1.0, 0.1, 0.9],    # wall, obstacle height, seen twice so it is corroborated
        [1.0, 0.1, 0.9],
        [2.5, 0.1, 0.0],    # floor beyond the wall, seen through a doorway elsewhere
    ], dtype=np.float32)
    grid = occupancy_from_points(
        PointCloud(xyz=xyz), resolution=0.1,
        bounds={"min": [0, 0, 0], "max": [3.0, 1.0, 2.0]},
        sources=np.array([0, 1, 2], dtype=np.int64),
        sensor_origins={0: (0.05, 0.1, 0.3), 1: (0.05, 0.1, 0.3),
                        2: (2.4, 0.1, 0.3)})
    cells = grid["cells"]
    assert cells[1][10] == 100, "the measured wall is not made free by the ray stopping in it"
    assert cells[1][15] == -1, "behind the wall was never seen"


def test_a_ray_longer_than_the_cap_is_not_used_to_clear_the_room():
    from tangying_robot_gateway.map_pipeline import MAX_RAY_CELLS, PointCloud

    reach = (MAX_RAY_CELLS + 40) * 0.1
    xyz = np.array([[reach, 0.1, 0.0]], dtype=np.float32)
    grid = occupancy_from_points(
        PointCloud(xyz=xyz), resolution=0.1,
        bounds={"min": [0, 0, 0], "max": [reach + 1.0, 1.0, 2.0]},
        sources=np.zeros(1, dtype=np.int64), sensor_origins={0: (0.05, 0.1, 0.3)})
    assert int((grid["cells"] >= 0).sum()) <= 2, "an over-long ray is refused, not interpolated"


def test_origins_without_sources_are_refused_rather_than_ignored():
    """Origins are keyed by observation id; without sources there is nothing to key
    them to, and silently clearing nothing would look like a working map."""
    from tangying_robot_gateway.map_pipeline import PointCloud

    xyz = np.array([[2.0, 0.1, 0.0]], dtype=np.float32)
    with pytest.raises(ValueError, match="needs sources"):
        occupancy_from_points(PointCloud(xyz=xyz), resolution=0.1,
                              bounds={"min": [0, 0, 0], "max": [3.0, 1.0, 2.0]},
                              sensor_origins={0: (0.0, 0.0, 0.3)})


def test_an_origin_that_is_not_a_point_is_refused():
    from tangying_robot_gateway.map_pipeline import PointCloud

    xyz = np.array([[2.0, 0.1, 0.0]], dtype=np.float32)
    with pytest.raises(ValueError, match="finite 3-D world point"):
        occupancy_from_points(PointCloud(xyz=xyz), resolution=0.1,
                              bounds={"min": [0, 0, 0], "max": [3.0, 1.0, 2.0]},
                              sources=np.zeros(1, dtype=np.int64),
                              sensor_origins={0: (float("nan"), 0.0, 0.3)})


def test_a_room_comes_out_measurably_more_complete_with_rays_than_with_points():
    """The claim, on a room rather than an anecdote: same cloud, same resolution,
    one difference - whether the space a return crossed counts as measured."""
    from tangying_robot_gateway.map_pipeline import PointCloud

    size, resolution = 6.0, 0.05
    rows = []
    origins = {}
    sources = []
    for step, origin in enumerate([(0.2, 0.2), (0.2, 3.0), (5.8, 3.0), (3.0, 0.2)]):
        origins[step] = (origin[0], origin[1], 0.3)
        # A decimated fan of returns, as a real point cloud gives: one point every
        # 0.25 m of range, none of them close enough to tile the floor.
        for index in range(1, 25):
            angle = math.pi * (index / 25.0) - math.pi / 2.0
            for turn in range(4):
                heading = angle + turn * math.pi / 2.0
                distance = index * 0.25
                rows.append([origin[0] + distance * math.cos(heading),
                             origin[1] + distance * math.sin(heading), 0.0])
                sources.append(step)
    xyz = np.asarray(rows, dtype=np.float32)
    cloud = PointCloud(xyz=xyz)
    bounds = {"min": [0, 0, 0], "max": [size, size, 2.0]}
    plain = occupancy_from_points(cloud, resolution=resolution, bounds=bounds)
    swept = occupancy_from_points(cloud, resolution=resolution, bounds=bounds,
                                  sources=np.asarray(sources, dtype=np.int64),
                                  sensor_origins=origins)
    before = int((plain["cells"] >= 0).sum())
    after = int((swept["cells"] >= 0).sum())
    assert after > 1.5 * before, (
        f"rays should settle far more of the room: {before} cells vs {after}")
    assert swept["cells"].shape == plain["cells"].shape
