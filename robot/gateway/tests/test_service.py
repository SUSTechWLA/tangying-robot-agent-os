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
