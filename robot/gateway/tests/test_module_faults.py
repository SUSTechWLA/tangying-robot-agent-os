"""Module faults: identity, severity, remedy class, gating and escalation.

The design claim these tests hold is that a hardware problem can travel from a
driver to the brain as *data* - which module, how bad, what may be done - and that
the two rules which keep that safe actually hold: a fault removes capabilities
rather than being interpreted in prose, and self-healing cannot quietly hide a
module that keeps breaking.
"""

from __future__ import annotations

import pytest
from tangying_robot_gateway.module_faults import (
    ESCALATION_THRESHOLD,
    Fault,
    FaultError,
    FaultLedger,
    capability_impact,
    worst_severity,
)

CAPABILITY_MODULES = {
    "navigation.navigate": ["chassis", "head"],
    "manipulation.pick": ["arm-left", "gripper-left"],
    "manipulation.place": ["arm-left", "gripper-left"],
    "observe_scene": ["head"],
    "emergency_stop": [],  # always available: it is the last line, not a module
}


def arm_fault(**overrides) -> Fault:
    values = {"module_id": "arm-left", "code": "ARM_NOT_FOUND", "kind": "arm",
              "severity": "blocked", "remedy": "self_recover",
              "user_instruction": "左臂无响应：检查电源与总线连接后重启机器人服务。",
              "detected_at_unix_ms": 1_000_000, "occurrences": 1}
    values.update(overrides)
    return Fault(**values)


def chassis_fault(**overrides) -> Fault:
    values = {"module_id": "chassis", "code": "SERIAL_PORTS_UNAVAILABLE", "kind": "chassis",
              "severity": "blocked", "remedy": "operator_assist",
              "user_instruction": "底盘串口未找到：重新插拔底盘 USB 并确认供电。",
              "detected_at_unix_ms": 1_000_000, "occurrences": 1}
    values.update(overrides)
    return Fault(**values)


def test_a_fault_names_its_module_and_removes_exactly_what_depends_on_it():
    faults = [arm_fault()]
    impacted = capability_impact(faults, CAPABILITY_MODULES)
    # The left arm is gone, so picking and placing are gone...
    assert set(impacted) == {"manipulation.pick", "manipulation.place"}
    assert impacted["manipulation.pick"] == ["arm-left:ARM_NOT_FOUND"]
    # ...and the robot can still drive and still look.
    assert "navigation.navigate" not in impacted
    assert "observe_scene" not in impacted


def test_a_chassis_fault_grounds_the_robot_but_leaves_the_arm_usable():
    impacted = capability_impact([chassis_fault()], CAPABILITY_MODULES)
    assert set(impacted) == {"navigation.navigate"}


def test_an_informational_fault_removes_nothing():
    # A warm motor or a low-but-usable battery is news, not a capability loss.
    faults = [arm_fault(severity="info", code="ARM_THERMAL_HIGH", remedy="self_recover"),
              arm_fault(severity="degraded", code="ARM_TORQUE_LIMITED")]
    impacted = capability_impact(faults, CAPABILITY_MODULES)
    assert impacted["manipulation.pick"] == ["arm-left:ARM_TORQUE_LIMITED"], \
        "only the fault that blocks may appear as a blocker"


def test_several_modules_down_are_all_reported_for_the_capability_they_share():
    faults = [arm_fault(module_id="arm-left", code="A"), arm_fault(module_id="gripper-left", code="B")]
    impacted = capability_impact(faults, CAPABILITY_MODULES)
    assert impacted["manipulation.pick"] == ["arm-left:A", "gripper-left:B"]


def test_the_snapshot_is_what_the_console_shows_and_the_brain_reads():
    ledger = FaultLedger()
    ledger.record(arm_fault(), now_unix_ms=1_000_000)
    ledger.record(chassis_fault(), now_unix_ms=1_100_000)
    snapshot = ledger.snapshot(now_unix_ms=1_200_000, capability_modules=CAPABILITY_MODULES)
    assert snapshot["schemaVersion"] == "robot.faults.v1"
    assert snapshot["severity"] == "blocked" and snapshot["count"] == 2
    # Most severe first, and every fault carries its age and its instruction.
    assert snapshot["faults"][0]["moduleId"] in {"arm-left", "chassis"}
    assert all(fault["ageMs"] is not None for fault in snapshot["faults"])
    assert any("重新插拔底盘" in action for action in snapshot["operatorActions"])
    assert set(snapshot["unavailableCapabilities"]) == {"manipulation.pick", "manipulation.place",
                                                       "navigation.navigate"}


def test_repeating_a_fault_escalates_it_from_a_retry_to_a_person():
    ledger = FaultLedger()
    fault = arm_fault()
    for _ in range(ESCALATION_THRESHOLD - 1):
        fault = ledger.record(fault, now_unix_ms=1_000_000)
    assert fault.remedy == "self_recover" and fault.occurrences == ESCALATION_THRESHOLD - 1
    fault = ledger.record(fault, now_unix_ms=1_000_000)
    assert fault.occurrences == ESCALATION_THRESHOLD
    # A module needing the same fix repeatedly is broken even while it runs.
    assert fault.remedy == "operator_assist"
    assert ledger.remedy_allowed(fault.key, "self_recover") is False
    assert ledger.remedy_allowed(fault.key, "operator_assist") is True


def test_repeating_a_fault_keeps_the_first_sighting_time():
    ledger = FaultLedger()
    first = ledger.record(arm_fault(), now_unix_ms=1_000_000)
    again = ledger.record(arm_fault(), now_unix_ms=1_600_000)
    assert again.occurrences == 2
    # Age must describe how long the problem has existed, not when it last blinked;
    # the pair of stamps is what tells "broken since 12:01" from "blinked at 12:10".
    assert again.detected_at_unix_ms == first.detected_at_unix_ms == 1_000_000
    assert again.last_seen_unix_ms == 1_600_000
    published = again.as_dict(now_unix_ms=1_700_000)
    assert published["ageMs"] == 700_000 and published["sinceLastSeenMs"] == 100_000


def test_only_a_self_recoverable_fault_may_be_repaired_without_a_person():
    ledger = FaultLedger()
    arm = ledger.record(arm_fault())
    chassis = ledger.record(chassis_fault())
    assert ledger.remedy_allowed(arm.key, "self_recover") is True
    assert ledger.remedy_allowed(chassis.key, "self_recover") is False
    # Asking for a person is a message, not a motion: always allowed.
    assert ledger.remedy_allowed(chassis.key, "operator_assist") is True
    with pytest.raises(FaultError, match="no current fault"):
        ledger.remedy_allowed("chassis:NOPE", "self_recover")
    with pytest.raises(FaultError, match="remedy"):
        ledger.remedy_allowed(arm.key, "replace_motor")


def test_clearing_a_repaired_module_removes_its_faults():
    ledger = FaultLedger()
    ledger.record(arm_fault())
    ledger.record(arm_fault(code="ARM_THERMAL_HIGH", severity="info"))
    ledger.record(chassis_fault())
    assert ledger.clear("arm-left") == 2
    assert [fault.module_id for fault in ledger.faults()] == ["chassis"]
    assert ledger.clear("arm-left") == 0
    assert ledger.clear("chassis", "SERIAL_PORTS_UNAVAILABLE") == 1
    assert ledger.snapshot()["severity"] == "info" and ledger.snapshot()["count"] == 0


def test_malformed_faults_are_refused_rather_than_recorded():
    with pytest.raises(FaultError) as raised:
        arm_fault(module_id="  ")
    assert raised.value.code == "FAULT_MODULE_MISSING"
    with pytest.raises(FaultError):
        arm_fault(code="")
    with pytest.raises(FaultError) as raised:
        arm_fault(severity="catastrophic")
    assert raised.value.code == "FAULT_SEVERITY_UNKNOWN"
    # An unknown code is fine - the vocabulary is open - but an unknown severity,
    # remedy class or module kind is a data defect and is refused.
    assert arm_fault(code="ARM_SOMETHING_NEW").code == "ARM_SOMETHING_NEW"
    with pytest.raises(FaultError):
        arm_fault(remedy="pray")
    with pytest.raises(FaultError):
        arm_fault(kind="teapot")


def test_the_ledger_is_bounded_and_says_so_instead_of_dropping_faults():
    ledger = FaultLedger(max_faults=2)
    ledger.record(arm_fault(code="A"))
    ledger.record(arm_fault(code="B"))
    with pytest.raises(FaultError) as raised:
        ledger.record(arm_fault(code="C"))
    assert raised.value.code == "FAULT_LEDGER_FULL", \
        "a full ledger must be visible, not silently forget the oldest fault"


def test_worst_severity_drives_the_headline():
    assert worst_severity([]) == "info"
    assert worst_severity([arm_fault(severity="info"), arm_fault(code="B", severity="degraded")]) == "degraded"
    assert worst_severity([arm_fault(severity="safety"), arm_fault(code="B", severity="blocked")]) == "safety"


def test_a_module_with_no_capabilities_declared_affects_nothing():
    # emergency_stop depends on nothing: it must stay callable when everything
    # else is broken, which is what the empty list means.
    impacted = capability_impact([chassis_fault(), arm_fault()], CAPABILITY_MODULES)
    assert "emergency_stop" not in impacted
