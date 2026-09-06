import threading

import pytest
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.runtime import Command
from tangying_robot_gateway.safety import SafetySupervisor


class RecordingBackend:
    def __init__(self):
        self.stop_count = 0

    def stop(self, reason: str):
        self.stop_count += 1


class ControlledBackend(RecordingBackend):
    """A stopped actuator with controllable stop/reset completion for race tests."""

    def __init__(self):
        super().__init__()
        self.stopped = False
        self.stop_started = threading.Event()
        self.finish_stop = threading.Event()
        self.finish_stop.set()
        self.reset_started = threading.Event()
        self.finish_reset = threading.Event()
        self.finish_reset.set()

    def stop(self, reason):
        self.stop_started.set()
        if not self.finish_stop.wait(timeout=2):
            raise TimeoutError("test did not release stop")
        super().stop(reason)
        self.stopped = True

    def reset_stop(self, *, operator_present):
        self.reset_started.set()
        if not self.finish_reset.wait(timeout=2):
            raise TimeoutError("test did not release reset")
        self.stopped = False
        return True


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
    value = command(parameters={"action_chunk": [{"left_arm_shoulder_pan.pos": 200.0}]})
    decision = supervisor.evaluate(value)
    assert not decision.allowed
    assert decision.code == "ACTION_VALUE_OUT_OF_RANGE"


def test_safety_accepts_bounded_tabletop_action_chunk():
    supervisor = SafetySupervisor(clock_ms=lambda: 0)
    value = command()
    value = command(parameters={
        "action_chunk": [{"left_arm_shoulder_pan.pos": 10.0}, {"left_arm_gripper.pos": 80.0}]
    })
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


def test_cancel_stop_failure_remains_latched_after_restart(tmp_path):
    class BrokenBackend:
        def stop(self, reason):
            raise RuntimeError("serial bus disappeared")

    path = tmp_path / "runtime-journal.json"
    supervisor = SafetySupervisor(
        clock_ms=lambda: 0, backend=BrokenBackend(), journal=RuntimeJournal(path)
    )
    assert supervisor.start(command()).allowed
    assert supervisor.cancel("cmd-1", "operator cancel")

    restarted = SafetySupervisor(clock_ms=lambda: 0, journal=RuntimeJournal(path))
    assert not restarted.start(command(command_id="cmd-2")).allowed
    assert "CANCEL_STOP_FAILED" in restarted.last_stop_reason


@pytest.mark.parametrize("deadline,lease,expiry", [(500, 1000, 500), (30000, 1000, 1000)])
def test_watchdog_stops_at_earliest_deadline_or_lease_boundary(deadline, lease, expiry):
    now = [0]
    backend = RecordingBackend()
    supervisor = SafetySupervisor(clock_ms=lambda: now[0], backend=backend)
    assert supervisor.start(command(deadline_unix_ms=deadline, lease_ms=lease)).allowed
    now[0] = expiry - 1
    supervisor.tick()
    assert not supervisor.estop_latched

    now[0] = expiry
    supervisor.tick()
    assert supervisor.estop_latched
    assert backend.stop_count == 1


def test_rejected_backend_reset_keeps_persistent_latch(tmp_path):
    class RejectingBackend(RecordingBackend):
        def reset_stop(self, *, operator_present):
            return False

    path = tmp_path / "runtime-journal.json"
    supervisor = SafetySupervisor(
        clock_ms=lambda: 0, backend=RejectingBackend(), journal=RuntimeJournal(path)
    )
    supervisor.emergency_stop("operator")
    assert supervisor.clear_local(operator_present=True) is False
    assert not supervisor.start(command()).allowed
    assert RuntimeJournal(path).estop_latched


def test_emergency_stop_attempts_backend_stop_when_journal_write_fails(tmp_path, monkeypatch):
    journal = RuntimeJournal(tmp_path / "runtime-journal.json")

    def disk_full():
        raise OSError("disk full")

    monkeypatch.setattr(journal, "_persist", disk_full)
    backend = RecordingBackend()
    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=backend, journal=journal)
    supervisor.emergency_stop("operator")
    assert backend.stop_count == 1
    assert not supervisor.start(command()).allowed
    assert "disk full" in supervisor.last_stop_reason


def test_cancel_reserves_robot_until_old_execution_completes():
    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=RecordingBackend())
    assert supervisor.start(command()).allowed
    assert supervisor.cancel("cmd-1", "operator cancel")
    assert not supervisor.start(command(command_id="cmd-2")).allowed
    supervisor.complete("cmd-1")
    assert supervisor.start(command(command_id="cmd-2")).allowed


def test_duplicate_start_cannot_extend_an_active_lease():
    now = [0]
    supervisor = SafetySupervisor(clock_ms=lambda: now[0], backend=RecordingBackend())
    assert supervisor.start(command()).allowed
    now[0] = 900
    assert not supervisor.start(command()).allowed
    now[0] = 1000
    supervisor.tick()
    assert supervisor.estop_latched


def test_command_completion_does_not_allow_start_during_cancel_stop():
    backend = ControlledBackend()
    backend.finish_stop.clear()
    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=backend)
    assert supervisor.start(command()).allowed
    worker = threading.Thread(target=lambda: supervisor.cancel("cmd-1", "operator cancel"))
    worker.start()
    try:
        assert backend.stop_started.wait(timeout=1)
        supervisor.complete("cmd-1")
        assert not supervisor.start(command(command_id="cmd-2")).allowed
    finally:
        backend.finish_stop.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert supervisor.start(command(command_id="cmd-2")).allowed


def test_local_reset_cannot_run_during_emergency_stop():
    backend = ControlledBackend()
    backend.finish_stop.clear()
    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=backend)
    worker = threading.Thread(target=lambda: supervisor.emergency_stop("operator"))
    worker.start()
    try:
        assert backend.stop_started.wait(timeout=1)
        assert supervisor.clear_local(operator_present=True) is False
        assert not backend.reset_started.is_set()
        assert not supervisor.start(command()).allowed
    finally:
        backend.finish_stop.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert supervisor.estop_latched


def test_emergency_stop_during_reset_is_not_cleared_and_reapplies_backend_stop(tmp_path):
    backend = ControlledBackend()
    path = tmp_path / "runtime-journal.json"
    supervisor = SafetySupervisor(
        clock_ms=lambda: 0, backend=backend, journal=RuntimeJournal(path)
    )
    supervisor.emergency_stop("initial stop")
    backend.finish_reset.clear()
    resets = []
    worker = threading.Thread(
        target=lambda: resets.append(supervisor.clear_local(operator_present=True))
    )
    worker.start()
    try:
        assert backend.reset_started.wait(timeout=1)
        assert not supervisor.start(command()).allowed
        supervisor.emergency_stop("new operator stop")
    finally:
        backend.finish_reset.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert resets == [False]
    assert supervisor.estop_latched
    assert backend.stopped
    assert RuntimeJournal(path).estop_latched


def test_local_reset_cannot_release_a_running_command():
    backend = ControlledBackend()
    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=backend)
    assert supervisor.start(command()).allowed
    supervisor.emergency_stop("operator")
    assert supervisor.clear_local(operator_present=True) is False
    assert backend.stopped
    supervisor.complete("cmd-1")
    assert supervisor.clear_local(operator_present=True) is True
    assert supervisor.start(command(command_id="cmd-2")).allowed


def test_local_reset_journal_failure_restores_backend_stop(tmp_path, monkeypatch):
    backend = ControlledBackend()
    journal = RuntimeJournal(tmp_path / "runtime-journal.json")
    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=backend, journal=journal)
    supervisor.emergency_stop("operator")

    def disk_full():
        raise OSError("disk full")

    monkeypatch.setattr(journal, "_persist", disk_full)
    assert supervisor.clear_local(operator_present=True) is False
    assert supervisor.estop_latched
    assert journal.estop_latched
    assert backend.stopped
    assert "disk full" in supervisor.last_stop_reason


def test_command_expiring_during_validation_does_not_start():
    ticks = iter([0, 500])
    supervisor = SafetySupervisor(clock_ms=lambda: next(ticks))
    decision = supervisor.start(command(deadline_unix_ms=500))
    assert not decision.allowed
    assert decision.code == "COMMAND_EXPIRED"
    assert supervisor.active_command_id == ""


def test_cancel_without_an_active_command_does_not_stop_backend():
    backend = RecordingBackend()
    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=backend)
    assert supervisor.cancel("", "operator cancel") is False
    assert backend.stop_count == 0


def test_local_reset_resets_backend_after_completed_controlled_cancellation():
    backend = ControlledBackend()
    supervisor = SafetySupervisor(clock_ms=lambda: 0, backend=backend)
    assert supervisor.start(command()).allowed
    assert supervisor.cancel("cmd-1", "operator cancel")
    supervisor.complete("cmd-1")
    assert not supervisor.estop_latched
    assert backend.stopped
    assert supervisor.clear_local(operator_present=True) is True
    assert not backend.stopped
