import inspect
from dataclasses import fields
from pathlib import Path

from tangying_robot_gateway import backend, safety, xlerobot_backend
from tangying_robot_gateway.runtime import Command, Observation


def test_robot_runtime_command_is_transport_neutral():
    command = Command(
        schema_version="robot.v1",
        command_id="cmd-1",
        task_id="task-1",
        capability="manipulation.pick",
        target_ref="cup-1",
        parameters={"action_chunk": [{"left_arm_1.pos": 1.0}]},
        deadline_unix_ms=30_000,
        lease_ms=5_000,
        idempotency_key="task-1:pick",
        safety_profile="desktop_standard",
        approval_id="approval-1",
    )

    assert command.capability == "manipulation.pick"
    assert command.parameters["action_chunk"][0]["left_arm_1.pos"] == 1.0


def test_robot_backend_and_safety_layers_do_not_depend_on_protobuf():
    for module in (backend, safety, xlerobot_backend):
        source = inspect.getsource(module)
        assert "robot_pb2" not in source
        assert "google.protobuf" not in source


def test_agent_observation_has_no_raw_sensor_stream_fields():
    names = {item.name.lower() for item in fields(Observation)}
    for raw_stream in ("camera", "image", "lidar", "pointcloud", "imu", "joint_state"):
        assert raw_stream not in names


def test_ros_backend_uses_semantic_runtime_contract_not_protobuf():
    robot_root = Path(__file__).parents[2]
    source = (
        robot_root
        / "ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/node.py"
    ).read_text()
    assert "robot_pb2" not in source
    assert "tangying_robot_gateway.runtime" in source
