from __future__ import annotations

import time

import pytest
from tangying_robot_gateway.backend import RobotBackend
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.runtime import Result
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_proto.robot.v1 import robot_pb2

from .test_plugin_backend import command_for, plugin


@pytest.mark.parametrize("outcome", [
    Result(True, confidence=float("nan")), Result("yes"), Result(True, code=None), None,
    RuntimeError("driver lost response after sending motion"),
])
def test_custom_physical_adapter_unknown_result_stops_and_persists_latch(tmp_path, outcome):
    movements, stops = [], []
    delegate = plugin(physical_ready=lambda: True)

    class Adapter(RobotBackend):
        def capabilities(self):
            return delegate.capabilities()

        def observe(self, request):
            return delegate.observe(request)

        def execute(self, command):
            movements.append(command.command_id)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def stop(self, reason):
            stops.append(reason)

    backend = Adapter()
    path = tmp_path / "runtime-journal.json"
    service = RobotRuntimeService(backend, RuntimeJournal(path))
    command = command_for(service)
    result = list(service.execute_for_test(command))[-1]
    assert result.type == robot_pb2.SKILL_EVENT_SAFETY_STOPPED
    assert result.code == "EXECUTION_OUTCOME_UNKNOWN"
    assert len(stops) == 1
    assert RuntimeJournal(path).estop_latched
    restarted = RobotRuntimeService(backend, RuntimeJournal(path))
    command.command_id, command.idempotency_key = "attempt-2", "attempt-2"
    assert list(restarted.execute_for_test(command))[-1].type != robot_pb2.SKILL_EVENT_SUCCEEDED
    assert len(movements) == 1


def test_sdk_does_not_hide_an_invalid_post_motion_result_as_an_ordinary_failure(tmp_path):
    backend = plugin(handlers={"arm.move": lambda command: Result(True, confidence=float("inf"))},
                     physical_ready=lambda: True)
    service = RobotRuntimeService(backend, RuntimeJournal(tmp_path / "journal.json"))
    result = list(service.execute_for_test(command_for(service)))[-1]
    assert result.type == robot_pb2.SKILL_EVENT_SAFETY_STOPPED
    assert service.safety.estop_latched


def test_known_physical_failure_does_not_become_an_unknown_outcome():
    outcomes = [Result(False, "TARGET_UNREACHABLE", confidence=0.0), Result(True)]
    backend = plugin(handlers={"arm.move": lambda command: outcomes.pop(0)}, physical_ready=lambda: True)
    service = RobotRuntimeService(backend)
    command = command_for(service)
    assert list(service.execute_for_test(command))[-1].code == "TARGET_UNREACHABLE"
    assert not service.safety.estop_latched
    command.command_id, command.idempotency_key = "known-failure-retry", "known-failure-retry"
    assert list(service.execute_for_test(command))[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED


def test_second_sensor_sample_cannot_start_a_handler_after_lease_stop():
    from tangying_robot_gateway.plugin_backend import PluginBackend

    from .test_plugin_contracts import arm_profile, scene

    payload = scene()
    samples, movements, stopped = [], [], []

    def provider():
        samples.append(True)
        if len(samples) == 2:
            time.sleep(0.1)
        return payload

    backend = PluginBackend(
        arm_profile(), observation_provider=provider,
        handlers={"arm.move": lambda command: movements.append(command) or Result(True)},
        stop=lambda reason: stopped.append(reason), physical_ready=lambda: not stopped,
    )
    service = RobotRuntimeService(backend)
    command = command_for(service)
    command.lease_ms = 40
    result = list(service.execute_for_test(command))[-1]
    assert result.type == robot_pb2.SKILL_EVENT_SAFETY_STOPPED
    assert stopped == ["COMMAND_LEASE_EXPIRED"]
    assert movements == []


def test_result_is_read_once_and_copied_to_a_stable_domain_value():
    reads = {}

    class ChangingResult(Result):
        def __getattribute__(self, name):
            if name in {"success", "confidence"}:
                reads[name] = reads.get(name, 0) + 1
                if reads[name] > 1:
                    return "changed" if name == "success" else float("nan")
            return super().__getattribute__(name)

    backend = plugin(handlers={"arm.move": lambda command: ChangingResult(True, confidence=0.8)},
                     physical_ready=lambda: True)
    service = RobotRuntimeService(backend)
    result = list(service.execute_for_test(command_for(service)))[-1]
    assert result.type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert result.verification_confidence == 0.8
