import inspect

from tangying_robot_gateway import backend, safety, xlerobot_backend
from tangying_robot_gateway.runtime import Command


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
