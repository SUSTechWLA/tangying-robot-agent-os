"""The contract between a fault and the remedy that is allowed to fix it.

`FaultRemedyEngine` is only as useful as its input, and for a long time it had
none: `FaultLedger` was instantiated in exactly one place — the MuJoCo simulator —
so the real robot published no fault report at all. That mattered well beyond the
engine sitting idle. The agent's `ANOMALY_COMPONENT_FAULT` rule reads the robot's
own `robot.faults.v1` document, so on real hardware it could never fire: the agent
could see an emergency stop and a failed action, and it could not see "this robot
is not commissioned yet", which is the fault a new owner actually has.

These tests are about the seam that was missing, and about the two ways it can go
wrong silently:

* a fault that declares `self_recover` while no remedy is registered for it — the
  engine then escalates saying "no usable remedy (unregistered, or it needs the
  robot to be movable)", which is true and useless; and
* a remedy registered for nothing, which is a deployment believing it wired
  self-repair when it did not.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tangying_robot_gateway.fault_remedy import FaultRemedyEngine, Remedy
from tangying_robot_gateway.module_faults import Fault, FaultLedger

ROOT = Path(__file__).resolve().parents[3]


def fault_construction_sites() -> list[tuple[str, int, dict[str, str]]]:
    """Every ``Fault(...)`` built anywhere in the repository, with its keywords.

    Parsed rather than grepped: a keyword argument's value decides whether the
    engine may act, and a regular expression over the source would happily match a
    ``remedy=`` inside a docstring.
    """
    sites: list[tuple[str, int, dict[str, str]]] = []
    for path in sorted(ROOT.rglob("*.py")):
        parts = set(path.parts)
        if parts & {".venv", "node_modules", "vendor", "build"}:
            continue
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name != "Fault":
                continue
            keyword_values = {
                keyword.arg: keyword.value.value
                for keyword in node.keywords
                if keyword.arg and isinstance(keyword.value, ast.Constant)
            }
            sites.append((str(path.relative_to(ROOT)), node.lineno, keyword_values))
    return sites


def test_the_repository_builds_at_least_one_fault():
    """A guard on the guard.

    If the scan stops finding fault constructions the other tests here pass
    vacuously, which is how a contract test becomes decoration.
    """
    assert fault_construction_sites(), "no Fault(...) construction found; the scan is broken"


def test_every_self_recover_fault_has_a_remedy_registered_for_it():
    """The seam that would otherwise fail silently.

    A fault that says ``self_recover`` is a claim that the robot can fix it. If no
    remedy is registered for that code, `FaultRemedyEngine` escalates with "no
    usable remedy" — which reads like a deployment gap and is really a wiring gap,
    and nobody finds out until the fault happens on a robot.

    Today no fault declares ``self_recover``, so this test passes on an empty set.
    That is the point: it is the check that has to exist *before* the first one
    does, not after.
    """
    self_recoverable = [
        (path, line, keywords)
        for path, line, keywords in fault_construction_sites()
        if keywords.get("remedy") == "self_recover"
    ]
    if not self_recoverable:
        pytest.skip("no fault declares self_recover; nothing to pair with a remedy yet")

    registered: set[str] = set()
    for remedy_id, codes in registered_remedies():
        registered.add(remedy_id)
        _ = codes
    # A remedy covers fault codes, not fault ids, so the pairing is by code.
    covered: set[str] = set()
    for _remedy_id, codes in registered_remedies():
        covered |= codes
    for path, line, keywords in self_recoverable:
        code = keywords.get("code")
        assert code in covered, (
            f"{path}:{line} declares remedy=self_recover for {code!r}, and no remedy in the "
            "deployment applies to that code. The engine would escalate it saying no remedy is "
            "registered, which is a wiring gap reported as a deployment gap."
        )


def registered_remedies() -> list[tuple[str, set[str]]]:
    """Remedies actually constructed outside tests, with the codes they cover.

    Parsed the same way as the faults, so "it is wired" means the constructor runs,
    not that the class exists.
    """
    remedies: list[tuple[str, set[str]]] = []
    for path in sorted(ROOT.rglob("*.py")):
        if set(path.parts) & {".venv", "node_modules", "vendor", "build"}:
            continue
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name != "Remedy":
                continue
            remedy_id = ""
            codes: set[str] = set()
            for keyword in node.keywords:
                if keyword.arg == "remedy_id" and isinstance(keyword.value, ast.Constant):
                    remedy_id = str(keyword.value.value)
                if keyword.arg == "applies_to":
                    codes = {
                        str(element.value)
                        for element in ast.walk(keyword.value)
                        if isinstance(element, ast.Constant) and isinstance(element.value, str)
                    }
                    if isinstance(keyword.value, ast.Call) and getattr(keyword.value.func, "id", "") == "frozenset":
                        codes = {
                            str(element.value)
                            for element in ast.walk(keyword.value)
                            if isinstance(element, ast.Constant) and isinstance(element.value, str)
                        }
            remedies.append((remedy_id, codes))
    return remedies


# --- the engine's own safety properties, on the real input -------------------


def test_a_fault_needing_a_person_is_escalated_and_never_repaired():
    ledger = FaultLedger()
    ledger.record(Fault(
        module_id="arm", code="ROBOT_NOT_ARMED", kind="custom", severity="blocked",
        remedy="operator_assist", detail="torque is off",
        user_instruction="先在本体上确认现场安全后使能",
    ))
    outcome = FaultRemedyEngine(ledger, ()).resolve("arm:ROBOT_NOT_ARMED")
    assert outcome.resolved is False
    assert outcome.escalated is True
    assert "现场安全" in outcome.instruction
    assert outcome.attempts == [], "a repair was attempted for a fault that needs a person"


def test_a_motion_remedy_never_runs_without_an_admission_that_the_robot_may_move():
    """The property that keeps an unattended process from moving a robot.

    This system has no unattended arming: `--arm` requires an interactive local
    terminal. So `robot_can_move` is False for every observation, and a remedy that
    moves the robot must therefore never be selected.
    """
    ran: list[str] = []
    ledger = FaultLedger()
    ledger.record(Fault(
        module_id="arm", code="STUCK", kind="custom", severity="blocked",
        remedy="self_recover", detail="arm reports a stall",
    ))
    remedy = Remedy(
        remedy_id="arm.home", applies_to=frozenset({"STUCK"}),
        run=lambda: ran.append("moved"), moves_robot=True,
    )
    outcome = FaultRemedyEngine(ledger, [remedy]).resolve("arm:STUCK", robot_can_move=False)
    assert ran == [], "a motion remedy ran without an admission that the robot may move"
    assert outcome.escalated is True

    # And with the admission it does run, so the guard is a guard and not a wall.
    FaultRemedyEngine(ledger, [remedy]).resolve("arm:STUCK", robot_can_move=True)
    assert ran == ["moved"]


def test_the_verdict_comes_from_the_ledger_not_from_the_remedy():
    """A repair that reports success while the fault is still there is a failure.

    This is the same scepticism the closure gate applies to a tool: a return value
    is a claim, and the record is the evidence.
    """
    ledger = FaultLedger()
    ledger.record(Fault(
        module_id="arm", code="STUCK", kind="custom", severity="blocked",
        remedy="self_recover", detail="arm reports a stall",
    ))
    optimistic = Remedy(
        remedy_id="arm.claim-done", applies_to=frozenset({"STUCK"}),
        run=lambda: True, description="returns success and changes nothing",
    )
    outcome = FaultRemedyEngine(ledger, [optimistic]).resolve("arm:STUCK")
    assert outcome.resolved is False, "a remedy's own report was taken as proof"
    assert outcome.attempts[0]["faultCleared"] is False
    assert outcome.escalated is True
