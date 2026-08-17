from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_robot_gateway_unit_starts_gateway_and_safety_supervisor_launch():
    unit = (ROOT / "deploy/raspberry-pi/tangying-robot-gateway.service").read_text()
    assert "ros2 launch tangying_ros_gateway robot_edge.launch.py" in unit


def test_laptop_launch_agent_provisions_robot_mtls_files():
    plist = (ROOT / "deploy/laptop/com.tangying.robot-agent.plist").read_text()
    assert "--robot-ca" in plist
    assert "--robot-cert" in plist
    assert "--robot-key" in plist
    assert "--robot-server-name" in plist


def test_ros_gateway_publishes_and_clears_command_lease_heartbeat():
    node = (
        ROOT
        / "robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/node.py"
    ).read_text()
    assert 'create_publisher(Int64, "command_lease_heartbeat"' in node
    assert "Int64(data=0)" in node
