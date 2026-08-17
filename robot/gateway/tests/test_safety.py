from tangying_robot_gateway.safety import SafetySupervisor
from tangying_robot_proto.robot.v1 import robot_pb2


class RecordingBackend:
    def __init__(self):
        self.stop_count = 0

    def stop(self, reason: str):
        self.stop_count += 1


def command(**overrides):
    values = {
        "schema_version": "robot.v1",
        "command_id": "cmd-1",
        "task_id": "task-1",
        "skill": "manipulation.pick",
        "deadline_unix_ms": 30_000,
        "lease_ms": 1_000,
        "idempotency_key": "task-1-pick-1",
        "safety_profile": "desktop_standard",
        "approval_id": "approval-1",
    }
    values.update(overrides)
    return robot_pb2.SkillCommand(**values)


def test_safety_rejects_expired_command():
    supervisor = SafetySupervisor(clock_ms=lambda: 20_000)
    decision = supervisor.evaluate(command(deadline_unix_ms=19_999))
    assert not decision.allowed
    assert decision.code == "COMMAND_EXPIRED"


def test_watchdog_stops_active_goal_after_lease_loss():
    now = [0]
    backend = RecordingBackend()
    supervisor = SafetySupervisor(clock_ms=lambda: now[0], backend=backend)
    assert supervisor.start(command(deadline_unix_ms=30_000)).allowed
    now[0] = 1_001
    supervisor.tick()
    assert backend.stop_count == 1
    assert supervisor.estop_latched


def test_remote_command_cannot_clear_emergency_stop():
    supervisor = SafetySupervisor(clock_ms=lambda: 0)
    supervisor.emergency_stop("operator")
    assert not supervisor.evaluate(command()).allowed
    assert supervisor.clear_local(operator_present=False) is False
    assert supervisor.clear_local(operator_present=True) is True
