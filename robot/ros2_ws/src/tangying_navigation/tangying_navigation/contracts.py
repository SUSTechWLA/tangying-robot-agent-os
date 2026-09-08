"""Pure validation shared by the ROS and loopback HTTP boundaries."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np


class ContractError(ValueError):
    pass


def visual_quality(stats):
    """Require actual RTAB-Map vocabulary, not merely a transient depth grid."""

    def count(key):
        value = stats.get(key, 0)
        return float(value) if finite_number(value) and value >= 0 else 0.0

    current = count("Keypoint/Current_frame/words")
    dictionary = count("Keypoint/Dictionary_size/words")
    return {
        "currentFrameWords": current,
        "dictionaryWords": dictionary,
        "ready": current >= 20 and dictionary >= 20,
    }


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_goal(body):
    if not isinstance(body, dict) or set(body) != {"commandId", "goalPose", "frameId"}:
        raise ContractError("expected commandId, goalPose and frameId")
    command = body["commandId"]
    if not isinstance(command, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", command
    ):
        raise ContractError("invalid commandId")
    pose = body["goalPose"]
    if not isinstance(pose, list) or len(pose) != 7 or not all(map(finite_number, pose)):
        raise ContractError("goalPose must contain seven finite xyz,wxyz values")
    if any(abs(value) > 1000 for value in pose[:2]) or abs(pose[2]) > 0.05:
        raise ContractError("goal outside planar navigation bounds")
    if (
        abs(sum(value * value for value in pose[3:]) - 1) > 1e-5
        or abs(pose[4]) > 1e-4
        or abs(pose[5]) > 1e-4
    ):
        raise ContractError("goal requires a normalized planar quaternion")
    if body["frameId"] not in ("map", "odom"):
        raise ContractError("frameId must be map or odom")
    return {"commandId": command, "goalPose": list(map(float, pose)), "frameId": body["frameId"]}


def localized_goal_error(world, goal, checked_at_ms):
    """A fresh measured pose may confirm an existing goal without authorizing motion."""
    stamp = world.get("poseObservedAtUnixMs")
    if (
        world.get("ready") is not True
        or world.get("poseSource") != "rtabmap_tf"
        or world.get("localizationState") not in {"LOCALIZED", "MAPPING_ODOMETRY"}
        or type(stamp) is not int
        or not -250 <= checked_at_ms - stamp <= 1000
    ):
        raise ContractError("goal confirmation requires fresh measured map localization")
    actual = world.get("mapPose")
    for pose in (actual, goal):
        if (
            not isinstance(pose, list) or len(pose) != 7
            or not all(map(finite_number, pose))
            or abs(sum(value * value for value in pose[3:]) - 1) > 1e-5
        ):
            raise ContractError("goal confirmation requires finite normalized poses")
    def yaw(pose):
        w, x, y, z = pose[3:]
        return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    delta = yaw(actual) - yaw(goal)
    return math.hypot(actual[0] - goal[0], actual[1] - goal[1]), abs(
        math.atan2(math.sin(delta), math.cos(delta))
    )


def quaternion_matrix(wxyz):
    w, x, y, z = wxyz
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=float,
    )


def matrix_quaternion(rotation):
    # Symmetric eigenvalue formulation handles rotations near pi without division by zero.
    r = np.asarray(rotation, dtype=float)
    k = (
        np.array(
            [
                [
                    r[0, 0] - r[1, 1] - r[2, 2],
                    r[1, 0] + r[0, 1],
                    r[2, 0] + r[0, 2],
                    r[2, 1] - r[1, 2],
                ],
                [
                    r[1, 0] + r[0, 1],
                    r[1, 1] - r[0, 0] - r[2, 2],
                    r[2, 1] + r[1, 2],
                    r[0, 2] - r[2, 0],
                ],
                [
                    r[2, 0] + r[0, 2],
                    r[2, 1] + r[1, 2],
                    r[2, 2] - r[0, 0] - r[1, 1],
                    r[1, 0] - r[0, 1],
                ],
                [r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1], np.trace(r)],
            ]
        )
        / 3
    )
    _, vectors = np.linalg.eigh(k)
    x, y, z, w = vectors[:, -1]
    result = np.array([w, x, y, z])
    return result if w >= 0 else -result


@dataclass(frozen=True)
class RawCapture:
    width: int
    height: int
    rgb: bytes
    depth: bytes
    intrinsics: np.ndarray
    base_from_camera: np.ndarray
    base_pose: tuple
    observed_at_ms: int
    observation_id: str
    robot_self_mask: bytes
    self_filter_model_revision: str


def decode_capture(observation, now_ms, *, max_age_ms=1500):
    if not observation.HasField("rgbd_frame"):
        raise ContractError("raw RGB-D unavailable")
    raw = observation.rgbd_frame
    width, height = raw.width, raw.height
    if not (1 <= width <= 1280 and 1 <= height <= 720):
        raise ContractError("invalid RGB-D dimensions")
    if len(raw.rgb) != width * height * 3 or len(raw.depth_metres_f32) != width * height * 4:
        raise ContractError("invalid RGB-D byte count")
    if (
        not observation.observation_id
        or not -250 <= now_ms - observation.wall_time_unix_ms <= max_age_ms
    ):
        raise ContractError("RGB-D capture stale")
    k = np.asarray(raw.intrinsics, dtype=float)
    transform = np.asarray(raw.base_from_camera, dtype=float)
    if (
        k.shape != (9,)
        or not np.isfinite(k).all()
        or k[0] <= 0
        or k[4] <= 0
        or not np.allclose(k[6:], [0, 0, 1])
    ):
        raise ContractError("invalid camera intrinsics")
    if transform.shape != (16,) or not np.isfinite(transform).all():
        raise ContractError("invalid camera transform")
    transform = transform.reshape(4, 4)
    rotation = transform[:3, :3]
    if (
        not np.allclose(transform[3], [0, 0, 0, 1])
        or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
        or not np.isclose(np.linalg.det(rotation), 1, atol=1e-5)
    ):
        raise ContractError("camera transform must be rigid")
    values = observation.robot_state.fields.get("base_pose")
    entries = list(values.list_value.values) if values is not None else []
    if any(value.WhichOneof("kind") != "number_value" for value in entries):
        raise ContractError("wheel odometry pose must be numeric")
    pose = [value.number_value for value in entries]
    if (
        len(pose) != 7
        or not all(map(finite_number, pose))
        or abs(sum(v * v for v in pose[3:]) - 1) > 1e-5
    ):
        raise ContractError("wheel odometry pose unavailable")
    depth = np.frombuffer(raw.depth_metres_f32, dtype="<f4")
    # Missing returns stay unknown (NaN), never turn into free-space range hits.
    depth = depth.copy()
    depth[(~np.isfinite(depth)) | (depth <= 0) | (depth > 5)] = np.nan
    mask = bytes(raw.robot_self_mask)
    revision = raw.self_filter_model_revision
    if mask:
        if (
            len(mask) != width * height
            or not revision.strip()
            or len(revision) > 256
            or any(value not in (0, 1) for value in mask)
        ):
            raise ContractError("invalid robot self mask or model revision")
        # Only measured returns already matched to the robot's CAD are removed.
        # A missing return is unknown, never a ray endpoint clearing hidden space.
        depth[np.frombuffer(mask, dtype=np.uint8) == 1] = np.nan
    return RawCapture(
        width,
        height,
        bytes(raw.rgb),
        depth.astype("<f4").tobytes(),
        k.reshape(3, 3),
        transform,
        tuple(pose),
        observation.wall_time_unix_ms,
        observation.observation_id,
        mask,
        revision if mask else "",
    )


class VelocityGate:
    """Bind stamped Nav2 outputs to the active accepted goal without retiming."""

    def __init__(self):
        self.goal_id = None
        self.accepted_ms = 0
        self.latest = None

    def accept(self, goal_id, accepted_ms):
        self.goal_id, self.accepted_ms, self.latest = goal_id, accepted_ms, None

    def clear(self, goal_id):
        if goal_id == self.goal_id:
            self.goal_id, self.accepted_ms, self.latest = None, 0, None

    def observe(self, goal_id, values, stamp_ms, now_ms):
        if goal_id != self.goal_id or not goal_id or not self.accepted_ms:
            return False
        if (
            not isinstance(stamp_ms, int)
            or stamp_ms < self.accepted_ms
            or not -250 <= now_ms - stamp_ms <= 250
        ):
            return False
        if len(values) != 3 or not all(map(finite_number, values)):
            return False
        if self.latest and stamp_ms < self.latest["stampUnixMs"]:
            return False
        self.latest = dict(zip(("linearX", "linearY", "angularZ"), values), stampUnixMs=stamp_ms)
        return True

    def read(self, goal_id):
        if goal_id != self.goal_id or self.latest is None:
            return {"linearX": 0.0, "linearY": 0.0, "angularZ": 0.0, "stampUnixMs": 0}
        return dict(self.latest)
