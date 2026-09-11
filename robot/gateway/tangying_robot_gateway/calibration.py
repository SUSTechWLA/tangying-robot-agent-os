"""Per-unit robot calibration: one document, one service, simulation and hardware alike.

Why this exists
---------------
A robot *model* and a robot *unit* are different things. The model says an arm
joint is a revolute joint between -2.1 and 2.1 rad; the unit on the bench has its
own servo zero offsets, its own gear backlash, its own camera mounted a few
millimetres off, with its own lens. Everything downstream — grounding, grasp
planning, arrival checks, evidence — is wrong until the unit's numbers are
applied, and neither the agent nor the task layer may care whether those numbers
came from a MuJoCo model or from a person turning a servo by hand.

So calibration is a **runtime-owned** document:

* the runtime publishes it and applies it; the agent only sees the contract
  (``robot.profile.v1`` + observations + the calibration revision that produced
  them), never a manufacturer or a simulator;
* simulation derives its document from the MuJoCo model, so the same fields are
  visible, editable and meaningful in both worlds;
* the revision is a *content* hash: two runs with the same numbers share it, and
  evidence can state which calibration an observation was captured under.

Motor entries keep the upstream LeRobot ``MotorCalibration`` shape
(``id``/``drive_mode``/``homing_offset``/``range_min``/``range_max``) so the
existing adapter validation stays authoritative for servos.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "robot.calibration.v1"

#: The reference unit carries two RGB-D cameras, two arms and a gripper on each
#: arm. Both arms drive six STS3215 servos on their own bus, so the two arms
#: reuse servo ids 1..6 and are told apart by the bus (and by the device path).
CAMERA_NAMES = ("head-rgbd", "base-rgbd")

ARM_SIDES = ("left", "right")
ARM_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
#: ``gripper`` is the sixth joint of each arm, so a gripper is calibrated by
#: calibrating its arm's last servo.
GRIPPER_JOINT = "gripper"

ARM_MOTOR_IDS = {f"{side}_arm_{joint}": index
                 for side in ARM_SIDES
                 for index, joint in enumerate(ARM_JOINTS, 1)}
HEAD_AND_BASE_MOTOR_IDS = {
    "head_motor_1": 7, "head_motor_2": 8,
    "base_left_wheel": 9, "base_right_wheel": 10,
}
#: Every motor a complete calibration covers: 2 arms x 6 joints + 2 head + 2 wheels.
MOTOR_IDS = {**ARM_MOTOR_IDS, **HEAD_AND_BASE_MOTOR_IDS}

#: Which serial bus a motor answers on. The two arms are separate buses, which is
#: why identical servo ids on left and right are not a conflict.
MOTOR_BUS = {name: ("left" if name.startswith("left_") else
                    "right" if name.startswith("right_") else "shared")
             for name in MOTOR_IDS}


def motor_layout() -> list[dict[str, object]]:
    """Grouped description of the canonical motors, for editors and validation UI.

    The console renders one group per arm plus the head/base group, so a user
    editing a number always knows which physical arm and which bus it belongs to.
    """
    groups: list[dict[str, object]] = []
    for side in ARM_SIDES:
        groups.append({
            "group": f"{'左' if side == 'left' else '右'}臂",
            "bus": side,
            "motors": [
                {"name": f"{side}_arm_{joint}", "servoId": ARM_MOTOR_IDS[f"{side}_arm_{joint}"],
                 "joint": joint, "isGripper": joint == GRIPPER_JOINT}
                for joint in ARM_JOINTS
            ],
        })
    groups.append({
        "group": "头部与底盘",
        "bus": "shared",
        "motors": [
            {"name": name, "servoId": servo_id, "joint": name, "isGripper": False}
            for name, servo_id in HEAD_AND_BASE_MOTOR_IDS.items()
        ],
    })
    return groups


def gripper_motors() -> dict[str, str]:
    """``side -> motor name`` for the two grippers."""
    return {side: f"{side}_arm_{GRIPPER_JOINT}" for side in ARM_SIDES}

#: Fields that describe the unit and are covered by the revision hash. Anything
#: outside this set is metadata (who/when) and deliberately excluded, so that
#: re-saving identical numbers keeps the same identity.
CONTENT_FIELDS = ("schemaVersion", "robotId", "adapterId", "source",
                  "motors", "cameras", "geometry", "safety")

_MOTOR_FIELDS = {"id", "drive_mode", "homing_offset", "range_min", "range_max"}
_CAMERA_FIELDS = {"sourceId", "width", "height", "intrinsics", "distortion", "extrinsics"}
_INTRINSIC_FIELDS = {"fx", "fy", "cx", "cy"}
_DISTORTION_MODELS = ("plumb_bob", "none")


class CalibrationError(ValueError):
    """Raised with a machine-readable code and a human-readable reason."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code: str, message: str) -> None:
    raise CalibrationError(code, message)


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("INVALID_TYPE", f"{where} must be an object")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], where: str) -> None:
    missing = sorted(expected - set(value))
    if missing:
        _fail("MISSING_FIELD", f"{where} is missing {', '.join(missing)}")
    unknown = sorted(set(value) - expected)
    if unknown:
        _fail("UNKNOWN_FIELD", f"{where} has unknown field(s) {', '.join(unknown)}")


def _require_number(value: Any, where: str, *, minimum: float | None = None,
                    maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("INVALID_TYPE", f"{where} must be a number")
    number = float(value)
    if not math.isfinite(number):
        _fail("INVALID_VALUE", f"{where} must be finite")
    if minimum is not None and number < minimum:
        _fail("OUT_OF_RANGE", f"{where} must be >= {minimum:g}")
    if maximum is not None and number > maximum:
        _fail("OUT_OF_RANGE", f"{where} must be <= {maximum:g}")
    return number


def _require_int(value: Any, where: str, *, minimum: int | None = None,
                 maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("INVALID_TYPE", f"{where} must be an integer")
    if minimum is not None and value < minimum:
        _fail("OUT_OF_RANGE", f"{where} must be >= {minimum}")
    if maximum is not None and value > maximum:
        _fail("OUT_OF_RANGE", f"{where} must be <= {maximum}")
    return value


def _require_vector(value: Any, length: int, where: str) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != length:
        _fail("INVALID_TYPE", f"{where} must be a list of {length} numbers")
    return [_require_number(item, f"{where}[{index}]") for index, item in enumerate(value)]


def _normalize_motors(motors: Any) -> dict[str, dict[str, int]]:
    motors = _require_mapping(motors, "motors")
    if not motors:
        _fail("MISSING_FIELD", "motors must list at least one motor")
    unknown = sorted(set(motors) - set(MOTOR_IDS))
    if unknown:
        _fail("UNKNOWN_MOTOR", f"motors has unknown name(s) {', '.join(unknown)}; "
                               f"known motors are {len(MOTOR_IDS)} "
                               "(2 arms x 6 joints + 2 head + 2 wheels)")
    normalized: dict[str, dict[str, int]] = {}
    for name, entry in motors.items():
        where = f"motors.{name}"
        entry = _require_mapping(entry, where)
        _require_exact_keys(entry, _MOTOR_FIELDS, where)
        homing = _require_int(entry["homing_offset"], f"{where}.homing_offset", minimum=-2047, maximum=2047)
        low = _require_int(entry["range_min"], f"{where}.range_min", minimum=0, maximum=4094)
        high = _require_int(entry["range_max"], f"{where}.range_max", minimum=1, maximum=4095)
        if low >= high:
            _fail("OUT_OF_RANGE", f"{where}.range_min must be below range_max")
        normalized[name] = {
            "id": _require_int(entry["id"], f"{where}.id", minimum=1, maximum=253),
            "drive_mode": _require_int(entry["drive_mode"], f"{where}.drive_mode", minimum=0, maximum=1),
            "homing_offset": homing,
            "range_min": low,
            "range_max": high,
        }
    return normalized


def _normalize_cameras(cameras: Any) -> dict[str, dict[str, Any]]:
    cameras = _require_mapping(cameras, "cameras")
    if not cameras:
        _fail("MISSING_FIELD", "cameras must list at least one camera")
    unknown = sorted(set(cameras) - set(CAMERA_NAMES))
    if unknown:
        _fail("UNKNOWN_FIELD", f"cameras has unknown name(s) {', '.join(unknown)}")
    normalized: dict[str, dict[str, Any]] = {}
    for name, entry in cameras.items():
        where = f"cameras.{name}"
        entry = _require_mapping(entry, where)
        _require_exact_keys(entry, _CAMERA_FIELDS, where)
        width = _require_int(entry["width"], f"{where}.width", minimum=32, maximum=8192)
        height = _require_int(entry["height"], f"{where}.height", minimum=32, maximum=8192)

        intrinsics = _require_mapping(entry["intrinsics"], f"{where}.intrinsics")
        _require_exact_keys(intrinsics, _INTRINSIC_FIELDS, f"{where}.intrinsics")
        fx = _require_number(intrinsics["fx"], f"{where}.intrinsics.fx", minimum=1.0)
        fy = _require_number(intrinsics["fy"], f"{where}.intrinsics.fy", minimum=1.0)
        cx = _require_number(intrinsics["cx"], f"{where}.intrinsics.cx", minimum=0.0, maximum=width - 1)
        cy = _require_number(intrinsics["cy"], f"{where}.intrinsics.cy", minimum=0.0, maximum=height - 1)

        distortion = _require_mapping(entry["distortion"], f"{where}.distortion")
        _require_exact_keys(distortion, {"model", "coefficients"}, f"{where}.distortion")
        model = distortion["model"]
        if model not in _DISTORTION_MODELS:
            _fail("INVALID_VALUE", f"{where}.distortion.model must be one of {', '.join(_DISTORTION_MODELS)}")
        coefficients = distortion["coefficients"]
        if not isinstance(coefficients, Sequence) or isinstance(coefficients, (str, bytes)):
            _fail("INVALID_TYPE", f"{where}.distortion.coefficients must be a list")
        if model == "none" and len(coefficients) != 0:
            _fail("INVALID_VALUE", f"{where}.distortion.coefficients must be empty when model is none")
        if model == "plumb_bob" and len(coefficients) != 5:
            _fail("INVALID_VALUE", f"{where}.distortion.coefficients needs 5 values for plumb_bob")
        coefficients = [_require_number(item, f"{where}.distortion.coefficients[{index}]")
                        for index, item in enumerate(coefficients)]

        extrinsics = _require_mapping(entry["extrinsics"], f"{where}.extrinsics")
        _require_exact_keys(extrinsics, {"parentLink", "xyz", "rpy"}, f"{where}.extrinsics")
        parent = extrinsics["parentLink"]
        if not isinstance(parent, str) or not parent:
            _fail("INVALID_TYPE", f"{where}.extrinsics.parentLink must be a non-empty string")

        normalized[name] = {
            "sourceId": str(entry["sourceId"] or ""),
            "width": width,
            "height": height,
            "intrinsics": {"fx": fx, "fy": fy, "cx": cx, "cy": cy},
            "distortion": {"model": model, "coefficients": coefficients},
            "extrinsics": {
                "parentLink": parent,
                "xyz": _require_vector(extrinsics["xyz"], 3, f"{where}.extrinsics.xyz"),
                "rpy": _require_vector(extrinsics["rpy"], 3, f"{where}.extrinsics.rpy"),
            },
        }
    return normalized


def _normalize_safety(safety: Any) -> dict[str, float]:
    safety = _require_mapping(safety, "safety")
    expected = {"maxRelativeTargetDeg", "maxActionChunkLength", "maxLinearSpeedMPerS", "maxAngularSpeedRadPerS"}
    _require_exact_keys(safety, expected, "safety")
    return {
        "maxRelativeTargetDeg": _require_number(safety["maxRelativeTargetDeg"], "safety.maxRelativeTargetDeg", minimum=0.1, maximum=90.0),
        "maxActionChunkLength": _require_int(safety["maxActionChunkLength"], "safety.maxActionChunkLength", minimum=1, maximum=1024),
        "maxLinearSpeedMPerS": _require_number(safety["maxLinearSpeedMPerS"], "safety.maxLinearSpeedMPerS", minimum=0.001, maximum=2.0),
        "maxAngularSpeedRadPerS": _require_number(safety["maxAngularSpeedRadPerS"], "safety.maxAngularSpeedRadPerS", minimum=0.001, maximum=6.0),
    }


def _normalize_geometry(geometry: Any) -> dict[str, Any]:
    geometry = _require_mapping(geometry, "geometry")
    _require_exact_keys(geometry, {"gripper"}, "geometry")
    gripper = _require_mapping(geometry["gripper"], "geometry.gripper")
    _require_exact_keys(gripper, {"openM", "closedM"}, "geometry.gripper")
    return {
        "gripper": {
            "openM": _require_number(gripper["openM"], "geometry.gripper.openM", minimum=0.0, maximum=1.0),
            "closedM": _require_number(gripper["closedM"], "geometry.gripper.closedM", minimum=0.0, maximum=1.0),
        }
    }


def validate_calibration(document: Any) -> dict[str, Any]:
    """Return a normalized copy, or raise :class:`CalibrationError`.

    Strict on purpose: an unknown field is almost always a typo that would
    otherwise silently do nothing to a physical robot.
    """
    document = _require_mapping(document, "calibration")
    _require_exact_keys(document, set(CONTENT_FIELDS) | {"updatedAtUnixMs"}, "calibration")
    if document["schemaVersion"] != SCHEMA_VERSION:
        _fail("SCHEMA_MISMATCH", f"schemaVersion must be {SCHEMA_VERSION}")
    robot_id = document["robotId"]
    if not isinstance(robot_id, str) or not robot_id:
        _fail("INVALID_TYPE", "robotId must be a non-empty string")
    adapter_id = document["adapterId"]
    if not isinstance(adapter_id, str) or not adapter_id:
        _fail("INVALID_TYPE", "adapterId must be a non-empty string")
    source = document["source"]
    if source not in ("simulation", "measured", "default"):
        _fail("INVALID_VALUE", "source must be simulation, measured or default")

    normalized = {
        "schemaVersion": SCHEMA_VERSION,
        "robotId": robot_id,
        "adapterId": adapter_id,
        "source": source,
        "motors": _normalize_motors(document["motors"]) if document.get("motors") is not None else {},
        "cameras": _normalize_cameras(document["cameras"]) if document.get("cameras") is not None else {},
        "geometry": _normalize_geometry(document["geometry"]) if document.get("geometry") is not None else None,
        "safety": _normalize_safety(document["safety"]) if document.get("safety") is not None else None,
    }
    if not normalized["motors"] and not normalized["cameras"]:
        _fail("MISSING_FIELD", "calibration must carry motors, cameras or both")
    for field in ("geometry", "safety", "motors", "cameras"):
        if normalized[field] in (None, {}):
            _fail("MISSING_FIELD", f"{field} is required")
    updated = document.get("updatedAtUnixMs", 0)
    normalized["updatedAtUnixMs"] = _require_int(updated, "updatedAtUnixMs", minimum=0)
    return normalized


def canonical_content(document: Mapping[str, Any]) -> str:
    """Stable JSON of the measured fields only (no timestamps, no revision)."""
    content = {field: document.get(field) for field in CONTENT_FIELDS}
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calibration_revision(document: Mapping[str, Any]) -> str:
    """Content identity of a calibration.

    Evidence references this value, so it must change when a number changes and
    stay identical when the same numbers are saved twice.
    """
    return hashlib.sha256(canonical_content(document).encode("utf-8")).hexdigest()


def load_calibration(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """Read and validate a stored calibration; ``None`` when the file is absent."""
    file = Path(path)
    if not file.is_file():
        return None
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError("UNREADABLE", f"cannot read {file}: {exc}") from exc
    return validate_calibration(raw)


def save_calibration(path: str | os.PathLike[str], document: Any, *,
                     expected_revision: str | None = None) -> dict[str, Any]:
    """Validate, compare-and-swap on revision, then write atomically.

    A concurrent edit must not silently overwrite somebody else's measured
    numbers, so callers that read a revision are expected to pass it back.
    """
    normalized = validate_calibration(document)
    file = Path(path)
    current = load_calibration(file) if file.is_file() else None
    if expected_revision is not None:
        actual = calibration_revision(current) if current else ""
        if actual != expected_revision:
            raise CalibrationError(
                "REVISION_CONFLICT",
                f"calibration changed since it was read (expected {expected_revision[:12] or 'none'}, "
                f"found {actual[:12] or 'none'}); reload before saving",
            )
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    handle, temporary = tempfile.mkstemp(dir=str(file.parent), prefix=f".{file.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, file)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return normalized


@dataclass(frozen=True)
class CalibrationView:
    """What the console renders: the document plus its identity and editability."""

    document: dict[str, Any]
    revision: str
    path: str
    source: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "revision": self.revision,
            "source": self.source,
            "path": self.path,
            "calibration": self.document,
        }


def describe_calibration(document: Any) -> dict[str, Any]:
    """Validation result shaped for a UI, never raising."""
    try:
        normalized = validate_calibration(document)
    except CalibrationError as error:
        return {"valid": False, "code": error.code, "message": error.message}
    return {"valid": True, "code": "OK", "message": "calibration is valid",
            "revision": calibration_revision(normalized)}
