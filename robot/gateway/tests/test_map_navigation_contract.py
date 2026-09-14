import io
import sqlite3

import numpy as np
import pytest
from PIL import Image
from tangying_robot_gateway.map_pipeline import PointCloud, build_map, occupancy_from_points
from tangying_robot_gateway.rtabmap_export import CameraIntrinsics, decode_depth, reconstruct_cloud


def png(array):
    output = io.BytesIO()
    Image.fromarray(array).save(output, format="PNG")
    return output.getvalue()


def test_depth_png_uint16_is_millimetres_not_float_bytes():
    depth = decode_depth(png(np.array([[1000, 0], [2500, 65535]], dtype=np.uint16)))
    np.testing.assert_allclose(depth.reshape(2, 2), [[1, 0], [2.5, 65.535]])


def test_rtabmap_float_png_recovers_opencv_bgra_byte_order():
    depth = np.array([[1., 2.5]], dtype="<f4")
    bgra = depth.view(np.uint8).reshape(1, 2, 4)
    np.testing.assert_allclose(decode_depth(png(bgra[..., [2, 1, 0, 3]])), depth.ravel())


def test_depth_cloud_uses_optical_to_base_transform():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE Node (id INTEGER, pose BLOB)")
    connection.execute("CREATE TABLE Data (id INTEGER, depth BLOB, image BLOB)")
    for index in range(2):
        pose = np.eye(4, dtype=np.float32)[:3]
        pose[0, 3] = index
        connection.execute("INSERT INTO Node VALUES (?,?)", (index, pose.tobytes()))
        connection.execute("INSERT INTO Data VALUES (?,?,NULL)",
                           (index, png(np.array([[1000]], dtype=np.uint16))))
    transform = np.array([[0, 0, 1, 0], [-1, 0, 0, 0], [0, -1, 0, .5], [0, 0, 0, 1]])
    cloud = reconstruct_cloud(connection, intrinsics=CameraIntrinsics(1, 1, 0, 0),
                              width=1, height=1, stride=1, base_from_optical=transform)
    np.testing.assert_allclose(cloud.xyz, [[1, 0, .5], [2, 0, .5]])


def test_invalid_points_do_not_become_a_plausible_map():
    with pytest.raises(ValueError, match="finite"):
        PointCloud(np.array([[float("nan"), 0, 0]], dtype=np.float32))


def test_navigation_export_preserves_unknown_and_image_row_orientation(tmp_path):
    import yaml

    grid = {"width": 3, "height": 2, "resolution": .1, "origin": [1., 2., .3],
            "cells": np.array([[-1, 0, 100], [0, 100, -1]], dtype=np.int16)}
    # The published grid has to be answerable from the published cloud, so the
    # two occupied cells carry an obstacle-height point each: cell (row 0, col 2)
    # and cell (row 1, col 1) at this resolution and origin.
    cloud = PointCloud(np.array([[0, 0, 0], [1, 1, 1], [1.25, 2.05, .5], [1.15, 2.15, .5]],
                                dtype=np.float32))
    manifest = build_map(tmp_path, map_id="home", robot_id="robot", lod_levels=1,
                         cloud=cloud,
                         occupancy_grid=grid, calibration_revision="c" * 64)
    document = yaml.safe_load((tmp_path / "navigation/map.yaml").read_text())
    image = np.asarray(Image.open(tmp_path / "navigation" / document["image"]))
    np.testing.assert_array_equal(image, [[254, 0, 205], [205, 254, 0]])
    assert document["origin"] == [1., 2., .3]
    assert document["mode"] == "trinary"
    assert {"navigation", "navigation_grid"} <= manifest["artifacts"].keys()


def test_obstacle_at_maximum_bound_is_not_lost():
    cloud = PointCloud(np.array([[0., 0., 0.], [1., 1., .5]], dtype=np.float32))
    grid = occupancy_from_points(cloud, resolution=.1)
    assert 100 in grid["cells"]
