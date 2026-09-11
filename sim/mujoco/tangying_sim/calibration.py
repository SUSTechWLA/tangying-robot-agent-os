"""Simulation's calibration service.

The simulation is not a special case: it publishes the same
``robot.calibration.v1`` document a real unit does, derived from the MuJoCo model
instead of from a person with a servo programmer. Editing the document changes
what the runtime *reports* about its cameras (intrinsics and the camera-to-world
transform attached to every RGB-D capture), which is exactly the quantity a real
unit gets wrong before it is calibrated.

Extrinsics are stored in the optical convention used by the runtime contract
(right/down/forward, see ``robot.profile.v1``), not in MuJoCo's right/up/back, so
the same numbers mean the same thing on hardware.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import mujoco
import numpy as np
from tangying_robot_gateway.calibration import (
    CAMERA_NAMES,
    MOTOR_IDS,
    CalibrationError,
    calibration_revision,
    load_calibration,
    save_calibration,
    validate_calibration,
)

# MuJoCo camera axes (right/up/back) to the contract's optical axes (right/down/forward).
_OPTICAL_FROM_MUJOCO = np.diag([1.0, -1.0, -1.0])

# The contract names cameras the way the runtime and tools do; the model names
# them after the link they are mounted on. Both must resolve to one document key.
MODEL_CAMERA_FOR = {"head-rgbd": "head_depth", "base-rgbd": "base_depth"}

DEFAULT_CALIBRATION_FILENAME = "calibration.json"


def _rotation_to_rpy(rotation: np.ndarray) -> list[float]:
    """Roll/pitch/yaw for ``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`` (extrinsic XYZ).

    Implemented here rather than through MuJoCo so the stored convention is the
    one this project documents, not whatever a bindings version happens to expose.
    """
    pitch = math.asin(max(-1.0, min(1.0, -float(rotation[2, 0]))))
    if abs(rotation[2, 0]) < 0.99999:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:  # Gimbal lock: pitch is +/-90 degrees, fold the rotation into roll.
        roll = math.atan2(-rotation[1, 2], rotation[1, 1])
        yaw = 0.0
    return [roll, pitch, yaw]


def _rpy_to_rotation(rpy: list[float]) -> np.ndarray:
    roll, pitch, yaw = (float(value) for value in rpy)
    cos_r, sin_r = math.cos(roll), math.sin(roll)
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cos_r, -sin_r], [0, sin_r, cos_r]])
    ry = np.array([[cos_p, 0, sin_p], [0, 1, 0], [-sin_p, 0, cos_p]])
    rz = np.array([[cos_y, -sin_y, 0], [sin_y, cos_y, 0], [0, 0, 1]])
    return rz @ ry @ rx


class SimulationCalibration:
    """Owns the active calibration document for one simulation runtime."""

    def __init__(
        self,
        *,
        model: mujoco.MjModel,
        root: str | Path | None,
        robot_id: str,
        cameras: tuple[str, ...] = CAMERA_NAMES,
        framebuffer: tuple[int, int] = (320, 240),
        safety: dict[str, float] | None = None,
    ):
        self.model = model
        # No root means "derive and hold in memory": the default for tests and for
        # runs that must not leave a calibration file behind.
        self.root = Path(root) if root else None
        self.robot_id = robot_id
        self.cameras = tuple(cameras)
        self.framebuffer = framebuffer
        self.path = self.root / DEFAULT_CALIBRATION_FILENAME if self.root else None
        self._safety = safety or {
            "maxRelativeTargetDeg": 8.0,
            "maxActionChunkLength": 64,
            "maxLinearSpeedMPerS": 0.05,
            "maxAngularSpeedRadPerS": 0.2,
        }
        stored = load_calibration(self.path) if self.path else None
        self._document = stored if stored is not None else self.derive()
        self._revision = calibration_revision(self._document)

    # -- derivation ---------------------------------------------------------

    def derive(self) -> dict[str, Any]:
        """Build the document that describes this model, unedited."""
        width, height = self.framebuffer
        cameras: dict[str, Any] = {}
        # Derive from the same world-frame quantities the renderer publishes, then
        # express them relative to the parent link. Going through cam_mat0/cam_pos
        # instead would mean re-deriving MuJoCo's frame conventions here, and a
        # single sign error in that transcription silently rotates every capture.
        data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, data)
        for name in self.cameras:
            model_camera = MODEL_CAMERA_FOR.get(name, name)
            camera_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, model_camera)
            if camera_id < 0:
                continue
            body_id = int(self.model.cam_bodyid[camera_id])
            body_name = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_BODY, body_id) or "chassis"
            focal = 0.5 * height / np.tan(np.deg2rad(self.model.cam_fovy[camera_id]) * 0.5)

            world_from_camera = np.eye(4)
            world_from_camera[:3, :3] = np.array(data.cam_xmat[camera_id]).reshape(3, 3) @ _OPTICAL_FROM_MUJOCO
            world_from_camera[:3, 3] = data.cam_xpos[camera_id]
            world_from_body = np.eye(4)
            world_from_body[:3, :3] = np.array(data.xmat[body_id]).reshape(3, 3)
            world_from_body[:3, 3] = data.xpos[body_id]
            parent_from_camera = np.linalg.inv(world_from_body) @ world_from_camera

            cameras[name] = {
                "sourceId": f"{self.robot_id}/{name}",
                "width": width,
                "height": height,
                "intrinsics": {
                    "fx": float(focal), "fy": float(focal),
                    "cx": (width - 1) * 0.5, "cy": (height - 1) * 0.5,
                },
                "distortion": {"model": "none", "coefficients": []},
                "extrinsics": {
                    "parentLink": body_name,
                    "xyz": [float(value) for value in parent_from_camera[:3, 3]],
                    "rpy": _rotation_to_rpy(parent_from_camera[:3, :3]),
                },
            }
        return validate_calibration({
            "schemaVersion": "robot.calibration.v1",
            "robotId": self.robot_id,
            "adapterId": "mujoco",
            "source": "simulation",
            "updatedAtUnixMs": int(time.time() * 1000),
            "motors": self._derived_motors(),
            "cameras": cameras,
            "geometry": {"gripper": {"openM": 0.081, "closedM": 0.0}},
            "safety": dict(self._safety),
        })

    def _derived_motors(self) -> dict[str, dict[str, int]]:
        """Nominal servo calibration for the canonical 16 motors.

        MuJoCo has no STS3215 registers and no servo zero error, so the simulated
        unit publishes the numbers a freshly programmed unit carries: one id per
        motor, no homing offset, the full encoder range. A real unit replaces
        exactly these fields with measured values, in the same document.
        """
        return {
            name: {"id": servo_id, "drive_mode": 0, "homing_offset": 0,
                   "range_min": 0, "range_max": 4095}
            for name, servo_id in MOTOR_IDS.items()
        }

    # -- inspection ---------------------------------------------------------

    @property
    def document(self) -> dict[str, Any]:
        return self._document

    @property
    def revision(self) -> str:
        return self._revision

    def camera(self, name: str) -> dict[str, Any] | None:
        return self._document["cameras"].get(name)

    def intrinsics(self, name: str) -> np.ndarray | None:
        """3x3 pinhole matrix, rescaled if the document was measured at another size."""
        camera = self.camera(name)
        if camera is None:
            return None
        width, height = self.framebuffer
        scale_x = width / float(camera["width"])
        scale_y = height / float(camera["height"])
        k = camera["intrinsics"]
        return np.array([
            [k["fx"] * scale_x, 0.0, k["cx"] * scale_x],
            [0.0, k["fy"] * scale_y, k["cy"] * scale_y],
            [0.0, 0.0, 1.0],
        ])

    def world_from_camera(self, name: str, data: mujoco.MjData) -> np.ndarray | None:
        """Camera pose from the document, resolved through its parent link."""
        camera = self.camera(name)
        if camera is None:
            return None
        extrinsics = camera["extrinsics"]
        body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, extrinsics["parentLink"])
        if body_id < 0:
            return None
        parent = np.eye(4)
        parent[:3, :3] = data.xmat[body_id].reshape(3, 3)
        parent[:3, 3] = data.xpos[body_id]
        local = np.eye(4)
        # The stored rotation is already the *optical* camera frame relative to
        # the parent link (derivation folded MuJoCo's right/up/back into
        # right/down/forward), so it must not be converted a second time.
        local[:3, :3] = _rpy_to_rotation(extrinsics["rpy"])
        local[:3, 3] = extrinsics["xyz"]
        return parent @ local

    # -- mutation -----------------------------------------------------------

    def replace(self, document: Any, *, expected_revision: str | None = None) -> dict[str, Any]:
        """Validate and adopt a new document, optionally guarded by revision."""
        if expected_revision is not None and expected_revision != self._revision:
            raise CalibrationError(
                "REVISION_CONFLICT",
                f"calibration changed since it was read (expected {expected_revision[:12]}, "
                f"found {self._revision[:12]}); reload before saving",
            )
        candidate = validate_calibration({**document, "updatedAtUnixMs": int(time.time() * 1000)})
        if candidate["robotId"] != self.robot_id:
            raise CalibrationError("ROBOT_MISMATCH",
                                   f"calibration is for {candidate['robotId']}, this runtime is {self.robot_id}")
        self._document = candidate
        self._revision = calibration_revision(candidate)
        if self.path is not None:
            save_calibration(self.path, candidate, expected_revision=expected_revision)
        return candidate

    def reset_to_model(self) -> dict[str, Any]:
        """Discard edits and re-derive from the model."""
        derived = self.derive()
        self._document = derived
        self._revision = calibration_revision(derived)
        if self.path is not None:
            self.path.unlink(missing_ok=True)
        return derived
