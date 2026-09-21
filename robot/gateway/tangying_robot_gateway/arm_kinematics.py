"""Forward kinematics for the XLeRobot arms, and the servo counts that drive it.

Why this module exists
---------------------
Hand-eye calibration is ``AX = XB``, and ``A`` is how the arm's end link moved
between two views. That requires a kinematic model. This repository has exactly
one, and it is the MuJoCo model the simulator renders from. The numbers below are
a frozen copy of it (``assets/arm_kinematics.json``, written by
``scripts/generate_arm_kinematics.py``), because the gateway runs on the robot
where the scene XML is not shipped; the copy is compared against the live model by
``sim/mujoco/tests/test_arm_kinematics_ground_truth.py``.

What that means for a real unit
-------------------------------
The link lengths are the *simulated* unit's. A hand-eye solve against a real unit
whose arm is built to different dimensions inherits whatever the difference is, and
nothing downstream could tell. This limitation is stated rather than hidden: it is
the same one the wizard already has, because the wizard's zero pose is the model's
zero pose.

Counts to angles, and why this mapping
--------------------------------------
A view records raw STS3215 encoder counts. Three facts pin the conversion, and all
three are in this repository:

* **One turn is 4096 counts.** ``XLeRobot/software/src/robots/xlerobot/xlerobot.py:360``
  converts degrees with ``steps_per_deg = 4096.0 / 360.0``, and
  ``robot/ros2_ws/src/xlerobot_adapter/xlerobot_adapter/calibration.py:28`` accepts a
  wheel calibration only when ``range_min == 0 and range_max == 4095``, which it
  calls "one complete revolution".
* **Count ``2048 + homing_offset`` is the joint's zero.** The wizard *defines* it
  that way at its zero step: ``calibration_wizard.py:635`` stores
  ``homing_offset = position - 2048`` from the count read while the operator holds
  the joint at the pose ``JOINT_GUIDE`` describes (``calibration_wizard.py:53``).
* **That zero pose is the model's assembled pose**, i.e. MuJoCo ``qpos = 0``. This
  is the one claim that could be false without anyone noticing, so it is checked
  against the model instead of assumed: at ``qpos = 0`` the elbow-to-wrist segment
  is 2.2° from horizontal ("小臂伸直，与地面平行"), the wrist-to-jaw segment is
  exactly horizontal ("手腕保持水平"), the arm lies along the chassis' forward axis
  ("正对机器人正前方"), and the jaw closes in the vertical plane ("两个指头上下相对").
  The alternative reading — zero at the middle of the joint's range — puts the upper
  arm 96° from the ground, which is not the pose the wizard asks for. See
  ``test_the_zero_pose_the_wizard_asks_for_is_the_models_assembled_pose``.

So ``q = (count - 2048 - homing_offset) * 2π / 4096``, with the model's joint range
choosing the branch. The branch matters: ``shoulder_lift`` reaches 3.45 rad
(197.7°) from zero, which is past half a turn, so its upper end reads as a count
below 2048. A single-turn absolute encoder cannot say which turn it is in — the
joint's own mechanical limits can, and they are in the table for exactly this
reason. A reading that fits no turn inside the model's range is refused rather than
wrapped into a confident wrong angle.

``drive_mode`` is refused when nonzero. The pinned hardware requires it to be 0 for
every motor (``robot/ros2_ws/src/xlerobot_adapter/xlerobot_adapter/calibration.py:22``),
and the sign convention of a *flipped* drive cannot be checked against anything in
this repository — inventing it would put a mirrored extrinsic on a real robot.

Only numpy is used, and MuJoCo is deliberately not imported: the table above is the
whole dependency.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from tangying_robot_gateway.calibration import ARM_JOINTS, ARM_SIDES

__all__ = [
    "COUNTS_PER_TURN",
    "SCHEMA_VERSION",
    "TABLE_PATH",
    "ZERO_COUNT",
    "ArmLink",
    "KinematicsError",
    "all_links",
    "arm_link_poses",
    "arm_links",
    "base_pose",
    "chain_poses",
    "joint_angles_from_counts",
    "link_pose",
    "resolve_link",
    "table_provenance",
]

SCHEMA_VERSION = "robot.arm_kinematics.v1"
TABLE_PATH = Path(__file__).with_name("assets") / "arm_kinematics.json"

#: The STS3215's absolute encoder resolution and the count the wizard treats as the
#: middle of the travel. Both are hardware facts, not tunables.
COUNTS_PER_TURN = 4096
ZERO_COUNT = 2048

_TAU = 2.0 * math.pi


class KinematicsError(Exception):
    """A refusal a person can act on, in the same shape the solver raises."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ArmLink:
    """One driven body of one arm, and the joint that moves it."""

    side: str
    index: int
    link: str
    motor: str
    joint: str
    body: str
    parent_body: str
    joint_name: str
    position: tuple[float, float, float]
    quaternion: tuple[float, float, float, float]
    axis: tuple[float, float, float]
    anchor: tuple[float, float, float]
    range_min: float
    range_max: float

    @property
    def range_span(self) -> float:
        return self.range_max - self.range_min


def _load_table() -> dict[str, Any]:
    with TABLE_PATH.open(encoding="utf-8") as handle:
        table = json.load(handle)
    if table.get("schemaVersion") != SCHEMA_VERSION:
        raise KinematicsError(
            "KINEMATICS_TABLE_INVALID",
            f"{TABLE_PATH} is {table.get('schemaVersion')!r}, expected {SCHEMA_VERSION!r}; "
            "regenerate it with scripts/generate_arm_kinematics.py",
        )
    return table


_TABLE = _load_table()


def table_provenance() -> dict[str, Any]:
    """Where the frozen link geometry came from, for evidence and for reports."""
    return dict(_TABLE["provenance"])


def _links() -> dict[str, tuple[ArmLink, ...]]:
    arms: dict[str, tuple[ArmLink, ...]] = {}
    for side in ARM_SIDES:
        entries = _TABLE["arms"][side]
        if len(entries) != len(ARM_JOINTS):
            raise KinematicsError(
                "KINEMATICS_TABLE_INVALID",
                f"the table has {len(entries)} {side} links, ARM_JOINTS has {len(ARM_JOINTS)}",
            )
        arms[side] = tuple(
            ArmLink(
                side=side,
                index=int(entry["index"]),
                link=str(entry["link"]),
                motor=str(entry["motor"]),
                joint=str(entry["joint"]),
                body=str(entry["body"]),
                parent_body=str(entry["parentBody"]),
                joint_name=str(entry["jointName"]),
                position=tuple(float(value) for value in entry["position"]),
                quaternion=tuple(float(value) for value in entry["quaternion"]),
                axis=tuple(float(value) for value in entry["axis"]),
                anchor=tuple(float(value) for value in entry["anchor"]),
                range_min=float(entry["rangeMin"]),
                range_max=float(entry["rangeMax"]),
            )
            for entry in entries
        )
    return arms


_ARMS = _links()

#: Everything a document is allowed to name as ``extrinsics.parentLink``, mapped to
#: the link it means: the canonical chain names, plus the model's own body and joint
#: names so a document derived from the simulation resolves without translation.
_BY_NAME: dict[str, ArmLink] = {}
for _side_links in _ARMS.values():
    for _link in _side_links:
        for _name in (_link.link, _link.body, _link.joint_name):
            _BY_NAME.setdefault(_name, _link)
del _side_links, _link, _name


def arm_links(side: str) -> tuple[ArmLink, ...]:
    """One arm's links, in chain order: index *i* is driven by servo id *i*."""
    if side not in _ARMS:
        raise KinematicsError(
            "UNKNOWN_ARM_SIDE", f"没有名为 {side!r} 的手臂；只有 {', '.join(ARM_SIDES)}")
    return _ARMS[side]


def all_links() -> tuple[ArmLink, ...]:
    return tuple(link for side in ARM_SIDES for link in _ARMS[side])


def resolve_link(name: Any) -> ArmLink:
    """The link a calibration document's ``parentLink`` names, or a refusal."""
    if isinstance(name, str) and name in _BY_NAME:
        return _BY_NAME[name]
    known = ", ".join(sorted(_BY_NAME))
    raise KinematicsError(
        "UNKNOWN_PARENT_LINK",
        f"运动学表里没有名为 {name!r} 的连杆，无法把它换算成手臂末端的位姿。"
        f"可以用的名字有：{known}。",
    )


def _base_pose() -> np.ndarray:
    base = _TABLE["base"]
    pose = np.eye(4)
    pose[:3, :3] = _quaternion_matrix(base["quaternion"])
    pose[:3, 3] = np.asarray(base["position"], dtype=float)
    return pose


def base_pose() -> np.ndarray:
    """The arm's base link in the model's world frame.

    Hand-eye only ever uses *relative* motions of the end link, so this cancels —
    which is why a solve does not care where the robot was standing.
    """
    return _base_pose()


def _quaternion_matrix(quaternion: Sequence[float]) -> np.ndarray:
    """Rotation matrix from ``(w, x, y, z)``, the order MuJoCo stores."""
    w, x, y, z = (float(value) for value in quaternion)
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm < 1e-12:
        raise KinematicsError("KINEMATICS_TABLE_INVALID", "表中的四元数为零，无法构成旋转")
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _translate(vector: Sequence[float]) -> np.ndarray:
    matrix = np.eye(4)
    matrix[:3, 3] = np.asarray(vector, dtype=float)
    return matrix


def _hinge(link: ArmLink, angle: float) -> np.ndarray:
    """MuJoCo's hinge transform: the body turns about ``axis`` through ``anchor``.

    The rotation is about the anchor, not about the body origin, and both are
    expressed in the body's own frame — so the anchor stays put and the body origin
    swings around it. Every arm joint in this model happens to have its anchor at
    the body origin, which makes the two readings identical here; the anchored form
    is used anyway because it is the one MuJoCo documents, and
    ``test_forward_kinematics_matches_mujoco_for_a_moved_anchor`` builds a model
    where the two differ to prove this implementation is the right one.
    """
    axis = np.asarray(link.axis, dtype=float)
    norm = float(np.linalg.norm(axis))
    if norm < 1e-12:
        raise KinematicsError("KINEMATICS_TABLE_INVALID", f"{link.link} 的关节轴为零向量")
    axis = axis / norm
    cos, sin = math.cos(angle), math.sin(angle)
    cross = np.array([
        [0.0, -axis[2], axis[1]],
        [axis[2], 0.0, -axis[0]],
        [-axis[1], axis[0], 0.0],
    ])
    rotation = np.eye(3) + sin * cross + (1.0 - cos) * (cross @ cross)
    anchor = np.asarray(link.anchor, dtype=float)
    hinge = np.eye(4)
    hinge[:3, :3] = rotation
    hinge[:3, 3] = anchor - rotation @ anchor
    return hinge


def _angle_for(link: ArmLink, angles: Mapping[str, float]) -> float:
    for key in (link.motor, link.joint, link.link, link.body, link.joint_name):
        if key in angles:
            value = angles[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise KinematicsError(
                    "INVALID_JOINT_ANGLE", f"{link.motor} 的角度不是数值：{value!r}")
            if not math.isfinite(float(value)):
                raise KinematicsError(
                    "INVALID_JOINT_ANGLE", f"{link.motor} 的角度不是有限值：{value!r}")
            return float(value)
    raise KinematicsError(
        "MISSING_JOINT_ANGLE",
        f"缺少 {link.motor}（{link.link} 的关节）的角度，无法算出这条手臂的正运动学",
    )


def chain_poses(links: Sequence[ArmLink], angles: Mapping[str, float],
                *, base: np.ndarray | None = None) -> tuple[np.ndarray, ...]:
    """Forward kinematics for an explicit chain of links.

    Public rather than private because the composition rule is the part that has to
    be right, and the only way to show that it is the rule MuJoCo uses is to run it
    on a model this repository does not ship — one whose joints are anchored away
    from their body origins, where a wrong reading of ``jnt_pos`` shows up.
    ``test_forward_kinematics_matches_mujoco_for_a_moved_anchor`` does exactly that.
    """
    if base is None:
        pose = _base_pose()
    else:
        pose = np.array(base, dtype=float)
        if pose.shape != (4, 4):
            raise KinematicsError("INVALID_BASE_POSE", "手臂基座位姿必须是 4x4 矩阵")
    poses: list[np.ndarray] = []
    for link in links:
        pose = pose @ _translate(link.position) @ _static_rotation(link) @ _hinge(
            link, _angle_for(link, angles))
        poses.append(pose.copy())
    return tuple(poses)


def arm_link_poses(side: str, angles: Mapping[str, float],
                   *, base: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """Every link pose of one arm, in the model's world frame by default.

    ``angles`` is keyed by motor name (``left_arm_elbow_flex``); the joint, link and
    model body names are accepted too, because a session records motors while a
    document names links.
    """
    links = arm_links(side)
    return {link.link: pose for link, pose in zip(links, chain_poses(links, angles, base=base))}


def _static_rotation(link: ArmLink) -> np.ndarray:
    matrix = np.eye(4)
    matrix[:3, :3] = _quaternion_matrix(link.quaternion)
    return matrix


def link_pose(link: Any, angles: Mapping[str, float],
              *, base: np.ndarray | None = None) -> np.ndarray:
    """The pose of one named link at the given joint angles."""
    resolved = resolve_link(link)
    return arm_link_poses(resolved.side, angles, base=base)[resolved.link]


def joint_angles_from_counts(
    counts: Mapping[str, Any],
    motors: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, float]:
    """Raw encoder counts to joint angles in radians.

    ``counts`` is a capture record's ``pose``: ``{"left_arm_elbow_flex": 2401, …}``.
    ``motors`` is the motor half of the calibration document in force
    (``homing_offset``, ``drive_mode`` per motor); passing ``None`` means the
    nominal unit the simulator publishes — every ``homing_offset`` 0, which is the
    same as saying "the arm was zeroed at the encoder's half turn".
    """
    if not isinstance(counts, Mapping) or not counts:
        raise KinematicsError("VIEW_INCOMPLETE", "这个视角没有记录手臂的舵机读数")
    angles: dict[str, float] = {}
    for motor, count in counts.items():
        link = _link_for_motor(motor)
        entry = _motor_entry(motors, motor)
        drive_mode = entry.get("drive_mode", 0)
        if drive_mode != 0:
            raise KinematicsError(
                "UNSUPPORTED_DRIVE_MODE",
                f"{motor} 的 drive_mode={drive_mode!r}：反向驱动的符号约定在本仓库里"
                "没有任何东西可以核对，照猜会让相机外参整体镜像，因此拒绝解算。",
            )
        homing = entry.get("homing_offset", 0)
        if isinstance(homing, bool) or not isinstance(homing, int):
            raise KinematicsError(
                "MOTOR_CALIBRATION_INVALID", f"{motor} 的 homing_offset 不是整数：{homing!r}")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count < COUNTS_PER_TURN:
            raise KinematicsError(
                "INVALID_ENCODER_READING",
                f"{motor} 的读数是 {count!r}，不是 0..{COUNTS_PER_TURN - 1} 之间的整数",
            )
        angles[motor] = _unwrap(link, (count - ZERO_COUNT - homing) * _TAU / COUNTS_PER_TURN, count)
    return angles


def _unwrap(link: ArmLink, raw: float, count: int) -> float:
    """Pick the turn that lands inside the joint's mechanical range, or refuse.

    A 12-bit absolute encoder reports one turn and cannot say which one. The joint's
    own limits can: they are in the model, they span at most a turn, and a reading
    that fits no turn inside them is a reading this model cannot explain. Wrapping it
    silently would hand FK an angle that is wrong by a whole turn and produce a
    confident, wrong camera mount — so the check is what makes the conversion
    falsifiable rather than merely arithmetic.
    """
    # A reading has a resolution of one count, so a count that puts the joint half a
    # count outside its stop is *at* the stop: the encoder cannot say finer than
    # that, and refusing there would reject the extreme poses an operator reaches by
    # hand. Half a count is a hardware quantity, not a fudge factor.
    epsilon = _TAU / COUNTS_PER_TURN / 2.0 + 1e-9
    low = math.ceil((link.range_min - epsilon - raw) / _TAU)
    high = math.floor((link.range_max + epsilon - raw) / _TAU)
    if low > high:
        raise KinematicsError(
            "READING_OUTSIDE_JOINT_RANGE",
            f"{link.motor} 读到 {count}，换算出的角度 ({math.degrees(raw):.1f}°) "
            f"不在模型给出的关节范围 "
            f"[{math.degrees(link.range_min):.1f}°, {math.degrees(link.range_max):.1f}°] 内。"
            "要么这条手臂的零点不是模型装配姿态，要么读数与这台机器人不符。",
        )
    # Two candidates can only occur when the range spans a whole turn (the wrist
    # roll and the jaw do, at ±180°). They differ by exactly that turn, and a hinge
    # rotated by a whole turn is the same pose — so this is not a choice between two
    # answers, and taking the lower one loses nothing.
    angle = raw + _TAU * low
    return min(max(angle, link.range_min), link.range_max)


def _link_for_motor(motor: Any) -> ArmLink:
    for link in all_links():
        if link.motor == motor:
            return link
    raise KinematicsError(
        "UNKNOWN_MOTOR",
        f"读数里有 {motor!r}，但它不是这两条手臂的关节。"
        f"手眼标定只需要手臂上的舵机（例如 left_arm_elbow_flex）。",
    )


def _motor_entry(motors: Mapping[str, Mapping[str, Any]] | None, motor: str) -> Mapping[str, Any]:
    if motors is None:
        return {}
    entry = motors.get(motor)
    if entry is None:
        raise KinematicsError(
            "MOTOR_CALIBRATION_MISSING",
            f"标定文档里没有 {motor} 的条目，无法知道它的零点偏移，"
            "因此无法把读数换算成角度。",
        )
    if not isinstance(entry, Mapping):
        raise KinematicsError(
            "MOTOR_CALIBRATION_INVALID", f"{motor} 的标定条目不是一组字段：{entry!r}")
    return entry
