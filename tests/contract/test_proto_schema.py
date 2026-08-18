def test_skill_command_carries_physical_safety_envelope():
    from tangying_robot_proto.robot.v1 import robot_pb2

    command = robot_pb2.SkillCommand(
        schema_version="robot.v1",
        command_id="cmd-1",
        task_id="task-1",
        skill="manipulation.pick",
        deadline_unix_ms=1_800_000_000_000,
        lease_ms=15_000,
        idempotency_key="task-1-pick-1",
        safety_profile="desktop_standard",
    )

    assert command.schema_version == "robot.v1"
    assert command.lease_ms == 15_000
    assert command.safety_profile == "desktop_standard"


def test_robot_runtime_protocol_is_thin_and_host_initiated():
    from tangying_robot_proto.robot.v1 import robot_pb2

    services = robot_pb2.DESCRIPTOR.services_by_name
    assert set(services) == {"RobotRuntime"}
    methods = {method.name for method in services["RobotRuntime"].methods}
    assert methods == {"GetRuntimeInfo", "Observe", "ExecuteSkill", "Cancel", "EmergencyStop"}
    info = robot_pb2.RuntimeInfo(protocol_version="1.0", runtime_version="0.2.0")
    assert info.protocol_version == "1.0"
