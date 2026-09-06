from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def gate_config(tmp_path: Path) -> Path:
    """Offline files satisfy preflight; upstream/provider code cannot move anything."""
    integration = tmp_path / "lerobot/robots/xlerobot_2wheels"
    integration.mkdir(parents=True)
    for directory in (integration, integration.parent, integration.parent.parent):
        (directory / "__init__.py").touch()
    fixtures = ROOT / "robot/ros2_ws/src/xlerobot_adapter/test/fixtures"
    for name in ("xlerobot_2wheels", "config_xlerobot_2wheels"):
        (integration / f"{name}.py").write_bytes((fixtures / f"{name}.py.txt").read_bytes())
    # The import boundary exposes the exact pinned source files for fingerprint
    # validation without executing upstream imports or constructing hardware.
    (integration / "__init__.py").write_text(
        "import sys, types\n"
        "from pathlib import Path\n"
        "for name in ('xlerobot_2wheels', 'config_xlerobot_2wheels'):\n"
        "    module = types.ModuleType(__name__ + '.' + name)\n"
        "    module.__file__ = str(Path(__file__).parent / (name + '.py'))\n"
        "    sys.modules[module.__name__] = module\n",
        encoding="utf-8",
    )
    metadata = tmp_path / "lerobot-0.4.1.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: lerobot\nVersion: 0.4.1\n", encoding="utf-8")
    (tmp_path / "test_providers.py").write_text(
        "not_callable = 42\n"
        "def scene_entities():\n"
        "    raise AssertionError('preflight must not invoke providers')\n"
        "def verify(*args):\n"
        "    raise AssertionError('preflight must not invoke providers')\n",
        encoding="utf-8",
    )
    for port in ("left-port", "right-port"):
        (tmp_path / port).touch()
    calibration = tmp_path / "calibration"
    calibration.mkdir()
    motors = {
        "left_arm_shoulder_pan": 1, "left_arm_shoulder_lift": 2, "left_arm_elbow_flex": 3,
        "left_arm_wrist_flex": 4, "left_arm_wrist_roll": 5, "left_arm_gripper": 6,
        "head_motor_1": 7, "head_motor_2": 8,
        "right_arm_shoulder_pan": 1, "right_arm_shoulder_lift": 2, "right_arm_elbow_flex": 3,
        "right_arm_wrist_flex": 4, "right_arm_wrist_roll": 5, "right_arm_gripper": 6,
        "base_left_wheel": 9, "base_right_wheel": 10,
    }
    (calibration / "tangying-xlerobot.json").write_text(json.dumps({
        name: {"id": motor_id, "drive_mode": 0, "homing_offset": 0,
               "range_min": 0, "range_max": 4095}
        for name, motor_id in motors.items()
    }), encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "hardware-trials.json").write_text(
        json.dumps(
            {
                "completed_trials": 30,
                "emergency_stop_tested": True,
                "network_interruption_tested": True,
                "duplicate_command_tested": True,
            }
        ),
        encoding="utf-8",
    )
    (evidence / "safety-checklist.json").write_text(
        json.dumps(
            {
                "physical_estop_installed": True,
                "physical_estop_tested": True,
                "operator_present_during_trials": True,
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "robot-pi.env"
    config.write_text(
        f"XLEROBOT_UPSTREAM_ROOT={tmp_path}\n"
        f"XLEROBOT_PORT1={tmp_path / 'left-port'}\n"
        f"XLEROBOT_PORT2={tmp_path / 'right-port'}\n"
        f"XLEROBOT_CALIBRATION_ROOT={calibration}\n"
        f"ROBOT_EVIDENCE_DIR={evidence}\n"
        "ROBOT_ENTITY_PROVIDER=test_providers:scene_entities\n"
        "ROBOT_VERIFIER_PROVIDER=test_providers:verify\n",
        encoding="utf-8",
    )
    return config


def run_gate(config: Path, *, json_output: bool = True) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(config.parent), environment.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/xlerobot_production_check.py"), str(config)]
        + (["--json"] if json_output else []),
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "module_name",
    ["scripts.xlerobot_production_check", "tangying_robot_gateway.run_direct_edge"],
)
def test_provider_loader_rejects_noncallable_imports(module_name: str):
    loader = importlib.import_module(module_name).load_callable

    with pytest.raises(TypeError, match="not callable"):
        loader("math:pi")


@pytest.mark.parametrize(
    "module_name",
    ["scripts.xlerobot_production_check", "tangying_robot_gateway.run_direct_edge"],
)
def test_provider_loader_preserves_optional_and_callable_providers(module_name: str):
    loader = importlib.import_module(module_name).load_callable

    assert loader(None) is None
    assert loader("") is None
    assert loader("builtins:len")([1, 2]) == 2


@pytest.mark.parametrize("provider", ["ENTITY", "VERIFIER"])
def test_gate_rejects_noncallable_provider(gate_config: Path, provider: str):
    with gate_config.open("a", encoding="utf-8") as stream:
        stream.write(f"ROBOT_{provider}_PROVIDER=test_providers:not_callable\n")

    result = run_gate(gate_config, json_output=False)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "not callable" in result.stdout
    assert f"provider failed to load for {provider.lower()}" in result.stdout


def test_successful_gate_emits_one_json_document(gate_config: Path):
    result = run_gate(gate_config)

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ready"] is True
    assert report["blockers"] == []


def test_provider_import_output_does_not_corrupt_json_report(gate_config: Path):
    with (gate_config.parent / "test_providers.py").open("a", encoding="utf-8") as stream:
        stream.write("print('provider module loaded')\n")

    result = run_gate(gate_config)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["ready"] is True
    assert "provider module loaded" in result.stderr


@pytest.mark.parametrize("invalid_config", ["missing", "invalid_utf8"])
def test_unreadable_config_reports_json_blocker(tmp_path: Path, invalid_config: str):
    config = tmp_path / "robot-pi.env"
    if invalid_config == "invalid_utf8":
        config.write_bytes(b"\xff\xfe")

    result = run_gate(config)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    report = json.loads(result.stdout)
    assert report["ready"] is False
    assert any("configuration is not readable" in blocker for blocker in report["blockers"])


@pytest.mark.parametrize("filename", ["hardware-trials.json", "safety-checklist.json"])
def test_invalid_evidence_encoding_reports_json_blocker(gate_config: Path, filename: str):
    (gate_config.parent / "evidence" / filename).write_bytes(b"\xff\xfe")

    result = run_gate(gate_config)

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    report = json.loads(result.stdout)
    assert report["ready"] is False
    assert any(filename in blocker for blocker in report["blockers"])


def test_failed_preflight_blocks_gate_with_valid_providers_and_evidence(gate_config: Path):
    (gate_config.parent / "left-port").unlink()

    result = run_gate(gate_config)

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["ready"] is False
    assert any("SERIAL_PORTS_UNAVAILABLE" in blocker for blocker in report["blockers"])


def test_gate_rejects_drifted_source_even_with_pinned_version_metadata(gate_config: Path):
    source = gate_config.parent / "lerobot/robots/xlerobot_2wheels/xlerobot_2wheels.py"
    source.write_text(source.read_text() + "\n# unsupported local patch\n")
    result = run_gate(gate_config)
    assert result.returncode == 1
    assert any("UPSTREAM_SOURCE_UNSUPPORTED" in item for item in json.loads(result.stdout)["blockers"])


def test_gate_rejects_partial_calibration_with_valid_source(gate_config: Path):
    calibration = gate_config.parent / "calibration/tangying-xlerobot.json"
    calibration.write_text('{"left_arm_shoulder_pan": {"id": 1}}')
    result = run_gate(gate_config)
    assert result.returncode == 1
    assert any("CALIBRATION_INVALID" in item for item in json.loads(result.stdout)["blockers"])


def test_calibration_script_disconnects_partial_connection_on_interrupt(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from xlerobot_adapter import upstream_compat

    from scripts import calibrate_xlerobot

    events = []

    class PartialRobot:
        is_connected = False

        def connect(self, calibrate):
            assert calibrate is False
            events.append("partial connect")
            raise KeyboardInterrupt

        def disconnect(self):
            events.append("disconnect")

    config = SimpleNamespace(XLerobot2WheelsConfig=lambda **values: SimpleNamespace(**values))
    monkeypatch.setitem(sys.modules, "lerobot.robots.xlerobot_2wheels.config_xlerobot_2wheels", config)
    monkeypatch.setattr(upstream_compat, "create_compatible_robot", lambda _: PartialRobot())
    monkeypatch.setattr(calibrate_xlerobot, "parse_args", lambda: SimpleNamespace(
        acknowledge_hardware_motion=True, port1="one", port2="two", max_relative_target=8.0,
        calibration_dir=tmp_path / "calibration"))
    monkeypatch.setattr(calibrate_xlerobot.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(Path, "is_char_device", lambda _: True)
    with pytest.raises(KeyboardInterrupt):
        calibrate_xlerobot.main()
    assert events == ["partial connect", "disconnect"]
