"""Turning a Gazebo camera into the runtime's own RGB-D contract.

Why this module exists
----------------------
The system already has a bridge from the runtime *to* ROS
(``robot/ros2_ws/.../rgbd_bridge.py``): the runtime is the gRPC server, and ROS
consumes it so RTAB-Map and Nav2 can localise. Gazebo is the mirror image - it
produces ROS topics itself and serves the navigation API - so making Gazebo a
*robot backend* needs the inverse process: one that consumes ROS and serves
``robot.profile.v1``.

That inversion is the whole design, and it means the numbers have to be converted
rather than forwarded. Two conversions here will silently corrupt every point
cloud downstream if they are wrong, and both have already gone wrong once in this
repository:

* **Intrinsics.** Gazebo's SDF declares a horizontal field of view, not a focal
  length. ``fx = (width / 2) / tan(hfov / 2)`` has to be derived, and a camera
  whose real lens differs from the declared FOV produces a map that is uniformly
  mis-scaled - which looks like a SLAM problem and is not one.
* **The camera pose.** ``RGBDFrame.base_from_camera`` is optical (right/down/
  forward) to the robot base (forward/left/up). Earlier in this project a stored
  head-camera rotation was 120.5 degrees from what the renderer used, and the
  runtime published it on every capture; nothing detected it because the pipeline
  was self-consistent. So the convention is stated once, here, and tested.

The functions are pure: dicts and arrays in, dicts and arrays out, no ROS, no gRPC,
no Gazebo. That is deliberate - the plumbing is hard to test and this is the part
that must be right.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

#: The runtime accepts uint8 RGB at these bounds and rejects the rest.
MAX_WIDTH = 3840
MAX_HEIGHT = 2160

__all__ = [
    "GazeboBridgeError",
    "base_from_camera",
    "build_rgbd_frame",
    "heights_from_depths",
    "intrinsics_from_field_of_view",
    "optical_from_link",
]


class GazeboBridgeError(Exception):
    """A refusal the bridge reports instead of publishing a wrong capture."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def intrinsics_from_field_of_view(width: int, height: int,
                                  horizontal_fov_rad: float) -> np.ndarray:
    """The pinhole K a Gazebo camera actually has, from the FOV it declares.

    Square pixels, so ``fy == fx``: a Gazebo camera has no separate vertical
    focal length to declare, and inventing an aspect correction here would tilt
    every projected point.

    The principal point is the centre of the *pixel grid*, ``(n - 1) / 2``, not
    ``n / 2``. The half-pixel difference is what makes a projected point land on
    the pixel its own ray came from, and it is the same convention the rest of
    this repository uses when it derives intrinsics from a model.
    """
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise GazeboBridgeError("INVALID_SIZE", f"image size must be positive integers, got {width}x{height}")
    if width > MAX_WIDTH or height > MAX_HEIGHT:
        raise GazeboBridgeError("INVALID_SIZE", f"{width}x{height} exceeds the bounded capture size")
    if not math.isfinite(horizontal_fov_rad) or not 0.0 < horizontal_fov_rad < math.pi:
        raise GazeboBridgeError(
            "INVALID_FOV", f"horizontal field of view must be in (0, pi), got {horizontal_fov_rad}")
    focal = (width / 2.0) / math.tan(horizontal_fov_rad / 2.0)
    return np.array([
        [focal, 0.0, (width - 1) / 2.0],
        [0.0, focal, (height - 1) / 2.0],
        [0.0, 0.0, 1.0],
    ])


def optical_from_link() -> np.ndarray:
    """The fixed rotation from a ROS camera *link* frame to its optical frame.

    REP-103 says a camera link is x-forward, y-left, z-up. The optical frame the
    runtime speaks is x-right, y-down, z-forward. Getting this wrong rotates every
    capture by a quarter turn, which surfaces as a point cloud pointing at the
    ceiling.
    """
    # Rows are the output components, so: optical x is the link's -y (right is
    # minus left), optical y is the link's -z (down is minus up), and optical z is
    # the link's +x (forward is forward). The first draft of this matrix mapped
    # forward to -x, which the test below caught; a camera a quarter turn out
    # produces a point cloud aimed at the wall behind the robot.
    return np.array([
        [0.0, -1.0, 0.0],
        [0.0, 0.0, -1.0],
        [1.0, 0.0, 0.0],
    ])


def _require_rigid(matrix: Any, name: str) -> np.ndarray:
    """A finite, proper rigid transform, or a refusal naming which one is not.

    Shape alone is not enough. A zero matrix has the right shape and would send
    every point to the origin; a scaled one would stretch the depth. Both look
    like plausible geometry downstream, which is why the check is here rather
    than left to a consumer.
    """
    value = np.asarray(matrix, dtype=float)
    if value.shape != (4, 4) or not np.isfinite(value).all():
        raise GazeboBridgeError("INVALID_TRANSFORM", f"{name} must be a finite 4x4 matrix")
    rotation = value[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5):
        raise GazeboBridgeError("INVALID_TRANSFORM", f"{name} rotation is not orthonormal")
    if not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-5):
        raise GazeboBridgeError("INVALID_TRANSFORM", f"{name} is not a proper rotation")
    if not np.allclose(value[3], [0.0, 0.0, 0.0, 1.0]):
        raise GazeboBridgeError("INVALID_TRANSFORM", f"{name} is not an affine rigid transform")
    return value


def base_from_camera(robot_from_base: np.ndarray, world_from_camera_link: np.ndarray,
                     *, camera_frame_is_optical: bool = False) -> np.ndarray:
    """The capture's pose in the robot base frame, as the runtime defines it.

    ``robot_from_base`` is the pose of the robot's base in whatever world frame
    both arguments share - for a Gazebo run, the odometry pose. Both transforms
    must be rigid: a scaled or reflected one would be applied to the depth and
    produce geometry that is wrong in a way no downstream check can see.
    """
    world_from_base = _require_rigid(robot_from_base, "robot_from_base")
    camera = _require_rigid(world_from_camera_link, "world_from_camera_link")
    if not camera_frame_is_optical:
        # ``optical_from_link`` is T(optical <- link): it takes a point's link
        # coordinates and returns its optical ones. Composing world_from_link with
        # it would be composing T(world <- link) with T(optical <- link), which is
        # not a chain. The link frame is reached *from* optical by the inverse,
        # which for a rotation is the transpose - and using the untransposed
        # matrix is exactly the quarter-turn error this module exists to prevent.
        # The test below found it: a level forward-looking camera came out with
        # its optical axis along the robot's right.
        link_from_optical = optical_from_link().T
        camera = camera @ np.block([[link_from_optical, np.zeros((3, 1))],
                                    [np.zeros((1, 3)), np.ones((1, 1))]])
    return np.linalg.inv(world_from_base) @ camera


def build_rgbd_frame(*, width: int, height: int, rgb: Any, depth_metres: Any,
                     intrinsics: np.ndarray, capture_in_base: np.ndarray,
                     calibration_revision: str) -> dict[str, Any]:
    """The wire fields of one ``RGBDFrame``, or a refusal.

    The depth is cast to little-endian float32 because that is what the message
    declares; a float64 array sent as-is would be read at half its length and the
    far half of every image would become whatever happened to follow it in memory.
    """
    if not calibration_revision:
        raise GazeboBridgeError(
            "CALIBRATION_REVISION_REQUIRED",
            "a capture without its calibration revision cannot be compared with any other")
    image = np.asarray(rgb)
    depth = np.asarray(depth_metres)
    if image.dtype != np.uint8 or image.shape != (height, width, 3):
        raise GazeboBridgeError(
            "INVALID_RGB", f"RGB must be uint8 {height}x{width}x3, got {image.dtype} {image.shape}")
    if depth.shape != (height, width):
        raise GazeboBridgeError(
            "INVALID_DEPTH", f"depth must be {height}x{width}, got {depth.shape}")
    if depth.dtype.kind != "f":
        # Floating point, and not integer millimetres: NaN and Inf are how a
        # sensor says "no return here", and they have to survive the conversion.
        # An integer array would have to invent a value for that, and every
        # invented value is a plausible-looking distance.
        raise GazeboBridgeError(
            "INVALID_DEPTH", f"depth must be floating-point metres, got {depth.dtype}")
    k = np.asarray(intrinsics, dtype=float)
    if k.shape != (3, 3) or k[0, 0] <= 0 or k[1, 1] <= 0:
        raise GazeboBridgeError("INVALID_INTRINSICS", "intrinsics must be a 3x3 with positive focal lengths")
    if not np.allclose(k[2], [0.0, 0.0, 1.0]) or abs(k[0, 1]) + abs(k[1, 0]) > 1e-9:
        raise GazeboBridgeError(
            "INVALID_INTRINSICS", "intrinsics must describe a rectified pinhole camera")
    if not (0 <= k[0, 2] < width and 0 <= k[1, 2] < height):
        raise GazeboBridgeError("INVALID_INTRINSICS", "principal point lies outside the image")
    transform = _require_rigid(capture_in_base, "capture_in_base")
    return {
        "width": int(width),
        "height": int(height),
        "rgb": np.ascontiguousarray(image, dtype=np.uint8).tobytes(order="C"),
        "depth_metres_f32": np.ascontiguousarray(depth, dtype="<f4").tobytes(order="C"),
        "intrinsics": [float(v) for v in k.reshape(-1)],
        "base_from_camera": [float(v) for v in transform.reshape(-1)],
    }


def heights_from_depths(depth: np.ndarray, intrinsics: np.ndarray,
                        transform: np.ndarray) -> np.ndarray:
    """Optical Z of every pixel projected back to its height in the robot base.

    Not part of the wire contract - a diagnostic. When a Gazebo capture looks
    wrong, the first question is whether the camera is where the bridge thinks it
    is, and a floor that should be flat comes back as a ramp if it is not.
    """
    valid = np.isfinite(depth) & (depth > 0)
    rows, columns = np.nonzero(valid)
    z = depth[rows, columns]
    optical = np.stack([(columns - intrinsics[0, 2]) * z / intrinsics[0, 0],
                        (rows - intrinsics[1, 2]) * z / intrinsics[1, 1], z], axis=1)
    base = optical @ transform[:3, :3].T + transform[:3, 3]
    return base[:, 2]
