"""Metadata-only synthetic preflight tests; never connect to real hardware."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import xlerobot_preflight as preflight


def test_regular_files_cannot_masquerade_as_serial_devices(tmp_path):
    ports = (tmp_path / "left", tmp_path / "right")
    for port in ports:
        port.touch()
    blockers = preflight.serial_device_checks(tuple(map(str, ports)))
    assert len(blockers) == 2
    assert all("not a character device" in item for item in blockers)


def test_aliases_of_one_character_device_do_not_count_as_two_ports(tmp_path):
    ports = (tmp_path / "left", tmp_path / "right")
    for port in ports:
        port.symlink_to("/dev/null")
    assert any(
        "SERIAL_PORTS_DUPLICATED" in item
        for item in preflight.serial_device_checks(tuple(map(str, ports)))
    )


def test_calibration_configuration_matches_actual_direct_runtime_default(tmp_path):
    with pytest.raises(ValueError, match="disagree"):
        preflight.calibration_directory({"XLEROBOT_CALIBRATION": str(tmp_path)})
    with pytest.raises(ValueError, match="disagree"):
        preflight.calibration_directory(
            {"XLEROBOT_CALIBRATION": "/one", "XLEROBOT_CALIBRATION_ROOT": "/two"}
        )
    with pytest.raises(ValueError, match="absolute"):
        preflight.calibration_directory({"XLEROBOT_CALIBRATION_ROOT": "relative"})
    assert preflight.calibration_directory({"XLEROBOT_CALIBRATION_ROOT": str(tmp_path)}) == tmp_path


@pytest.mark.parametrize(
    "path", ["", "relative.json", "/tmp/robot.json", "/run/robot.json", "/dev/shm/robot.json"]
)
def test_journal_requires_persistent_absolute_storage(path):
    state, blockers = preflight.journal_status({"ROBOT_RUNTIME_JOURNAL": path})
    assert blockers and not state["history_verified"]


def test_missing_journal_is_uninitialized_not_cleared_history(tmp_path, monkeypatch):
    # Isolated fixtures use temporary storage. No CLI override exposes this seam.
    monkeypatch.setattr(preflight, "EPHEMERAL_JOURNAL_ROOTS", ())
    path = tmp_path / "runtime.json"
    state, blockers = preflight.journal_status({"ROBOT_RUNTIME_JOURNAL": str(path)})
    assert blockers == []
    assert state == {"state": "uninitialized", "history_verified": False}
    assert not path.exists()


@pytest.mark.parametrize(
    "payload,blocked",
    [
        ({"version": 2, "estop_latched": False, "commands": {}}, False),
        ({"version": 2, "estop_latched": True, "commands": {}}, True),
        ({"version": 2, "estop_latched": False, "commands": {"task/x": {"pending": True}}}, True),
        ({"version": 999, "commands": {}}, True),
        ("corrupt-json", True),
    ],
)
def test_journal_inspection_preserves_latches_unresolved_history_and_file_bytes(
    tmp_path, monkeypatch, payload, blocked
):
    monkeypatch.setattr(preflight, "EPHEMERAL_JOURNAL_ROOTS", ())
    path = tmp_path / "runtime.json"
    original = json.dumps(payload).encode() if isinstance(payload, dict) else payload.encode()
    path.write_bytes(original)
    state, blockers = preflight.journal_status({"ROBOT_RUNTIME_JOURNAL": str(path)})
    assert bool(blockers) is blocked
    assert state["history_verified"] is (not blocked)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_shell_and_ros_templates_use_the_same_calibration_root():
    root = Path(__file__).resolve().parents[2]
    shell = (root / "scripts/robot-pi-preflight.sh").read_text()
    unit = (root / "deploy/robot/raspberry-pi/tangying-xlerobot.service").read_text()
    assert "calibration=$(read_config XLEROBOT_CALIBRATION_ROOT)" in shell
    assert "calibration_root:=${XLEROBOT_CALIBRATION_ROOT:-$XLEROBOT_CALIBRATION}" in unit
