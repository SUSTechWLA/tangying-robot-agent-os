"""The conversions that stand between Gazebo and the runtime's own contract.

Two of these have already gone wrong once in this repository - a head camera
whose published rotation was 120.5 degrees from the one the renderer used, and a
calibration lens that was only right at one resolution - so they are tested here
rather than discovered on a robot.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from tangying_robot_gateway.gazebo_bridge import (
    GazeboBridgeError,
    base_from_camera,
    build_rgbd_frame,
    heights_from_depths,
    intrinsics_from_field_of_view,
    optical_from_link,
)


def test_the_focal_length_gazebo_implies_is_the_one_the_runtime_gets():
    """SDF declares a field of view, the runtime wants a focal length. A camera
    whose real lens differs from the declared FOV produces a uniformly mis-scaled
    map, which reads as a SLAM fault and is not one."""
    for width, height, fov in ((320, 240, 1.25), (640, 480, 1.0472), (1280, 720, 1.5)):
        k = intrinsics_from_field_of_view(width, height, fov)
        assert k[0, 0] == pytest.approx((width / 2) / math.tan(fov / 2))
        # Square pixels: Gazebo has no vertical focal length to declare, and an
        # invented aspect correction would tilt every projected point.
        assert k[1, 1] == pytest.approx(k[0, 0])
        # The centre of the pixel grid, not n/2: the half pixel is what makes a
        # point land on the pixel its own ray came from.
        assert (k[0, 2], k[1, 2]) == ((width - 1) / 2, (height - 1) / 2)


def test_the_declared_sensor_size_and_fov_are_the_worlds_own():
    """Pinned against worlds/tangying_home.sdf so a change there is noticed here."""
    k = intrinsics_from_field_of_view(320, 240, 1.25)
    assert k[0, 0] == pytest.approx(221.765, abs=0.01)
    assert (k[0, 2], k[1, 2]) == (159.5, 119.5)


def test_a_camera_link_becomes_an_optical_frame_by_the_documented_rotation():
    """REP-103 camera link (x forward, y left, z up) versus the runtime's optical
    frame (x right, y down, z forward). Getting this wrong by a quarter turn is
    what a point cloud pointing at the ceiling looks like."""
    rotation = optical_from_link()
    np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-12)
    assert np.linalg.det(rotation) == pytest.approx(1.0)
    # A ray one metre forward along the link's x is one metre deep in optical z.
    np.testing.assert_allclose(rotation @ [1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    # Left in the link is right in optical, and optical x is right, so left is -x.
    np.testing.assert_allclose(rotation @ [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0])
    np.testing.assert_allclose(rotation @ [0.0, 0.0, 1.0], [0.0, -1.0, 0.0])


def test_the_capture_pose_is_relative_to_the_robot_not_to_the_world():
    """The runtime attaches the camera to the robot, so the world pose has to be
    divided out. A bridge that published the world pose would put every capture's
    geometry wherever the robot happened to be standing."""
    world_from_base = np.eye(4)
    world_from_base[:3, 3] = [2.0, 3.0, 0.0]
    camera = np.eye(4)
    camera[:3, 3] = [2.0, 3.0, 0.5]      # half a metre above the base, same spot
    result = base_from_camera(world_from_base, camera, camera_frame_is_optical=True)
    np.testing.assert_allclose(result[:3, 3], [0.0, 0.0, 0.5], atol=1e-12)
    np.testing.assert_allclose(result[:3, :3], np.eye(3), atol=1e-12)


def test_a_non_rigid_transform_is_refused_rather_than_applied():
    """A scaled transform applied to depth produces geometry that is wrong in a
    direction no downstream check can see."""
    scaled = np.eye(4)
    scaled[0, 0] = 2.0
    with pytest.raises(GazeboBridgeError) as failure:
        base_from_camera(np.eye(4), scaled, camera_frame_is_optical=True)
    assert failure.value.code == "INVALID_TRANSFORM"


def a_capture(**changes):
    values = {
        "width": 4, "height": 3,
        "rgb": np.zeros((3, 4, 3), dtype=np.uint8),
        "depth_metres": np.full((3, 4), 1.5, dtype=np.float64),
        "intrinsics": intrinsics_from_field_of_view(4, 3, 1.25),
        "capture_in_base": np.eye(4),
        "calibration_revision": "c" * 64,
    }
    return build_rgbd_frame(**(values | changes))


def test_a_capture_is_wire_encoded_in_the_declared_widths():
    frame = a_capture()
    assert frame["width"] == 4 and frame["height"] == 3
    assert len(frame["rgb"]) == 4 * 3 * 3
    # float32 little-endian, because that is what the message declares: a float64
    # array sent as-is would be read at half its length.
    assert len(frame["depth_metres_f32"]) == 4 * 3 * 4
    assert len(frame["intrinsics"]) == 9 and len(frame["base_from_camera"]) == 16


def test_depth_that_is_not_floating_point_is_refused():
    """NaN and Inf are how a sensor says "no return". An integer array has to
    invent a value for that, and every invented value is a plausible distance."""
    with pytest.raises(GazeboBridgeError) as failure:
        a_capture(depth_metres=np.full((3, 4), 1500, dtype=np.uint16))
    assert failure.value.code == "INVALID_DEPTH"
    # Floating point is accepted with holes in it.
    frame = a_capture(depth_metres=np.full((3, 4), np.nan))
    assert len(frame["depth_metres_f32"]) == 4 * 3 * 4


def test_a_capture_without_its_calibration_revision_is_refused():
    with pytest.raises(GazeboBridgeError) as failure:
        a_capture(calibration_revision="")
    assert failure.value.code == "CALIBRATION_REVISION_REQUIRED"


def test_mismatched_or_impossible_captures_are_named_not_encoded():
    for changes, code in (
        ({"rgb": np.zeros((3, 4, 3), dtype=np.float32)}, "INVALID_RGB"),
        ({"rgb": np.zeros((4, 3, 3), dtype=np.uint8)}, "INVALID_RGB"),
        ({"depth_metres": np.zeros((4, 3))}, "INVALID_DEPTH"),
        ({"intrinsics": np.zeros((3, 3))}, "INVALID_INTRINSICS"),
        ({"intrinsics": np.array([[4.0, 0, 99.0], [0, 4.0, 1.0], [0, 0, 1.0]])},
         "INVALID_INTRINSICS"),
        ({"capture_in_base": np.zeros((4, 4))}, "INVALID_TRANSFORM"),
    ):
        with pytest.raises(GazeboBridgeError) as failure:
            a_capture(**changes)
        assert failure.value.code == code, changes


def test_a_level_camera_sees_lower_rows_lower_in_the_robot_frame():
    """The diagnostic a person reaches for first when a capture looks wrong.

    A level camera half a metre up, looking forward, with a constant 2 m of depth
    in front of it: the bottom rows of the image are tilted downwards and so land
    lower in the base frame than the top rows. If the optical convention is a sign
    out, this ordering inverts - and an inverted height is a point cloud on the
    ceiling, which a self-consistent pipeline will happily keep publishing.
    """
    intrinsics = intrinsics_from_field_of_view(4, 3, 1.25)
    transform = np.eye(4)
    # Base-from-optical for a level camera: optical z (forward) is base x.
    transform[:3, :3] = optical_from_link().T
    transform[:3, 3] = [0.0, 0.0, 0.5]
    depth = np.full((3, 4), 2.0)
    heights = heights_from_depths(depth, intrinsics, transform).reshape(3, 4)
    top, bottom = heights[0].mean(), heights[2].mean()
    assert bottom < top, f"the lower image row must land lower: top {top}, bottom {bottom}"
    # The top of the image looks up and the bottom looks down, so the spread
    # straddles the camera's own height. A sign error inverts exactly this.
    assert top > 0.5 > bottom, f"top {top} should be above the camera, bottom {bottom} below"


def test_the_heights_diagnostic_sees_a_tilted_camera():
    intrinsics = intrinsics_from_field_of_view(4, 3, 1.25)
    transform = np.eye(4)
    tilt = math.radians(20.0)
    transform[:3, :3] = np.array([
        [1.0, 0.0, 0.0],
        [0.0, math.cos(tilt), -math.sin(tilt)],
        [0.0, math.sin(tilt), math.cos(tilt)],
    ])
    transform[:3, 3] = [0.0, 0.0, 0.5]
    heights = heights_from_depths(np.full((3, 4), 2.0), intrinsics, transform)
    assert heights.max() - heights.min() > 0.1, "a tilt must spread the heights out"
