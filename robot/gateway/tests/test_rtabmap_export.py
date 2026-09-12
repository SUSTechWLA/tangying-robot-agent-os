"""Reading a real RTAB-Map database.

These tests run against a genuine 208 MB survey when one is available and are
skipped otherwise, because the point of this module is what real databases actually
contain rather than what the schema suggests they should.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from tangying_robot_gateway.rtabmap_export import (
    CameraIntrinsics,
    RtabmapExportError,
    database_summary,
    decode_depth,
    open_database,
    read_poses,
    read_trajectory,
    reconstruct_cloud,
    require_distinct_poses,
)

#: A survey extracted from the navigation stack's Docker volume, when present.
REAL_DATABASE = Path(os.environ.get("TANGYING_RTABMAP_SAMPLE", "/tmp/rtabmap/rtabmap.db"))

requires_real = pytest.mark.skipif(
    not REAL_DATABASE.is_file(), reason="no RTAB-Map sample database available"
)


def build_fake_database(path: Path, poses: list[bytes], depth: bytes | None = None) -> Path:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE Node (id INTEGER PRIMARY KEY, pose BLOB)")
    connection.execute("CREATE TABLE Data (id INTEGER PRIMARY KEY, image BLOB, depth BLOB)")
    connection.execute("CREATE TABLE Admin (version TEXT, opt_cloud BLOB)")
    for index, blob in enumerate(poses, start=1):
        connection.execute("INSERT INTO Node (id, pose) VALUES (?, ?)", (index, blob))
        connection.execute("INSERT INTO Data (id, image, depth) VALUES (?, NULL, ?)", (index, depth))
    connection.execute("INSERT INTO Admin (version, opt_cloud) VALUES ('0.22.1', NULL)")
    connection.commit()
    connection.close()
    return path


def pose_blob(x: float, y: float = 0.0, z: float = 0.0) -> bytes:
    matrix = np.eye(4, dtype=np.float32)[:3, :]
    matrix[0, 3], matrix[1, 3], matrix[2, 3] = x, y, z
    return matrix.tobytes()


def test_a_database_without_the_expected_tables_is_named_not_guessed(tmp_path: Path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(RtabmapExportError) as failure:
        open_database(empty)
    assert "Node" in str(failure.value)

    with pytest.raises(RtabmapExportError):
        open_database(tmp_path / "missing.db")


def test_poses_are_read_as_twelve_float_transforms(tmp_path: Path):
    database = build_fake_database(tmp_path / "ok.db", [pose_blob(1.5), pose_blob(2.5)])
    connection = open_database(database)
    poses = read_poses(connection)
    assert [node for node, _ in poses] == [1, 2]
    assert poses[0][1].shape == (3, 4)
    assert poses[1][1][0, 3] == pytest.approx(2.5)
    assert read_trajectory(connection) == [(1.5, 0.0, 0.0), (2.5, 0.0, 0.0)]


def test_a_pose_of_the_wrong_size_is_an_error_not_a_reshape(tmp_path: Path):
    database = build_fake_database(tmp_path / "bad.db", [b"\x00" * 20, pose_blob(1.0)])
    with pytest.raises(RtabmapExportError) as failure:
        read_poses(open_database(database))
    assert "expected 12" in str(failure.value)


def test_a_repeated_pose_is_refused_rather_than_stacked():
    # The real survey has 1237 nodes and one distinct pose. Back-projecting at a
    # single repeated pose yields a dense, plausible, entirely wrong cloud, so it has
    # to be an error.
    import numpy as np

    repeated = [(index, np.eye(3, 4, dtype=np.float64)) for index in range(1, 6)]
    with pytest.raises(RtabmapExportError) as failure:
        require_distinct_poses(repeated)
    assert "same pose" in str(failure.value)
    assert "5 scans" in str(failure.value)

    moving = [(index, np.eye(3, 4, dtype=np.float64) + np.array([[0, 0, 0, index], [0, 0, 0, 0], [0, 0, 0, 0]]))
              for index in range(1, 6)]
    require_distinct_poses(moving)  # must not raise


def test_the_distinct_pose_check_actually_compares_values():
    # A set comprehension over a generator collects generator objects, each unique,
    # and the guard silently never fires. This pins the comparison.
    import numpy as np

    identical = [(1, np.zeros((3, 4))), (2, np.zeros((3, 4)))]
    with pytest.raises(RtabmapExportError):
        require_distinct_poses(identical)
    # The guard rounds to a micrometre, so real motion passes and shared sensor
    # noise below that tolerance still counts as "not moving".
    moved = [(1, np.zeros((3, 4))), (2, np.zeros((3, 4)) + 0.001)]
    require_distinct_poses(moved)
    below_tolerance = [(1, np.zeros((3, 4))), (2, np.zeros((3, 4)) + 0.0000001)]
    with pytest.raises(RtabmapExportError):
        require_distinct_poses(below_tolerance)


def test_a_blob_that_is_not_a_png_is_rejected():
    with pytest.raises(RtabmapExportError):
        decode_depth(b"")
    with pytest.raises(RtabmapExportError):
        decode_depth(b"not a png at all")


def test_no_depth_frames_is_reported_as_such(tmp_path: Path):
    database = build_fake_database(tmp_path / "nodepth.db", [pose_blob(0.0), pose_blob(1.0)], depth=None)
    connection = open_database(database)
    with pytest.raises(RtabmapExportError) as failure:
        reconstruct_cloud(connection, intrinsics=CameraIntrinsics(100.0, 100.0, 50.0, 50.0))
    assert "RGB-D" in str(failure.value) or "depth" in str(failure.value)


@requires_real
def test_the_real_survey_is_understood():
    connection = open_database(REAL_DATABASE)
    summary = database_summary(connection)
    assert summary["nodes"] > 100
    assert summary["depthFrames"] > 100
    # And it is a debugging artifact, not a usable survey: the export has to be able
    # to say so rather than merging two maps and fifty hours into one.
    assert summary["health"], "this sample is expected to be a multi-session database"
    assert any("地图" in reason for reason in summary["health"])
    assert summary["hasOptimizedCloud"] is False, (
        "if this ever becomes true, the export should read the assembled cloud "
        "instead of reconstructing one"
    )


@requires_real
def test_the_real_survey_carries_no_trajectory_and_says_so():
    # This is the finding that matters: the database reconstructs to millions of
    # points, and every one of them would be placed at the same pose.
    connection = open_database(REAL_DATABASE)
    poses = read_poses(connection)
    distinct = {tuple(round(float(v), 6) for v in pose.ravel()) for _, pose in poses}
    assert len(distinct) == 1, "the sample is expected to have no per-node poses"
    with pytest.raises(RtabmapExportError) as failure:
        read_trajectory(connection)
    assert "no trajectory" in str(failure.value).lower() or "same pose" in str(failure.value)


@requires_real
def test_the_real_depth_frames_decode_to_metres():
    connection = open_database(REAL_DATABASE)
    blob = connection.execute("SELECT depth FROM Data WHERE depth IS NOT NULL LIMIT 1").fetchone()[0]
    depth = decode_depth(bytes(blob))
    assert depth.size == 320 * 240, "the sample unit captures 320x240"
    finite = depth[np.isfinite(depth) & (depth > 0)]
    assert finite.size > 0
    # Indoor depths in metres: sub-millimetre values would mean a millimetre buffer
    # read as metres, and kilometre values would mean the reverse.
    assert 0.05 < float(np.median(finite)) < 20.0


def test_a_database_that_is_not_one_survey_says_why(tmp_path: Path):
    # A database is not necessarily one mapping run. The sample from the navigation
    # volume has two maps, spans fifty hours and has 1235 of its 1237 nodes deleted;
    # exporting that as a single map would merge unrelated sessions.
    connection = sqlite3.connect(tmp_path / "messy.db")
    connection.execute("CREATE TABLE Node (id INTEGER PRIMARY KEY, map_id INTEGER, weight INTEGER, stamp REAL, pose BLOB)")
    connection.execute("CREATE TABLE Data (id INTEGER PRIMARY KEY, image BLOB, depth BLOB)")
    connection.execute("CREATE TABLE Admin (version TEXT, opt_cloud BLOB)")
    fine = np.eye(3, 4, dtype=np.float32)
    connection.executemany("INSERT INTO Node VALUES (?, ?, ?, ?, ?)", [
        (1, 0, 0, 0.0, fine.tobytes()),
        (2, 0, 0, 10.0, fine.tobytes()),
        (3, 1, -9, 100000.0, fine.tobytes()),
    ])
    connection.execute("INSERT INTO Admin VALUES ('0.22.1', NULL)")
    connection.commit()
    connection.close()

    from tangying_robot_gateway.rtabmap_export import survey_health

    reasons = survey_health(open_database(tmp_path / "messy.db"))
    assert any("2 张地图" in reason for reason in reasons)
    assert any("已被删除" in reason for reason in reasons)
    assert any("小时" in reason for reason in reasons)


def test_a_clean_single_survey_has_nothing_to_report(tmp_path: Path):
    connection = sqlite3.connect(tmp_path / "clean.db")
    connection.execute("CREATE TABLE Node (id INTEGER PRIMARY KEY, map_id INTEGER, weight INTEGER, stamp REAL, pose BLOB)")
    connection.execute("CREATE TABLE Data (id INTEGER PRIMARY KEY, image BLOB, depth BLOB)")
    connection.execute("CREATE TABLE Admin (version TEXT, opt_cloud BLOB)")
    matrix = np.eye(3, 4, dtype=np.float32)
    connection.executemany("INSERT INTO Node VALUES (?, ?, ?, ?, ?)", [
        (index, 0, 0, float(index), (matrix + np.array([[0, 0, 0, index], [0, 0, 0, 0], [0, 0, 0, 0]], dtype=np.float32)).tobytes())
        for index in range(1, 6)
    ])
    connection.execute("INSERT INTO Admin VALUES ('0.22.1', NULL)")
    connection.commit()
    connection.close()

    from tangying_robot_gateway.rtabmap_export import survey_health

    assert survey_health(open_database(tmp_path / "clean.db")) == []
