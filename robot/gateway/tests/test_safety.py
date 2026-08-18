from tangying_robot_gateway.runtime import Command
from tangying_robot_gateway.safety import SafetySupervisor


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
        "capability": "manipulation.pick",
        "deadline_unix_ms": 30_000,
        "lease_ms": 1_000,
        "idempotency_key": "task-1-pick-1",
        "safety_profile": "desktop_standard",
        "approval_id": "approval-1",
    }
    values.update(overrides)
    return Command(**values)


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


def test_safety_rejects_mobile_base_key_inside_action_chunk():
    supervisor = SafetySupervisor(clock_ms=lambda: 0)
    value = command()
    value = command(parameters={"action_chunk": [{"x.vel": 0.1}]})
    decision = supervisor.evaluate(value)
    assert not decision.allowed
    assert decision.code == "MOBILE_BASE_DISABLED"


def test_safety_rejects_unknown_action_key():
    supervisor = SafetySupervisor(clock_ms=lambda: 0)
    value = command()
    value = command(parameters={"action_chunk": [{"shell.command": 1.0}]})
    decision = supervisor.evaluate(value)
    assert not decision.allowed
    assert decision.code == "ACTION_KEY_REJECTED"


def test_safety_rejects_out_of_range_action_value():
    supervisor = SafetySupervisor(clock_ms=lambda: 0)
    value = command()
    value = command(parameters={"action_chunk": [{"left_arm_1.pos": 200.0}]})
    decision = supervisor.evaluate(value)
    assert not decision.allowed
    assert decision.code == "ACTION_VALUE_OUT_OF_RANGE"


def test_safety_accepts_bounded_tabletop_action_chunk():
    supervisor = SafetySupervisor(clock_ms=lambda: 0)
    value = command()
    value = command(parameters={"action_chunk": [{"left_arm_1.pos": 10.0}, {"left_arm_gripper.pos": 80.0}]})
    decision = supervisor.evaluate(value)
    assert decision.allowed


def test_safety_rejects_lease_longer_than_runtime_limit():
    supervisor = SafetySupervisor(clock_ms=lambda: 0)
    decision = supervisor.evaluate(command(lease_ms=120_000))
    assert not decision.allowed
    assert decision.code == "LEASE_TOO_LONG"


def test_cancel_stops_active_command_without_latching_estop():
    backend = RecordingBackend()
    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=backend)
    assert supervisor.start(command()).allowed
    assert supervisor.cancel("cmd-1", "operator cancel")
    assert backend.stop_count == 1
    assert not supervisor.estop_latched


def test_emergency_stop_latches_even_when_backend_stop_raises():
    class BrokenBackend:
        def stop(self, reason):
            raise RuntimeError("serial bus disappeared")

    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=BrokenBackend())
    supervisor.emergency_stop("operator")
    assert supervisor.estop_latched
    assert "BACKEND_STOP_FAILED" in supervisor.last_stop_reason
    assert not supervisor.evaluate(command()).allowed
