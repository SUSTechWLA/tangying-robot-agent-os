"""Module faults: one vocabulary for "which part of the robot is broken, how badly,
and what may be done about it".

XLeRobot is not one device. It is a chassis, two arms, grippers, a head, cameras, a
bus and an onboard computer, and any of them can fail while the others keep
working. Today that knowledge arrives as scattered strings - ``ARM_FAILED``,
``SERIAL_PORTS_UNAVAILABLE``, ``HARDWARE_ERROR`` - with no module identity, no
severity and no remedy, which is why the brain can only say "something went wrong"
and a person has to guess which part to look at.

This module is the vocabulary and the two rules that make it useful:

* **A fault names a module and a severity**, so it can be turned into capability
  availability without anyone reading prose. The runtime already advertises
  ``available`` plus ``blockers`` per capability and the Go Agent already fails
  closed on that, so faults do not need a second gating path in another language.
  Severity ``safety`` is the exception that proves the rule: it removes every
  capability that depends on a module, because a stopped robot may not do work.
* **A fault says what may be done about it.** ``self_recover`` faults have a
  bounded remedy the robot may run by itself; ``operator_assist`` needs a person;
  ``service_required`` needs parts. An LLM may propose a remedy, but only inside
  the class the fault declares.

Two properties are deliberate:

* **Unknown is a value.** An unrecognised code is still recorded, with severity
  derived from what is known about the module rather than guessed.
* **Self-healing may not hide a fault.** A remedy that keeps being needed
  escalates: after ``ESCALATION_THRESHOLD`` **episodes** (&quot;fixed, then broken
  again&quot;, not &quot;observed again&quot;) a ``self_recover`` fault becomes
  ``operator_assist``, because a module that needs a reset every ten minutes is
  broken even while it works.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: Kinds of module a robot may declare. Adding one is data, not code: the
#: capability mapping below is what decides the consequences.
MODULE_KINDS = ("chassis", "arm", "gripper", "head", "camera", "compute", "power", "bus", "custom")

#: How bad it is. Ordered, so a caller can compare and a ledger can keep the worst.
SEVERITIES = ("info", "degraded", "blocked", "safety")

#: What may be done, in increasing order of who has to act.
#:
#: ``self_recover``    the robot has a bounded, pre-approved remedy (retry, re-home,
#:                     reconnect, re-observe). It may run it and must record it.
#: ``operator_assist`` a person must do something physical: release an e-stop, plug a
#:                     connector, power-cycle a module, replace a battery.
#: ``service_required`` the module needs parts or a workshop.
REMEDY_CLASSES = ("self_recover", "operator_assist", "service_required")

#: Faults that only inform: they never remove a capability.
_INFORMATIONAL = {"info"}

#: The severity that stops the whole robot rather than one module. A latched
#: emergency stop is not "the chassis is broken": nothing may move, whatever the
#: module list says, so the fault is not looked up per module.
SAFETY_SEVERITY = "safety"

#: After this many episodes a self-recoverable fault is treated as needing a
#: person. A robot that re-homes its arm every few minutes is not healthy; a
#: robot whose arm is simply still un-homed is not a repeat offender.
ESCALATION_THRESHOLD = 5


class FaultError(ValueError):
    """A fault record is malformed, or a remedy is not allowed for it."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Fault:
    """One module's problem, as a fact about the robot rather than an exception."""

    module_id: str
    code: str
    severity: str
    kind: str = "custom"
    detail: str = ""
    #: When the problem was first seen, and when it was last seen. An operator
    #: needs both: "broken since 12:01" is a different situation from "blinked
    #: once at 12:09", and only the pair distinguishes them.
    detected_at_unix_ms: int = 0
    last_seen_unix_ms: int = 0
    occurrences: int = 1
    remedy: str = "operator_assist"
    #: What a person should do, in the operator's language. Written by whoever
    #: knows the hardware, carried through every layer, shown verbatim.
    user_instruction: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.module_id).strip():
            raise FaultError("FAULT_MODULE_MISSING", "a fault must name the module it belongs to")
        if not str(self.code).strip():
            raise FaultError("FAULT_CODE_MISSING", "a fault must carry a code")
        if self.severity not in SEVERITIES:
            raise FaultError("FAULT_SEVERITY_UNKNOWN",
                             f"severity {self.severity!r} is not one of {', '.join(SEVERITIES)}")
        if self.remedy not in REMEDY_CLASSES:
            raise FaultError("FAULT_REMEDY_UNKNOWN",
                             f"remedy {self.remedy!r} is not one of {', '.join(REMEDY_CLASSES)}")
        if self.kind not in MODULE_KINDS:
            raise FaultError("FAULT_MODULE_KIND_UNKNOWN",
                             f"module kind {self.kind!r} is not one of {', '.join(MODULE_KINDS)}")
        if not isinstance(self.occurrences, int) or self.occurrences < 1:
            raise FaultError("FAULT_OCCURRENCES_INVALID", "occurrences must be a positive integer")

    @property
    def key(self) -> str:
        """Identity for de-duplication: the same problem on the same module."""
        return f"{self.module_id}:{self.code}"

    @property
    def blocks_capability(self) -> bool:
        return self.severity not in _INFORMATIONAL

    def with_occurrence(self, *, at_unix_ms: int) -> Fault:
        """Record another occurrence, escalating once it stops looking transient."""
        occurrences = self.occurrences + 1
        remedy = self.remedy
        severity = self.severity
        if remedy == "self_recover" and occurrences >= ESCALATION_THRESHOLD:
            # The robot can still work, but it keeps needing the same fix: that is
            # a person's problem now, not a retry's.
            remedy = "operator_assist"
        return Fault(module_id=self.module_id, code=self.code, severity=severity, kind=self.kind,
                     detail=self.detail, detected_at_unix_ms=self.detected_at_unix_ms,
                     last_seen_unix_ms=at_unix_ms, occurrences=occurrences,
                     remedy=remedy, user_instruction=self.user_instruction, evidence=self.evidence)

    def as_dict(self, *, now_unix_ms: int | None = None) -> dict[str, Any]:
        published: dict[str, Any] = {
            "moduleId": self.module_id, "kind": self.kind, "code": self.code,
            "severity": self.severity, "detail": self.detail,
            "occurrences": self.occurrences, "remedy": self.remedy,
            "userInstruction": self.user_instruction,
            "detectedAtUnixMs": self.detected_at_unix_ms,
            "lastSeenUnixMs": self.last_seen_unix_ms,
            "evidence": dict(self.evidence),
        }
        # Age is derived, so it is published only when a clock was given: an
        # absent key says "not computed", while a null would have to be decoded
        # as a number by the Go contract, which refuses nulls on principle.
        if now_unix_ms is not None:
            now = int(now_unix_ms)
            if self.detected_at_unix_ms:
                published["ageMs"] = max(0, now - int(self.detected_at_unix_ms))
            if self.last_seen_unix_ms:
                published["sinceLastSeenMs"] = max(0, now - int(self.last_seen_unix_ms))
        return published


def worst_severity(faults: Iterable[Fault]) -> str:
    """The most serious severity present, or ``info`` when there is none."""
    best = 0
    for fault in faults:
        best = max(best, SEVERITIES.index(fault.severity))
    return SEVERITIES[best]


def capability_impact(faults: Iterable[Fault],
                      capability_modules: Mapping[str, Iterable[str]]) -> dict[str, list[str]]:
    """Which capabilities a set of faults takes away, and which faults did it.

    ``capability_modules`` is the robot's own declaration - "navigation.navigate
    needs the chassis and the head camera" - so adding a module or a capability is
    data in the profile, not a change here. A capability with no blocking fault is
    absent from the result, which is what "still available" means.

    A ``safety`` fault is not matched per module: it takes away every capability
    that declares a dependency at all. The one capability that must survive is
    declared with no modules - ``emergency_stop`` - because a robot that has
    stopped still has to be stoppable.
    """
    blocking = [fault for fault in faults if fault.blocks_capability]
    safety = sorted({fault.key for fault in blocking if fault.severity == SAFETY_SEVERITY})
    impacted: dict[str, list[str]] = {}
    for capability, modules in capability_modules.items():
        required = {str(module) for module in modules}
        culprits = sorted({fault.key for fault in blocking if fault.module_id in required})
        if safety and required:
            culprits = sorted(set(culprits) | set(safety))
        if culprits:
            impacted[str(capability)] = culprits
    return impacted


class FaultLedger:
    """The robot's current faults, bounded and de-duplicated by module and code.

    This is the piece the brain reads: what is wrong right now, how long it has
    been wrong, how many times it has happened, and what the operator should do.
    It is deliberately not a log: the log is the incident bundle, this is state.
    """

    def __init__(self, *, max_faults: int = 64) -> None:
        if not isinstance(max_faults, int) or max_faults <= 0:
            raise FaultError("FAULT_LEDGER_INVALID", "max_faults must be a positive integer")
        self.max_faults = max_faults
        self._faults: dict[str, Fault] = {}
        #: Occurrences of faults that have since been cleared. A fault that comes
        #: back after being reported clear is a recurrence, and recurrences are
        #: what the escalation rule is about, so the count has to survive the
        #: clear. Bounded the same way the live table is.
        self._episodes: dict[str, int] = {}

    def record(self, fault: Fault, *, now_unix_ms: int | None = None) -> Fault:
        """Add or refresh a fault. The returned fault is what was stored.

        Recording a fault that is already present **refreshes** it: ``last_seen``
        moves and ``occurrences`` does not. Occurrences count episodes - the
        condition came back after having been reported clear - because a runtime
        republishes the conditions it can attest on every observation. Counting
        samples instead would inflate "7 times" into "7 times in three seconds"
        and escalate every self-recoverable fault to "a person must fix this"
        almost immediately, which is the opposite of what that rule is for: a
        module that needs the same fix every few minutes is broken, a module that
        is simply still broken is not a repeat offender.
        """
        stamp = int(now_unix_ms) if isinstance(now_unix_ms, int) and now_unix_ms > 0 else fault.detected_at_unix_ms
        existing = self._faults.get(fault.key)
        if existing is not None:
            stored = Fault(**{**fault.__dict__,
                              "occurrences": existing.occurrences,
                              "detected_at_unix_ms": existing.detected_at_unix_ms or stamp,
                              "last_seen_unix_ms": stamp or existing.last_seen_unix_ms})
        else:
            if len(self._faults) >= self.max_faults:
                raise FaultError("FAULT_LEDGER_FULL",
                                 f"the ledger already holds {self.max_faults} faults; clear resolved ones first")
            seen = stamp or fault.detected_at_unix_ms
            stored = Fault(**{**fault.__dict__, "detected_at_unix_ms": seen, "last_seen_unix_ms": seen})
            previous = self._episodes.pop(fault.key, 0)
            if previous:
                # Same problem, new episode: it was fixed and came back. Age
                # restarts at this sighting; the count carries on.
                stored = Fault(**{**stored.__dict__, "occurrences": previous})
                stored = stored.with_occurrence(at_unix_ms=seen)
        self._faults[stored.key] = stored
        return stored

    def clear(self, module_id: str, code: str = "") -> int:
        """Forget faults that no longer apply: a cleared fault must not linger.

        With no code it clears the whole module, which is what a successful
        module self-test reports. What is remembered is how often this fault has
        already happened, so a recurrence can be told from a first sighting.
        """
        keys = [key for key, fault in self._faults.items()
                if fault.module_id == module_id and (not code or fault.code == code)]
        for key in keys:
            self._remember(self._faults.pop(key))
        return len(keys)

    def _remember(self, fault: Fault) -> None:
        self._episodes[fault.key] = fault.occurrences
        while len(self._episodes) > self.max_faults:
            self._episodes.pop(next(iter(self._episodes)))

    def faults(self) -> list[Fault]:
        """Current faults, most severe first, then oldest first."""
        return sorted(self._faults.values(),
                      key=lambda fault: (-SEVERITIES.index(fault.severity),
                                         fault.detected_at_unix_ms or math.inf, fault.key))

    def get(self, key: str) -> Fault | None:
        return self._faults.get(key)

    def remedy_allowed(self, key: str, remedy: str) -> bool:
        """Whether this remedy may be attempted for this fault.

        The rule an LLM has to live with: it may run a remedy the fault itself
        declares as self-recoverable, and nothing else. Asking for a person's
        action is always allowed - that is a message, not a motion.
        """
        fault = self._faults.get(key)
        if fault is None:
            raise FaultError("FAULT_UNKNOWN", f"no current fault with key {key!r}")
        if remedy not in REMEDY_CLASSES:
            raise FaultError("FAULT_REMEDY_UNKNOWN",
                             f"remedy {remedy!r} is not one of {', '.join(REMEDY_CLASSES)}")
        if remedy == "self_recover":
            return fault.remedy == "self_recover"
        return True

    def snapshot(self, *, now_unix_ms: int | None = None,
                 capability_modules: Mapping[str, Iterable[str]] | None = None) -> dict[str, Any]:
        """What the console shows and the brain reads."""
        faults = self.faults()
        snapshot: dict[str, Any] = {
            "schemaVersion": "robot.faults.v1",
            "severity": worst_severity(faults),
            "count": len(faults),
            "faults": [fault.as_dict(now_unix_ms=now_unix_ms) for fault in faults],
            "operatorActions": sorted({fault.user_instruction for fault in faults
                                       if fault.user_instruction and fault.severity != "info"}),
        }
        if capability_modules is not None:
            impacted = capability_impact(faults, capability_modules)
            snapshot["unavailableCapabilities"] = sorted(impacted)
            snapshot["capabilityBlockers"] = impacted
        return snapshot
