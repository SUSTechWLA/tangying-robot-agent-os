"""Automatic recovery: bounded remedies that are judged by the fault disappearing.

The system already refuses to trust a tool that reports its own success - the
closure gate demands a fresh observation of the world. A robot that repairs itself
deserves exactly the same scepticism, and this module is where that rule is
enforced for hardware faults:

* **A remedy never declares victory.** It returns whatever it likes; the engine
  re-reads the fault ledger and the fault must be *gone* for the attempt to count.
  A recovery action that says "done" while the module is still broken is recorded
  as a failure, which is what an operator needs to see.
* **Only what the fault allows.** A fault that needs a person (`operator_assist`)
  or parts (`service_required`) is escalated immediately, with its instruction -
  no amount of retrying fixes a disconnected cable.
* **Every attempt is evidence.** Who ran what, for how long, and what happened,
  kept whether it worked or not, so "it fixed itself" is never an unexplained
  event.
* **Bounded.** Per fault and per ledger: after `max_attempts` failures the fault is
  escalated to a person rather than retried forever.

The remedies themselves are supplied by the deployment - "re-observe the scene",
"re-home the arm", "reconnect the device", "retry the command" - each an existing
bounded capability. This module decides *whether* and *in what order*, and keeps
the record; it never invents a motion.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .module_faults import Fault, FaultLedger

#: How many different remedies one fault may try in a single resolve() call.
DEFAULT_MAX_ATTEMPTS = 2


class RemedyError(ValueError):
    """A remedy is malformed or claims something it may not."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Remedy:
    """A bounded recovery action the robot may run by itself.

    ``applies_to`` is a set of fault codes (or ``"*"``), so adding a fault does not
    require touching the engine - the deployment registers the remedy next to the
    driver that knows how to run it.
    """

    remedy_id: str
    applies_to: frozenset[str]
    run: Callable[[], Any]
    #: What this action does, in the operator's language. Recorded, not shown as a
    #: substitute for the fault's own instruction.
    description: str = ""
    #: A remedy that moves the robot needs the same admission as any other motion.
    #: The engine will not run it unless the caller says the robot is safe to move.
    moves_robot: bool = False

    def __post_init__(self) -> None:
        if not str(self.remedy_id).strip():
            raise RemedyError("REMEDY_ID_MISSING", "a remedy needs an id")
        if not self.applies_to:
            raise RemedyError("REMEDY_SCOPE_MISSING",
                              f"remedy {self.remedy_id!r} declares no fault codes it applies to")
        if not callable(self.run):
            raise RemedyError("REMEDY_NOT_CALLABLE", f"remedy {self.remedy_id!r} has no action")

    def covers(self, fault: Fault) -> bool:
        return "*" in self.applies_to or fault.code in self.applies_to


@dataclass
class RemedyOutcome:
    """What actually happened: attempts, verdict, and what the operator must do."""

    fault_key: str
    resolved: bool
    attempts: list[dict[str, Any]] = field(default_factory=list)
    escalated: bool = False
    instruction: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"faultKey": self.fault_key, "resolved": self.resolved,
                "escalated": self.escalated, "instruction": self.instruction,
                "reason": self.reason, "attempts": self.attempts}


class FaultRemedyEngine:
    """Runs the remedies a fault allows, and reports what the ledger says afterwards."""

    def __init__(self, ledger: FaultLedger, remedies: Iterable[Remedy], *,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not isinstance(max_attempts, int) or max_attempts <= 0:
            raise RemedyError("REMEDY_LIMIT_INVALID", "max_attempts must be a positive integer")
        self.ledger = ledger
        self.remedies = list(remedies)
        self.max_attempts = max_attempts
        self._clock = clock
        seen: set[str] = set()
        for remedy in self.remedies:
            if remedy.remedy_id in seen:
                raise RemedyError("REMEDY_DUPLICATE", f"two remedies share the id {remedy.remedy_id!r}")
            seen.add(remedy.remedy_id)

    def candidates(self, fault: Fault, *, robot_can_move: bool = False) -> list[Remedy]:
        """Remedies that may be attempted for this fault, in registration order."""
        if fault.remedy != "self_recover":
            return []
        return [remedy for remedy in self.remedies
                if remedy.covers(fault) and (robot_can_move or not remedy.moves_robot)]

    def resolve(self, fault_key: str, *, robot_can_move: bool = False) -> RemedyOutcome:
        """Try to clear one fault. The ledger decides whether it worked."""
        fault = self.ledger.get(fault_key)
        if fault is None:
            # Already gone: someone else fixed it, or it was a transient that
            # cleared itself. Not an error, and not a reason to run anything.
            return RemedyOutcome(fault_key=fault_key, resolved=True,
                                 reason="该故障已不在台账里（可能已自愈）")

        if fault.remedy != "self_recover":
            return RemedyOutcome(
                fault_key=fault_key, resolved=False, escalated=True,
                instruction=fault.user_instruction or fault.detail,
                reason=f"故障声明为 {fault.remedy}，需要人处理，自动恢复不介入")

        candidates = self.candidates(fault, robot_can_move=robot_can_move)
        if not candidates:
            return RemedyOutcome(
                fault_key=fault_key, resolved=False, escalated=True,
                instruction=fault.user_instruction or fault.detail,
                reason="没有可用的自愈动作（未注册，或动作需要机器人可动）")

        outcome = RemedyOutcome(fault_key=fault_key, resolved=False)
        for remedy in candidates[:self.max_attempts]:
            started = self._clock()
            error = ""
            try:
                remedy.run()
            except Exception as exc:  # noqa: BLE001 - a failed remedy is a recorded attempt, not a crash
                error = f"{type(exc).__name__}: {exc}"
            duration_ms = round((self._clock() - started) * 1000, 3)
            # The verdict comes from the ledger, never from the remedy's own report.
            still_present = self.ledger.get(fault_key) is not None
            outcome.attempts.append({
                "remedyId": remedy.remedy_id,
                "description": remedy.description,
                "durationMs": duration_ms,
                "error": error,
                "faultCleared": not still_present,
            })
            if not still_present:
                outcome.resolved = True
                outcome.reason = f"自愈动作 {remedy.remedy_id} 执行后故障已从台账消失"
                return outcome

        outcome.escalated = True
        outcome.instruction = fault.user_instruction or fault.detail
        outcome.reason = (f"尝试 {len(outcome.attempts)} 个自愈动作后故障仍在"
                          "（成功以故障消失为准，不以动作返回为准）")
        return outcome

    def resolve_all(self, *, robot_can_move: bool = False) -> list[RemedyOutcome]:
        """Work through the current faults, worst first. Each is independent."""
        return [self.resolve(fault.key, robot_can_move=robot_can_move)
                for fault in self.ledger.faults()]


def default_remedy_order(outcomes: Iterable[RemedyOutcome]) -> list[str]:
    """The operator-facing summary: what still needs a person, and what to do.

    Deduplicated and ordered so a console can show one line per action instead of
    one line per fault - three modules down because one bus died is one problem to
    a person, not three.
    """
    instructions: list[str] = []
    for outcome in outcomes:
        if outcome.escalated and outcome.instruction and outcome.instruction not in instructions:
            instructions.append(outcome.instruction)
    return instructions
