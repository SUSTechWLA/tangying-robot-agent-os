"""Offline validation of the pinned two-wheel robot's LeRobot motor calibration."""
from __future__ import annotations

JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
MOTOR_IDS = {f"{side}_arm_{joint}": motor_id
             for side in ("left", "right") for motor_id, joint in enumerate(JOINTS, 1)} | {
    "head_motor_1": 7, "head_motor_2": 8, "base_left_wheel": 9, "base_right_wheel": 10,
}


def validate_calibration_data(data: object) -> None:
    """Reject partial, foreign or invalid STS3215 raw calibration before opening a port."""
    if not isinstance(data, dict) or set(data) != set(MOTOR_IDS):
        raise ValueError("calibration must contain exactly the 16 pinned two-wheel motors")
    fields = {"id", "drive_mode", "homing_offset", "range_min", "range_max"}
    for name, motor_id in MOTOR_IDS.items():
        entry = data[name]
        if not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError(f"{name}: invalid MotorCalibration fields")
        if any(type(value) is not int for value in entry.values()):
            raise ValueError(f"{name}: calibration values must be integers")
        if entry["id"] != motor_id or entry["drive_mode"] != 0:
            raise ValueError(f"{name}: motor ID or drive mode does not match pinned hardware")
        if not -2047 <= entry["homing_offset"] <= 2047:
            raise ValueError(f"{name}: homing offset outside STS3215 range")
        if not 0 <= entry["range_min"] < entry["range_max"] <= 4095:
            raise ValueError(f"{name}: invalid STS3215 position range")
        if name.startswith("base_") and (entry["homing_offset"] != 0
                                         or entry["range_min"] != 0 or entry["range_max"] != 4095):
            raise ValueError(f"{name}: wheel calibration must cover one complete revolution")
