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

#: How far a stored document may be from the model and still describe it. A stored
#: document *wins* over the model, so these are the numbers that decide whether the
#: stack projects every capture through this robot's camera or through numbers
#: somebody typed. The defect this guards against was 120.5° of rotation, three
#: orders above the rotation tolerance; a tolerance tight enough to catch that and
#: loose enough to survive a real unit's own lens being written down is the honest
#: range, and these sit inside it.
MODEL_LENS_TOLERANCE = 0.02
MODEL_PRINCIPAL_POINT_TOLERANCE = 0.02
MODEL_MOUNT_POSITION_TOLERANCE_M = 0.005
MODEL_MOUNT_ROTATION_TOLERANCE_DEG = 2.0


def _rotation_angle_degrees(rotation: np.ndarray) -> float:
    """How far apart two rotations are, in degrees, without going through Euler."""
    cosine = max(-1.0, min(1.0, (float(np.trace(rotation)) - 1.0) / 2.0))
    return math.degrees(math.acos(cosine))


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
        if stored is not None and stored["robotId"] != self.robot_id:
            # A stored document wins over the model, so adopting the wrong one means
            # projecting every capture through another robot's camera. The same check
            # already guards `replace`; loading was the way around it. Refusing costs
            # a clear error at start-up; adopting costs a plausible wrong answer
            # forever, which is the worse trade.
            raise CalibrationError(
                "ROBOT_MISMATCH",
                f"{self.path} is calibration for {stored['robotId']}, "
                f"this runtime is {self.robot_id}",
            )
        self._document = stored if stored is not None else self.derive()
        if stored is not None:
            # The same argument one step further: the right robot's *numbers* can
            # still describe a different camera. Nothing downstream can tell, because
            # every consumer of the document is asking it where the camera is.
            self._verify_against_model(stored)
        self._revision = calibration_revision(self._document)

    # -- the start-up self-check --------------------------------------------

    def _verify_against_model(self, document: dict[str, Any]) -> None:
        """Refuse a stored document whose geometry is not the model's.

        Loading is the last moment at which this is cheap: after it, the document is
        what every RGB-D capture's pose is computed from, and a wrong number there is
        indistinguishable from a right one all the way downstream. So it is checked
        here, against the model, in the construction path — not only in a test, and
        not only when someone remembers to compare.

        Two things are deliberately *not* errors:

        * a camera the model does not have (a real unit's extra camera has no model
          geometry to contradict);
        * the extrinsics of a camera the model aims itself
          (``cam_mode != mjCAMLIGHT_FIXED``, e.g. ``head_depth``, which is
          ``mode="targetbody"``). No fixed parent-relative rotation can describe such
          a camera, ``world_from_camera`` already takes its pose from the model for
          exactly that reason, and flagging it would refuse every document the
          simulation itself can produce.
        """
        derived = self.derive()["cameras"]
        for name in sorted(document.get("cameras") or {}):
            stored = document["cameras"][name]
            camera_id = mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_CAMERA, MODEL_CAMERA_FOR.get(name, name))
            expected = derived.get(name)
            if camera_id < 0 or expected is None:
                continue
            self._verify_lens(name, stored, camera_id)
            if int(self.model.cam_mode[camera_id]) != int(mujoco.mjtCamLight.mjCAMLIGHT_FIXED):
                continue
            self._verify_mount(name, stored, expected)

    def _verify_lens(self, name: str, stored: dict[str, Any], camera_id: int) -> None:
        """The document's lens against ``cam_fovy`` at the document's own frame size.

        At the document's own size, not the runtime's framebuffer: a document measured
        at another resolution is rescaled by ``intrinsics()`` and is not contradicting
        anything. What it may not do is describe a different lens.
        """
        height, width = stored["height"], stored["width"]
        focal = 0.5 * height / math.tan(math.radians(float(self.model.cam_fovy[camera_id])) / 2.0)
        for key, reference, tolerance in (
                ("fx", focal, MODEL_LENS_TOLERANCE * focal),
                ("fy", focal, MODEL_LENS_TOLERANCE * focal),
                ("cx", (width - 1) * 0.5, MODEL_PRINCIPAL_POINT_TOLERANCE * width),
                ("cy", (height - 1) * 0.5, MODEL_PRINCIPAL_POINT_TOLERANCE * height)):
            claimed = float(stored["intrinsics"][key])
            difference = abs(claimed - reference)
            if difference > tolerance:
                raise CalibrationError(
                    "DOCUMENT_CONTRADICTS_MODEL",
                    f"{self.path}: camera {name} declares {key}={claimed} but this model's "
                    f"camera renders with {key}={reference:.4f} at {width}x{height} "
                    f"(off by {difference:.4f}, allowed {tolerance:.4f}). Every frame would "
                    "be projected through the wrong lens; re-derive the document from the "
                    "model, or delete it.",
                )

    def _verify_mount(self, name: str, stored: dict[str, Any], expected: dict[str, Any]) -> None:
        """The document's fixed mount against the model's, for a camera the model fixes."""
        claimed = stored["extrinsics"]
        model = expected["extrinsics"]
        if claimed["parentLink"] != model["parentLink"]:
            raise CalibrationError(
                "DOCUMENT_CONTRADICTS_MODEL",
                f"{self.path}: camera {name} is mounted on {claimed['parentLink']!r} "
                f"but the model mounts it on {model['parentLink']!r}. Every capture's pose "
                "would be computed from the wrong link; re-derive the document from the "
                "model, or delete it.",
            )
        position = np.asarray(claimed["xyz"], dtype=float)
        rotation = _rpy_to_rotation(claimed["rpy"])
        offset = float(np.linalg.norm(position - np.asarray(model["xyz"], dtype=float)))
        angle = _rotation_angle_degrees(rotation.T @ _rpy_to_rotation(model["rpy"]))
        if offset > MODEL_MOUNT_POSITION_TOLERANCE_M:
            raise CalibrationError(
                "DOCUMENT_CONTRADICTS_MODEL",
                f"{self.path}: camera {name} is mounted at {claimed['xyz']} but the model "
                f"puts it at {model['xyz']} of {model['parentLink']} "
                f"(off by {offset * 1000:.1f} mm, allowed "
                f"{MODEL_MOUNT_POSITION_TOLERANCE_M * 1000:.0f} mm). Re-derive the document "
                "from the model, or delete it.",
            )
        if angle > MODEL_MOUNT_ROTATION_TOLERANCE_DEG:
            raise CalibrationError(
                "DOCUMENT_CONTRADICTS_MODEL",
                f"{self.path}: camera {name} is rotated {angle:.3f}° away from where this "
                f"model renders it (allowed {MODEL_MOUNT_ROTATION_TOLERANCE_DEG:.0f}°). "
                "The runtime publishes this pose on every RGB-D capture, so a document that "
                "disagrees with the renderer is worse than no document; re-derive it from "
                "the model, or delete it.",
            )

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
        """Camera pose from the document, resolved through its parent link.

        With one exception, and it is the whole reason this method is not a one-liner.
        ``head_depth`` is declared ``mode="targetbody"``: MuJoCo recomputes its
        orientation on every step to look at the body it was pointed at. No fixed
        parent-relative rotation can describe that, so a stored one is a snapshot —
        exact at the pose it was derived from and wrong by tens of degrees at every
        other pose. For such a camera the pose comes from the model, because the
        model is what rendered the image; the document still supplies the intrinsics
        and names the parent link, and those parts are unaffected.

        Without this, a document whose head rotation was written by hand (as the
        stored per-unit ones were, with the base camera's yaw) silently put the head
        camera more than a right angle away from where its depth image was taken, and
        the runtime published that as the capture's pose.
        """
        camera = self.camera(name)
        if camera is None:
            return None
        extrinsics = camera["extrinsics"]
        model_camera = MODEL_CAMERA_FOR.get(name, name)
        camera_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_CAMERA, model_camera)
        if camera_id >= 0 and int(self.model.cam_mode[camera_id]) != int(
                mujoco.mjtCamLight.mjCAMLIGHT_FIXED):
            aim = np.eye(4)
            aim[:3, :3] = (np.array(data.cam_xmat[camera_id]).reshape(3, 3)
                           @ _OPTICAL_FROM_MUJOCO)
            aim[:3, 3] = data.cam_xpos[camera_id]
            return aim
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
        if self.path is not None:
            # The initial derived document may not have been written yet. The
            # in-memory revision above remains the compare-and-swap authority.
            save_calibration(self.path, candidate,
                             expected_revision=expected_revision if self.path.exists() else "")
        self._document = candidate
        self._revision = calibration_revision(candidate)
        return candidate

    def reset_to_model(self) -> dict[str, Any]:
        """Discard edits and re-derive from the model."""
        derived = self.derive()
        self._document = derived
        self._revision = calibration_revision(derived)
        if self.path is not None:
            self.path.unlink(missing_ok=True)
        return derived
