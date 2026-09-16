"""Automatic recovery: bounded remedies, judged by the fault disappearing.

The design claim is that a robot may repair itself without being trusted about it.
These cases hold that claim: a remedy that reports success while the module is still
broken is recorded as a failure, a fault that needs a person is never retried, every
attempt leaves a record, and a remedy that moves the robot will not run unless the
robot is safe to move.
"""

from __future__ import annotations

import pytest
from tangying_robot_gateway.fault_remedy import (
    FaultRemedyEngine,
    Remedy,
    RemedyError,
    default_remedy_order,
)
from tangying_robot_gateway.module_faults import Fault, FaultLedger


def self_recoverable(code: str = "ARM_NOT_FOUND", module: str = "arm-left", **overrides) -> Fault:
    values = {"module_id": module, "code": code, "kind": "arm", "severity": "blocked",
              "remedy": "self_recover", "detail": "no response on the arm bus",
              "user_instruction": "左臂无响应：检查电源与总线，然后点『重新自检』。",
              "detected_at_unix_ms": 1_000_000}
    values.update(overrides)
    return Fault(**values)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.25
        return self.now


def engine(ledger: FaultLedger, *remedies: Remedy, **kwargs) -> FaultRemedyEngine:
    return FaultRemedyEngine(ledger, remedies, clock=Clock(), **kwargs)


def test_a_remedy_is_credited_only_when_the_fault_really_disappears():
    ledger = FaultLedger()
    fault = ledger.record(self_recoverable())
    # The action claims success loudly and does nothing at all.
    shouted: list[str] = []

    def pretend() -> str:
        shouted.append("done")
        return "SUCCESS"

    outcome = engine(ledger, Remedy("rehome", frozenset({"ARM_NOT_FOUND"}), pretend)).resolve(fault.key)
    assert shouted == ["done"], "the action was attempted"
    assert outcome.resolved is False and outcome.escalated is True
    assert outcome.attempts[0]["faultCleared"] is False
    assert "以故障消失为准" in outcome.reason
    # ...and the operator is told what to do, from the fault's own instruction.
    assert outcome.instruction == "左臂无响应：检查电源与总线，然后点『重新自检』。"


def test_a_remedy_that_clears_the_fault_is_recorded_as_the_fix():
    ledger = FaultLedger()
    fault = ledger.record(self_recoverable())

    def rehome() -> None:
        ledger.clear("arm-left")

    outcome = engine(ledger, Remedy("rehome", frozenset({"ARM_NOT_FOUND"}), rehome,
                                    description="机械臂回零")).resolve(fault.key)
    assert outcome.resolved is True and outcome.escalated is False
    assert outcome.attempts == [{"remedyId": "rehome", "description": "机械臂回零",
                                 "durationMs": 250.0, "error": "", "faultCleared": True}]


def test_a_fault_that_needs_a_person_is_never_retried():
    ledger = FaultLedger()
    fault = ledger.record(self_recoverable(code="SERIAL_PORTS_UNAVAILABLE", module="chassis",
                                           kind="chassis", remedy="operator_assist"))
    ran: list[str] = []
    remedy = Remedy("reconnect", frozenset({"*"}), lambda: ran.append("x"))
    outcome = engine(ledger, remedy).resolve(fault.key)
    assert ran == [], "a person's job must not be attempted by the robot"
    assert outcome.escalated is True and "需要人处理" in outcome.reason
    assert outcome.instruction.startswith("左臂无响应")


def test_a_remedy_that_moves_the_robot_waits_for_a_robot_that_may_move():
    ledger = FaultLedger()
    fault = ledger.record(self_recoverable())
    ran: list[str] = []
    moving = Remedy("retry_motion", frozenset({"ARM_NOT_FOUND"}), lambda: ran.append("moved"),
                    moves_robot=True)
    blocked = engine(ledger, moving).resolve(fault.key, robot_can_move=False)
    assert ran == [] and blocked.escalated is True
    assert "需要机器人可动" in blocked.reason
    allowed = engine(ledger, moving).resolve(fault.key, robot_can_move=True)
    assert ran == ["moved"] and allowed.resolved is False, "it ran, and still did not fix anything"


def test_a_remedy_that_raises_is_a_failed_attempt_not_a_crash():
    ledger = FaultLedger()
    fault = ledger.record(self_recoverable())

    def explode() -> None:
        raise RuntimeError("bus is gone")

    second_ran: list[str] = []
    outcome = engine(
        ledger,
        Remedy("explode", frozenset({"ARM_NOT_FOUND"}), explode),
        Remedy("reconnect", frozenset({"ARM_NOT_FOUND"}), lambda: second_ran.append("y")),
        max_attempts=2,
    ).resolve(fault.key)
    assert outcome.attempts[0]["error"] == "RuntimeError: bus is gone"
    assert second_ran == ["y"], "one broken remedy must not stop the next candidate"
    assert outcome.escalated is True and len(outcome.attempts) == 2


def test_attempts_are_bounded_per_fault():
    ledger = FaultLedger()
    fault = ledger.record(self_recoverable())
    ran: list[str] = []
    remedies = [Remedy(f"r{index}", frozenset({"ARM_NOT_FOUND"}), lambda index=index: ran.append(str(index)))
                for index in range(5)]
    outcome = engine(ledger, *remedies, max_attempts=2).resolve(fault.key)
    assert ran == ["0", "1"], "the budget is the whole point of having one"
    assert outcome.escalated is True and len(outcome.attempts) == 2


def test_a_fault_that_is_already_gone_runs_nothing_and_is_not_an_error():
    ledger = FaultLedger()
    ran: list[str] = []
    remedy = Remedy("rehome", frozenset({"*"}), lambda: ran.append("x"))
    outcome = engine(ledger, remedy).resolve("arm-left:ARM_NOT_FOUND")
    assert ran == [] and outcome.resolved is True
    assert "已不在台账里" in outcome.reason


def test_resolving_everything_keeps_the_operator_summary_deduplicated():
    ledger = FaultLedger()
    # One bus failure takes two modules down with the same instruction: to a
    # person that is one thing to do, not two.
    ledger.record(self_recoverable(code="BUS_DOWN", module="arm-left",
                                   user_instruction="总线无响应：检查总线供电后重新自检。"))
    ledger.record(self_recoverable(code="BUS_DOWN", module="arm-right",
                                   user_instruction="总线无响应：检查总线供电后重新自检。"))
    ledger.record(self_recoverable(code="ARM_NOT_FOUND", module="gripper-left"))
    outcomes = engine(ledger).resolve_all()
    assert len(outcomes) == 3 and all(outcome.escalated for outcome in outcomes)
    actions = default_remedy_order(outcomes)
    assert actions == ["总线无响应：检查总线供电后重新自检。", "左臂无响应：检查电源与总线，然后点『重新自检』。"]


def test_remedies_must_be_well_formed_and_unique():
    with pytest.raises(RemedyError) as raised:
        Remedy("", frozenset({"X"}), lambda: None)
    assert raised.value.code == "REMEDY_ID_MISSING"
    with pytest.raises(RemedyError) as raised:
        Remedy("r", frozenset(), lambda: None)
    assert raised.value.code == "REMEDY_SCOPE_MISSING"
    with pytest.raises(RemedyError) as raised:
        Remedy("r", frozenset({"X"}), "not callable")
    assert raised.value.code == "REMEDY_NOT_CALLABLE"
    duplicate = Remedy("same", frozenset({"X"}), lambda: None)
    with pytest.raises(RemedyError) as raised:
        FaultRemedyEngine(FaultLedger(), [duplicate, duplicate])
    assert raised.value.code == "REMEDY_DUPLICATE"
    with pytest.raises(RemedyError):
        FaultRemedyEngine(FaultLedger(), [], max_attempts=0)


def test_candidates_are_scoped_to_the_fault_code():
    ledger = FaultLedger()
    arm = ledger.record(self_recoverable())
    thermal = ledger.record(self_recoverable(code="ARM_THERMAL_HIGH"))
    rehome = Remedy("rehome", frozenset({"ARM_NOT_FOUND"}), lambda: None)
    cool = Remedy("cool", frozenset({"ARM_THERMAL_HIGH"}), lambda: None)
    scoped = engine(ledger, rehome, cool)
    assert [remedy.remedy_id for remedy in scoped.candidates(arm)] == ["rehome"]
    assert [remedy.remedy_id for remedy in scoped.candidates(thermal)] == ["cool"]
    # A wildcard remedy is a deployment's escape hatch for a whole module family.
    wildcard = engine(ledger, Remedy("reobserve", frozenset({"*"}), lambda: None))
    assert len(wildcard.candidates(arm)) == 1 and len(wildcard.candidates(thermal)) == 1
