"""First-return robot self filtering from robot-only CAD and capture-time FK.

No environment model, object registry or segmentation identifiers enter this
module. A pixel is marked only when measured depth agrees with the first robot
surface along that exact calibrated ray. Consumers invalidate marked pixels;
they must never turn them into free-space measurements or modify the raw image.
The same scheme transfers to hardware with commissioned CAD, camera extrinsics
and synchronized joint encoders. Missing state disables the filter by failing.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np
from tangying_robot_gateway.rgbd import RgbdFrame, validate_frame

from .model import MODEL_REVISION, TASK_MODEL_PATH

BASE_JOINT_NAMES = frozenset({"slide_joint_x", "slide_joint_y", "hinge_joint_z"})
ROBOT_XML_PATH = TASK_MODEL_PATH.parent / "xlerobot" / "xlerobot.xml"
SELF_FILTER_MODEL_REVISION = f"xlerobot-{MODEL_REVISION}-first-return-v1"


def robot_joint_positions(model, data):
    """Read only scalar encoders attached to chassis from an atomic state copy."""
    chassis = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "chassis")
    if chassis < 0:
        raise ValueError("robot joint capture requires chassis")
    attached = {chassis}
    for body in range(1, model.nbody):
        if int(model.body_parentid[body]) in attached:
            attached.add(body)
    return {model.joint(joint).name: float(data.qpos[model.jnt_qposadr[joint]])
            for joint in range(model.njnt)
            if int(model.jnt_bodyid[joint]) in attached
            and model.joint(joint).name not in BASE_JOINT_NAMES
            and model.jnt_type[joint] in (mujoco.mjtJoint.mjJNT_HINGE, mujoco.mjtJoint.mjJNT_SLIDE)}


@dataclass(frozen=True)
class SelfFilterResult:
    mask: np.ndarray
    model_revision: str


def _base_transform(pose):
    if not isinstance(pose, (list, tuple)) or len(pose) != 7:
        raise ValueError("base pose must be world XYZ and wxyz quaternion")
    if any(isinstance(x, (bool, np.bool_)) or not isinstance(x, (int, float, np.integer, np.floating))
           or not math.isfinite(x) for x in pose):
        raise ValueError("base pose requires finite numeric components")
    values = np.asarray(pose, dtype=float)
    if not math.isclose(float(values[3:] @ values[3:]), 1., abs_tol=1e-5):
        raise ValueError("base quaternion must be normalized")
    transform = np.eye(4)
    rotation = np.empty(9)
    mujoco.mju_quat2Mat(rotation, values[3:])
    transform[:3, :3], transform[:3, 3] = rotation.reshape(3, 3), values[:3]
    return transform


class RobotSelfFilter:
    MAX_PIXELS = 1_048_576

    def __init__(self, *, robot_xml: Path | None = None, model_revision: str | None = None,
                 depth_tolerance_m: float = .002):
        if (isinstance(depth_tolerance_m, bool) or not math.isfinite(depth_tolerance_m)
                or not 0 < depth_tolerance_m <= .005):
            raise ValueError("self-filter depth tolerance must be within (0, .005] metres")
        if robot_xml is not None and not model_revision:
            raise ValueError("custom robot CAD requires a model revision")
        self.model_revision = model_revision or SELF_FILTER_MODEL_REVISION
        if not isinstance(self.model_revision, str) or not self.model_revision.strip():
            raise ValueError("robot self-filter model revision is required")
        spec = mujoco.MjSpec.from_file(str(robot_xml or ROBOT_XML_PATH))
        # The upstream CAD's camera has a workcell target. Its pose is not used:
        # optical transforms come from the capture's actual calibrated camera.
        # Clear only this reference so the standalone robot CAD can compile.
        for camera in spec.cameras:
            camera.targetbody = ""
            camera.mode = mujoco.mjtCamLight.mjCAMLIGHT_FIXED
        self.model = spec.compile()
        chassis = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "chassis")
        if chassis < 0:
            raise ValueError("robot-only CAD must contain a chassis root")
        attached = {chassis}
        for body in range(1, self.model.nbody):
            if int(self.model.body_parentid[body]) in attached:
                attached.add(body)
        if len(attached) != self.model.nbody - 1 or any(
            int(body) not in attached for body in self.model.geom_bodyid
        ):
            raise ValueError("self filtering requires robot-only attached CAD geometry")
        joints = []
        for joint in range(self.model.njnt):
            name = self.model.joint(joint).name
            if not name or self.model.jnt_type[joint] not in (
                mujoco.mjtJoint.mjJNT_HINGE, mujoco.mjtJoint.mjJNT_SLIDE
            ):
                raise ValueError("self-filter CAD requires named scalar encoder joints")
            if name in BASE_JOINT_NAMES:
                if self.model.jnt_bodyid[joint] != chassis:
                    raise ValueError("base virtual joints must be attached to chassis")
                continue
            joints.append((name, int(self.model.jnt_qposadr[joint])))
        self.required_joint_names = tuple(name for name, _ in joints)
        self._joints = tuple(joints)
        self._chassis = chassis
        self._data = mujoco.MjData(self.model)
        # Match the camera renderer's visual geom groups, not invisible collision
        # hulls. A broad collision approximation cannot identify real pixels.
        self._geom_groups = mujoco.MjvOption().geomgroup.copy()
        self._tolerance = depth_tolerance_m
        self._lock = threading.Lock()
        self._closed = False

    def filter(self, frame: RgbdFrame, joint_positions: dict, base_pose, *,
               joints_observed_at_unix_ms: int) -> SelfFilterResult:
        validate_frame(frame)
        if (type(joints_observed_at_unix_ms) is not int
                or joints_observed_at_unix_ms != frame.captured_at_unix_ms):
            raise ValueError("joint state must belong to the same capture as RGB-D")
        if not isinstance(joint_positions, dict):
            raise TypeError("capture-time joint encoder positions are required")
        values = []
        for name, _ in self._joints:
            value = joint_positions.get(name)
            if (isinstance(value, (bool, np.bool_))
                    or not isinstance(value, (int, float, np.integer, np.floating))
                    or not math.isfinite(value)):
                raise ValueError(f"missing or invalid capture-time encoder joint {name}")
            values.append(float(value))
        actual_base = _base_transform(base_pose)
        height, width = frame.depth_m.shape
        if height * width > self.MAX_PIXELS:
            raise ValueError("self-filter image exceeds bounded pixel count")
        row, column = np.indices((height, width))
        k = frame.intrinsics
        rays = np.stack(((column-k[0, 2])/k[0, 0], (row-k[1, 2])/k[1, 1],
                         np.ones((height, width))), axis=-1).reshape(-1, 3)
        rays /= np.linalg.norm(rays, axis=1)[:, None]
        optical_z = rays[:, 2].copy()
        with self._lock:
            if self._closed:
                raise RuntimeError("robot self filter is closed")
            self._data.qpos[:] = self.model.qpos0
            for (_, address), value in zip(self._joints, values, strict=True):
                self._data.qpos[address] = value
            mujoco.mj_forward(self.model, self._data)
            canonical_base = np.eye(4)
            canonical_base[:3, :3] = self._data.xmat[self._chassis].reshape(3, 3)
            canonical_base[:3, 3] = self._data.xpos[self._chassis]
            camera = canonical_base @ np.linalg.inv(actual_base) @ frame.world_from_camera
            directions = rays @ camera[:3, :3].T
            distances = np.full(len(rays), -1., dtype=np.float64)
            measured = frame.depth_m.ravel()
            candidates = np.isfinite(measured) & (measured > .02) & (measured <= 5.)
            if self.model.ngeom and np.all(self.model.geom_rbound > 0):
                # A measured endpoint outside every robot geom's enclosing
                # sphere cannot match a robot surface. Use only a conservative
                # broad-phase rejection; all candidates still get exact rays.
                # Holes/foreground objects inside the envelope are not masked.
                margin = self._tolerance / optical_z.min()
                lower = (self._data.geom_xpos - self.model.geom_rbound[:, None]).min(axis=0) - margin
                upper = (self._data.geom_xpos + self.model.geom_rbound[:, None]).max(axis=0) + margin
                with np.errstate(invalid="ignore"):
                    endpoints = camera[:3, 3] + directions * (measured / optical_z)[:, None]
                candidates &= ((endpoints >= lower) & (endpoints <= upper)).all(axis=1)
            indices = np.flatnonzero(candidates)
            if len(indices):
                hits = np.full(len(indices), -1., dtype=np.float64)
                geom_ids = np.full(len(indices), -1, dtype=np.int32)
                mujoco.mj_multiRay(self.model, self._data, np.ascontiguousarray(camera[:3, 3]),
                                   np.ascontiguousarray(directions[indices]).ravel(), self._geom_groups,
                                   True, -1, geom_ids, hits, None, len(indices),
                                   5. * float(1. / optical_z.min()))
                distances[indices] = hits
        predicted = (distances * optical_z).reshape(height, width)
        measured = frame.depth_m
        matched = (np.isfinite(measured) & (measured > .02) & (measured <= 5.)
                   & (predicted > .02) & (np.abs(measured-predicted) <= self._tolerance))
        # FK/ray work consumes the original capture budget; it never refreshes it.
        validate_frame(frame)
        return SelfFilterResult(matched.astype(np.uint8), self.model_revision)

    def close(self):
        with self._lock:
            self._closed = True
