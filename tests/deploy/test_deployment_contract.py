from pathlib import Path

ROOT = Path(__file__).parents[2]


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
    node = (
        ROOT
        / "robot/ros2_ws/src/xlerobot_adapter/xlerobot_adapter/node.py"
    ).read_text()
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
        ROOT
        / "robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/node.py"
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
