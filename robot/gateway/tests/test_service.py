import threading
import time

import pytest
from tangying_robot_gateway.backend import BackendResult, RobotBackend
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.runtime import Observation
from tangying_robot_gateway.service import RobotRuntimeService, start_server
from tangying_robot_proto.robot.v1 import robot_pb2


class RecordingBackend(RobotBackend):
    def __init__(self):
        self.executed = []
        self.stopped = []

    def execute(self, command):
        self.executed.append(command.capability)
        return BackendResult(success=True, confidence=0.98)

    def stop(self, reason: str):
        self.stopped.append(reason)


class BlockingBackend(RecordingBackend):
    def __init__(self):
        super().__init__()
        self.released = threading.Event()

    def execute(self, command):
        self.executed.append(command.capability)
        self.released.wait(timeout=2)
        return BackendResult(success=False, code="STOPPED")

    def stop(self, reason: str):
        super().stop(reason)
        self.released.set()


class ObservingBackend(RecordingBackend):
    def observe(self, request):
        return Observation(observation_id="obs-1", wall_time_unix_ms=0, monotonic_time_ns=0)


def valid_command():
    return robot_pb2.SkillCommand(
        schema_version="robot.v1",
        command_id="cmd-1",
        task_id="task-1",
        skill="manipulation.pick",
        target_ref="red-cup",
        deadline_unix_ms=int(time.time() * 1000) + 10_000,
        lease_ms=5_000,
        idempotency_key="task-1-pick-1",
        safety_profile="desktop_standard",
        approval_id="approval-1",
    )


def test_gateway_executes_only_after_safety_approval():
    backend = RecordingBackend()
    service = RobotRuntimeService(backend)
    events = list(service.execute_for_test(valid_command()))
    assert backend.executed == ["manipulation.pick"]
    assert events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED


def test_gateway_rejects_unknown_skill_without_backend_call():
    backend = RecordingBackend()
    service = RobotRuntimeService(backend)
    command = valid_command()
    command.skill = "shell.execute"
    events = list(service.execute_for_test(command))
    assert backend.executed == []
    assert events[-1].code == "SKILL_NOT_ALLOWED"


def test_gateway_watchdog_stops_blocking_command_after_lease_expiry():
    backend = BlockingBackend()
    service = RobotRuntimeService(backend)
    command = valid_command()
    command.lease_ms = 50
    worker = threading.Thread(target=lambda: list(service.execute_for_test(command)))
    worker.start()
    worker.join(timeout=0.5)
    if worker.is_alive():
        backend.released.set()
        worker.join(timeout=1)
    assert backend.stopped == ["COMMAND_LEASE_EXPIRED"]
    assert service.safety.estop_latched


def test_server_refuses_plaintext_without_explicit_development_flag():
    with pytest.raises(ValueError, match="mTLS credentials"):
        start_server(RecordingBackend(), "127.0.0.1:0")


def test_observe_annotates_backend_result_with_semantic_runtime_state():
    service = RobotRuntimeService(ObservingBackend())
    observation = next(service.Observe(robot_pb2.ObserveRequest(), None))
    assert observation.semantic_state.activity == "IDLE"
    assert not observation.semantic_state.emergency_stopped


def test_cancel_active_command_emits_cancelled_terminal_event():
    backend = BlockingBackend()
    service = RobotRuntimeService(backend)
    command = valid_command()
    events = []
    worker = threading.Thread(target=lambda: events.extend(service.execute_for_test(command)))
    worker.start()
    deadline = time.monotonic() + 1
    while not backend.executed and time.monotonic() < deadline:
        time.sleep(0.005)
    result = service.Cancel(
        robot_pb2.CancelRequest(command_id="cmd-1", reason="operator cancel"), None
    )
    worker.join(timeout=1)
    assert result.accepted
    assert events[-1].type == robot_pb2.SKILL_EVENT_CANCELLED
    assert events[-1].code == "CANCELLED"
    assert not service.safety.estop_latched


def test_completed_command_replays_after_runtime_restart(tmp_path):
    path = tmp_path / "runtime-journal.json"
    first_backend = RecordingBackend()
    first = RobotRuntimeService(first_backend, journal=RuntimeJournal(path))
    first_events = list(first.execute_for_test(valid_command()))

    second_backend = RecordingBackend()
    restarted = RobotRuntimeService(second_backend, journal=RuntimeJournal(path))
    replayed = list(restarted.execute_for_test(valid_command()))

    assert first_events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert replayed[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert second_backend.executed == []


def test_estop_remains_latched_after_runtime_restart(tmp_path):
    path = tmp_path / "runtime-journal.json"
    first = RobotRuntimeService(RecordingBackend(), journal=RuntimeJournal(path))
    first.EmergencyStop(robot_pb2.EStopRequest(reason="operator"), None)

    restarted = RobotRuntimeService(RecordingBackend(), journal=RuntimeJournal(path))
    info = restarted.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)

    assert info.manipulation_ready is False
    assert "EMERGENCY_STOP_LATCHED" in info.blockers


@pytest.mark.parametrize("retry_kind, expected_code", [
    ("duplicate", "EXECUTION_OUTCOME_UNKNOWN"),
    ("conflict", "IDEMPOTENCY_CONFLICT"),
    ("new_key", "ROBOT_BUSY"),
])
def test_inflight_commands_never_execute_twice(retry_kind, expected_code):
    class PausedBackend(RecordingBackend):
        entered = threading.Event()
        release = threading.Event()

        def execute(self, command):
            self.executed.append(command.capability)
            if len(self.executed) == 1:
                self.entered.set()
                assert self.release.wait(3)
            return BackendResult(success=True)

    backend = PausedBackend()
    service = RobotRuntimeService(backend)
    original = valid_command()
    completed = []
    worker = threading.Thread(target=lambda: completed.extend(service.execute_for_test(original)))
    worker.start()
    try:
        assert backend.entered.wait(1)
        retry = valid_command()
        if retry_kind == "conflict":
            retry.target_ref = "different-object"
        elif retry_kind == "new_key":
            retry.idempotency_key = "new-key-same-command-id"
        rejected = list(service.execute_for_test(retry))
        assert rejected[-1].code == expected_code
        assert backend.executed == ["manipulation.pick"]
    finally:
        backend.release.set()
        worker.join(2)
    assert not worker.is_alive()
    assert completed[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert list(service.execute_for_test(original))[-1] == completed[-1]


def test_interrupted_execution_is_not_repeated_after_restart(tmp_path):
    class ProcessLost(BaseException):
        pass

    class InterruptedBackend(RecordingBackend):
        def execute(self, command):
            self.executed.append(command.capability)
            raise ProcessLost()

    path = tmp_path / "runtime-journal.json"
    first = RobotRuntimeService(InterruptedBackend(), journal=RuntimeJournal(path))
    with pytest.raises(ProcessLost):
        list(first.execute_for_test(valid_command()))

    backend = RecordingBackend()
    restarted = RobotRuntimeService(backend, journal=RuntimeJournal(path))
    events = list(restarted.execute_for_test(valid_command()))
    assert backend.executed == []
    assert events[-1].code == "EXECUTION_OUTCOME_UNKNOWN"
    assert restarted.safety.estop_latched


def test_journal_write_failure_prevents_backend_execution(tmp_path, monkeypatch):
    backend = RecordingBackend()
    journal = RuntimeJournal(tmp_path / "runtime-journal.json")

    def disk_full():
        raise OSError("disk full")

    monkeypatch.setattr(journal, "_persist", disk_full)
    service = RobotRuntimeService(backend, journal=journal)
    events = list(service.execute_for_test(valid_command()))
    assert backend.executed == []
    assert events[-1].code == "RUNTIME_JOURNAL_UNAVAILABLE"
    assert service.safety.estop_latched


def test_cancel_is_visible_before_backend_stop_returns():
    finished = threading.Event()

    class StopWaitsForExecution(BlockingBackend):
        def stop(self, reason):
            super().stop(reason)
            assert finished.wait(1)

    backend = StopWaitsForExecution()
    service = RobotRuntimeService(backend)
    events = []

    def execute():
        events.extend(service.execute_for_test(valid_command()))
        finished.set()

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        deadline = time.monotonic() + 1
        while not backend.executed and time.monotonic() < deadline:
            time.sleep(0.005)
        assert backend.executed
        result = service.Cancel(robot_pb2.CancelRequest(command_id="cmd-1", reason="cancel"), None)
        assert result.accepted
        assert events[-1].type == robot_pb2.SKILL_EVENT_CANCELLED
    finally:
        backend.released.set()
        worker.join(2)


def test_nonfinite_wire_parameters_are_rejected_without_backend_execution():
    backend = RecordingBackend()
    service = RobotRuntimeService(backend)
    command = valid_command()
    command.parameters.fields["bad"].number_value = float("nan")
    events = list(service.execute_for_test(command))
    assert events[-1].code == "COMMAND_PARAMETERS_INVALID"
    assert backend.executed == []


def test_emergency_stop_tool_uses_persistent_safety_latch(tmp_path):
    path = tmp_path / "runtime-journal.json"
    backend = RecordingBackend()
    service = RobotRuntimeService(backend, journal=RuntimeJournal(path))
    command = valid_command()
    command.skill = "emergency_stop"
    list(service.execute_for_test(command))
    assert backend.stopped
    assert RuntimeJournal(path).estop_latched


def test_journal_begin_failure_releases_command_for_local_reset(tmp_path, monkeypatch):
    backend = RecordingBackend()
    journal = RuntimeJournal(tmp_path / "runtime-journal.json")
    service = RobotRuntimeService(backend, journal=journal)

    def disk_full():
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(journal, "_persist", disk_full)
        events = list(service.execute_for_test(valid_command()))

    assert events[-1].code == "RUNTIME_JOURNAL_UNAVAILABLE"
    assert backend.executed == []
    assert service.safety.active_command_id == ""
    assert service.safety.clear_local(operator_present=True) is True


@pytest.mark.parametrize("deadline, lease", [(50, 1000), (1000, 50)])
def test_journal_delay_cannot_start_expired_motion(tmp_path, monkeypatch, deadline, lease):
    backend = RecordingBackend()
    journal = RuntimeJournal(tmp_path / "runtime-journal.json")
    service = RobotRuntimeService(backend, journal=journal)
    now = [0]
    monkeypatch.setattr(service.safety, "clock_ms", lambda: now[0])
    persist = journal._persist

    def slow_persist():
        now[0] = 100
        persist()

    monkeypatch.setattr(journal, "_persist", slow_persist)
    command = valid_command()
    command.deadline_unix_ms = deadline
    command.lease_ms = lease
    events = list(service.execute_for_test(command))

    assert backend.executed == []
    assert events[-1].type == robot_pb2.SKILL_EVENT_SAFETY_STOPPED
    assert service.safety.estop_latched


def test_cancel_accepted_during_journal_begin_prevents_backend_dispatch(tmp_path, monkeypatch):
    backend = RecordingBackend()
    journal = RuntimeJournal(tmp_path / "runtime-journal.json")
    service = RobotRuntimeService(backend, journal=journal)
    persist = journal._persist
    cancellations = []

    def cancel_before_persist():
        if not cancellations:
            cancellations.append(service.Cancel(
                robot_pb2.CancelRequest(command_id="cmd-1", reason="cancel before dispatch"), None
            ))
        persist()

    monkeypatch.setattr(journal, "_persist", cancel_before_persist)
    events = list(service.execute_for_test(valid_command()))
    assert cancellations[0].accepted
    assert backend.executed == []
    assert events[-1].type == robot_pb2.SKILL_EVENT_CANCELLED


def test_emergency_stop_tool_preempts_an_executing_command(tmp_path):
    class ActiveBackend(BlockingBackend):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()

        def execute(self, command):
            self.executed.append(command.capability)
            self.entered.set()
            assert self.released.wait(timeout=2)
            return BackendResult(success=False, code="STOPPED")

    path = tmp_path / "runtime-journal.json"
    backend = ActiveBackend()
    service = RobotRuntimeService(backend, journal=RuntimeJournal(path))
    motion_events = []
    worker = threading.Thread(
        target=lambda: motion_events.extend(service.execute_for_test(valid_command()))
    )
    worker.start()
    try:
        assert backend.entered.wait(timeout=1)
        stop = valid_command()
        stop.command_id = "cmd-stop"
        stop.idempotency_key = "stop-key"
        stop.skill = "emergency_stop"
        stop_events = list(service.execute_for_test(stop))
        assert backend.stopped
        assert stop_events[-1].type == robot_pb2.SKILL_EVENT_SAFETY_STOPPED
        assert RuntimeJournal(path).estop_latched
    finally:
        backend.released.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert motion_events[-1].type == robot_pb2.SKILL_EVENT_SAFETY_STOPPED


def test_cancel_cannot_be_accepted_after_success_selection_begins():
    cancellation_started = threading.Event()
    cancellation_finished = threading.Event()
    cancellations = []

    def cancel():
        cancellation_started.set()
        try:
            cancellations.append(service.Cancel(
                robot_pb2.CancelRequest(command_id="cmd-1", reason="cancel at completion"), None
            ))
        finally:
            cancellation_finished.set()

    worker = threading.Thread(target=cancel)

    backend = RecordingBackend()
    service = RobotRuntimeService(backend)
    complete = service.safety.complete

    def complete_at_barrier(command_id):
        # The real completion path must hold the admission lock while it
        # clears the active command. This barrier does not modify the Result.
        if service.safety.active_command_id == command_id and worker.ident is None:
            worker.start()
            assert cancellation_started.wait(timeout=1)
            cancellation_finished.wait(timeout=0.2)
        complete(command_id)

    service.safety.complete = complete_at_barrier
    try:
        events = list(service.execute_for_test(valid_command()))
    finally:
        if worker.ident is not None:
            worker.join(timeout=2)

    assert not worker.is_alive()
    assert events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert len(cancellations) == 1
    assert not cancellations[0].accepted
    assert backend.stopped == []
