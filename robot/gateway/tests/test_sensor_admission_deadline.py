"""Deterministic clock boundaries for read-only sensor admission waits."""

import copy

import pytest
from tangying_robot_gateway.backend import BackendResult
from tangying_robot_gateway.service import RobotRuntimeService, command_from_proto

from .test_plugin_backend import command_for, plugin


def admission_clock(monkeypatch, *, inspect=None, preempt_sample=False, deadline_ms=200):
    import tangying_robot_gateway.service as module

    dispatched = []
    backend = plugin(handlers={"arm.move": lambda command: dispatched.append(command) or BackendResult(True)},
                     physical_ready=lambda: True)
    service = RobotRuntimeService(backend)
    request = command_for(service)
    request.deadline_unix_ms = deadline_ms
    command = command_from_proto(request)
    clock = {"ms": 0, "monotonic_calls": 0, "inspections": 0, "sleeps": []}
    service.safety.clock_ms = lambda: clock["ms"]

    def monotonic():
        clock["monotonic_calls"] += 1
        captured = clock["ms"] / 1000.
        if preempt_sample and clock["monotonic_calls"] == 2:
            # Model a context switch after a monotonic read and before the next
            # clock read, not a longer sleep or a changed command deadline.
            clock["ms"] += 2
        return captured

    def sleep(seconds):
        assert 0 < seconds <= .05
        clock["sleeps"].append(seconds)
        clock["ms"] += round(seconds*1000)

    monkeypatch.setattr(module.time, "monotonic", monotonic)
    monkeypatch.setattr(module.time, "time", lambda: clock["ms"] / 1000.)
    monkeypatch.setattr(module.time, "sleep", sleep)
    original = backend.capabilities

    def capabilities():
        clock["inspections"] += 1
        info = copy.deepcopy(original())
        item = next(item for item in info.capabilities if item.name == "arm.move")
        item.available, item.blockers = False, ["IMU_NOT_READY"]
        if inspect is not None:
            inspect(clock, service)
        return info

    backend.capabilities = capabilities
    return service, command, clock, dispatched


@pytest.mark.parametrize("higher_priority", [None, "estop", "busy"])
def test_capability_read_crossing_deadline_rechecks_full_policy_without_wait(monkeypatch, higher_priority):
    def inspect(clock, service):
        if clock["ms"] == 150:
            clock["ms"] = 200
            if higher_priority == "estop":
                service.safety.estop_latched = True
            elif higher_priority == "busy":
                service.safety._active_command_id = "other-owned-command"

    service, command, clock, dispatched = admission_clock(monkeypatch, inspect=inspect)
    decision = service._admit_when_sensors_ready(command)
    expected = {None: "COMMAND_EXPIRED", "estop": "EMERGENCY_STOP_LATCHED", "busy": "ROBOT_BUSY"}
    assert not decision.allowed and decision.code == expected[higher_priority]
    assert clock["ms"] == 200 and clock["inspections"] == 4
    assert clock["sleeps"] == [.05, .05, .05]
    assert dispatched == []
    assert service.safety.active_command_id == ("other-owned-command" if higher_priority == "busy" else "")


def test_preempted_clock_sampling_does_not_shorten_200ms_deadline(monkeypatch):
    service, command, clock, dispatched = admission_clock(monkeypatch, preempt_sample=True)
    decision = service._admit_when_sensors_ready(command)
    assert not decision.allowed and decision.code == "COMMAND_EXPIRED"
    assert clock["ms"] == 200
    assert dispatched == [] and service.safety.active_command_id == ""


def test_preemption_inside_connection_check_does_not_add_a_poll_to_deadline(monkeypatch):
    service, command, clock, dispatched = admission_clock(monkeypatch)

    class Context:
        def is_active(self):
            clock["ms"] = 195
            return True

    decision = service._admit_when_sensors_ready(command, Context())
    assert not decision.allowed and decision.code == "COMMAND_EXPIRED"
    assert clock["ms"] == 200 and clock["sleeps"] == [.005]
    assert dispatched == [] and service.safety.active_command_id == ""


def test_five_second_sensor_budget_is_not_reported_as_a_future_command_deadline(monkeypatch):
    service, command, clock, dispatched = admission_clock(monkeypatch, deadline_ms=10_000)
    decision = service._admit_when_sensors_ready(command)
    assert not decision.allowed and decision.code == "CAPABILITY_UNAVAILABLE"
    assert clock["ms"] == 5_000
    assert dispatched == [] and service.safety.active_command_id == ""
