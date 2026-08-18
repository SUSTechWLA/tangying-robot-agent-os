import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_direct_robot_edge_unit_does_not_require_ros2():
    unit = (ROOT / "deploy/raspberry-pi/tangying-robot-edge-direct.service").read_text()
    assert "run_direct_edge" in unit
    assert "ros2 launch" not in unit.lower()
    assert "/opt/ros" not in unit.lower()
    assert "EnvironmentFile=/etc/tangying-robot-agent-os/robot-pi.env" in unit
    assert "SupplementaryGroups=dialout" in unit


def test_robot_edge_unit_starts_gateway_and_safety_supervisor_launch():
    unit = (ROOT / "deploy/raspberry-pi/tangying-robot-edge.service").read_text()
    assert "ros2 launch tangying_ros_gateway robot_edge.launch.py" in unit
    assert "EnvironmentFile=/etc/tangying-robot-agent-os/robot-pi.env" in unit
    assert "ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST" in unit
    assert "Requires=tangying-xlerobot.service" in unit
    assert "/opt/tangying-robot-agent-os/robot/ros2_ws/install/setup.bash" in unit


def test_xlerobot_unit_uses_local_ros_and_dialout_group():
    unit = (ROOT / "deploy/raspberry-pi/tangying-xlerobot.service").read_text()
    assert "ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST" in unit
    assert "SupplementaryGroups=dialout" in unit
    assert "xlerobot_adapter adapter" in unit
    assert "port1:=$XLEROBOT_PORT1" in unit
    assert "port2:=$XLEROBOT_PORT2" in unit
    assert "calibration_root:=$XLEROBOT_CALIBRATION" in unit


def test_xlerobot_defaults_keep_calibration_inside_robot_state_directory():
    config = (ROOT / "robot/ros2_ws/src/xlerobot_adapter/config/xlerobot.yaml").read_text()
    assert "/var/lib/tangying-robot-agent-os/calibration" in config
    assert "/var/lib/tangying-robot/calibration" not in config
    node = (ROOT / "robot/ros2_ws/src/xlerobot_adapter/xlerobot_adapter/node.py").read_text()
    assert "/var/lib/tangying-robot-agent-os/calibration" in node
    assert '"/dev/tangying-left"' in node
    assert '"/dev/tangying-right"' in node


def test_xlerobot_udev_rules_are_group_scoped_and_never_world_writable():
    rules = (ROOT / "deploy/raspberry-pi/99-tangying-xlerobot.rules").read_text()
    assert 'GROUP="dialout"' in rules
    assert 'MODE="0660"' in rules
    assert 'SYMLINK+="tangying-left"' in rules
    assert 'SYMLINK+="tangying-right"' in rules
    assert "0666" not in rules


def test_laptop_launch_agent_provisions_robot_mtls_files():
    plist = (ROOT / "deploy/laptop/com.tangying.robot-agent.plist").read_text()
    assert "--config" in plist
    assert "__HOME__/Library/Application Support/TangyingRobotAgent/local.env" in plist
    assert "robot-agent.example.internal" not in plist


def test_linux_laptop_service_reads_generated_local_config():
    unit = (ROOT / "deploy/laptop/tangying-robot-local-agent.service").read_text()
    assert "--config %h/.config/tangying-robot-agent-os/local.env" in unit
    assert "NoNewPrivileges=true" in unit


def test_ros_gateway_publishes_and_clears_command_lease_heartbeat():
    node = (
        ROOT / "robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/node.py"
    ).read_text()
    assert 'create_publisher(Int64, "command_lease_heartbeat"' in node
    assert "Int64(data=0)" in node


def test_robot_pi_preflight_is_no_motion_and_requires_real_calibration_file():
    script = (ROOT / "scripts/robot-pi-preflight.sh").read_text()
    assert "tangying-xlerobot.json" in script
    assert "ROBOT_SERVER_CERT" in script
    assert "ROBOT_CLIENT_CA" in script
    assert "send_action" not in script
    assert "enable_torque" not in script


def test_xlerobot_service_uses_bounded_configurable_motion_defaults():
    unit = (ROOT / "deploy/raspberry-pi/tangying-xlerobot.service").read_text()
    assert "${XLEROBOT_MAX_RELATIVE_TARGET:-8.0}" in unit
    assert "${XLEROBOT_MAX_ACTION_CHUNK_LENGTH:-64}" in unit
    assert "TimeoutStopSec=10" in unit
    config = (ROOT / "robot/ros2_ws/src/xlerobot_adapter/config/xlerobot.yaml").read_text()
    assert "max_relative_target: 8.0" in config
    assert "max_action_chunk_length: 64" in config
    env = (ROOT / "deploy/config/robot-pi.env.example").read_text()
    assert "XLEROBOT_MAX_RELATIVE_TARGET=8.0" in env
    assert "XLEROBOT_MAX_ACTION_CHUNK_LENGTH=64" in env


def test_no_motion_xlerobot_preflight_never_connects_robot():
    script = (ROOT / "scripts/xlerobot_preflight.py").read_text()
    assert "validate_calibration_file" in script
    assert "robot.connect" not in script
    assert "enable_torque" not in script


def test_quick_robot_pi_deploy_defaults_to_direct_edge():
    script = (ROOT / "scripts/robot-pi-quick-deploy.sh").read_text()
    assert 'ROBOT_AGENT_DIRECT_EDGE="${ROBOT_AGENT_DIRECT_EDGE:-1}"' in script
    assert "robot-pi --yes" in script
    assert "calibrate_xlerobot.py" in script
    makefile = (ROOT / "Makefile").read_text()
    assert "sim2real-check:" in makefile
    assert "deploy-robot-pi:" in makefile


def test_production_check_fails_closed_without_providers_and_hardware_evidence(tmp_path):
    script = ROOT / "scripts" / "xlerobot_production_check.py"
    config = tmp_path / "robot-pi.env"
    config.write_text(
        "XLEROBOT_PORT1=/dev/null\n"
        "XLEROBOT_PORT2=/dev/null\n"
        f"XLEROBOT_UPSTREAM_ROOT={tmp_path / 'XLeRobot'}\n"
        f"XLEROBOT_CALIBRATION_ROOT={tmp_path / 'calibration'}\n"
        f"ROBOT_EVIDENCE_DIR={tmp_path / 'evidence'}\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script), str(config), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode != 0
    report = json.loads(completed.stdout)
    assert report["ready"] is False
    blockers = "\n".join(report["blockers"])
    assert "no-motion preflight failed" in blockers
    assert "ROBOT_ENTITY_PROVIDER" in blockers
    assert "ROBOT_POLICY_PROVIDER" not in blockers
    assert "ROBOT_VERIFIER_PROVIDER" in blockers
    assert "completed_trials" in blockers
