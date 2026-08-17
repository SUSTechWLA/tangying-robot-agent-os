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
