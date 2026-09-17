"""The Python fault publisher and the Go fault consumer, on the same document.

`robot.faults.v1` is written by the Robot Runtime and read by the Go Agent, where
it gates capabilities. Two languages, one contract: this test takes the document
the real sim runtime publishes, hands it to the real Go decoder, and checks that
both sides mean the same thing. A drift here would not crash anything - it would
quietly let a plan run against a module the robot already reported as broken, or
refuse a robot that is fine.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from tangying_robot_gateway.module_faults import capability_impact
from tangying_robot_gateway.runtime import ObservationRequest
from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def faults_probe(tmp_path_factory):
    probe = tmp_path_factory.mktemp("faults-go-probe") / "probe"
    built = subprocess.run(
        ["go", "build", "-o", str(probe), "./tests/contract/faults_probe"],
        cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
    )
    assert built.returncode == 0, built.stderr
    return probe


def decode_with_go(probe: Path, document: dict) -> dict:
    decoded = subprocess.run(
        [str(probe)], input=json.dumps(document), capture_output=True, text=True, timeout=60, check=False,
    )
    assert decoded.returncode == 0, decoded.stderr
    return json.loads(decoded.stdout)


def published_faults(runtime) -> dict:
    """The document the running robot actually publishes, taken from its observation."""
    _scene, _pixels, public = runtime.capture_scene()
    return public["faults"]


def test_a_latched_stop_published_by_python_is_read_as_a_safety_stop_by_go(faults_probe):
    runtime = RgbdRuntimeService(RgbdTabletopWorld.seeded(7))
    try:
        clean = decode_with_go(faults_probe, published_faults(runtime))
        assert clean["accepted"] and clean["count"] == 0 and clean["severity"] == "info"
        assert clean["safetyStopped"] is False and clean["blocking"] == 0

        # The real path: the emergency-stop RPC latches the runtime, and the next
        # observation carries the fault.
        runtime.EmergencyStop(robot_pb2.EStopRequest(reason="contract test"), None)
        document = published_faults(runtime)
        read_back = decode_with_go(faults_probe, document)

        assert read_back["accepted"] is True
        assert read_back["severity"] == "safety" and read_back["safetyStopped"] is True
        assert read_back["keys"] == ["estop:EMERGENCY_STOP_LATCHED"]
        assert read_back["blocking"] == 1, "a safety fault is not informational"
        # The operator instruction travels verbatim: the console, the LLM and the
        # incident record must quote the same sentence the robot produced.
        assert read_back["firstInstruction"] == document["faults"][0]["userInstruction"]
        assert "现场复位急停" in read_back["firstInstruction"]
    finally:
        runtime.close()


def test_the_published_blockers_are_the_ones_the_ledger_computed(faults_probe):
    runtime = RgbdRuntimeService(RgbdTabletopWorld.seeded(7, scene="home_task"))
    try:
        # The seeded home_task world arrives with an active map, so navigation is
        # genuinely available and nothing is blocked. This test is about the case
        # where it is not: without this, the assertion below was checking an empty
        # list against an empty list and passing for the wrong reason — or, once
        # the world gained a map, failing while the robot was reporting correctly.
        runtime.workflow.active = None

        document = published_faults(runtime)
        expected = capability_impact(runtime._faults.faults(), runtime.CAPABILITY_MODULES)
        assert document["capabilityBlockers"] == expected
        assert document["unavailableCapabilities"] == sorted(expected)
        assert "navigation.navigate" in document["unavailableCapabilities"], \
            "没有启用地图的移动机器人必须自己说导航不可用"

        read_back = decode_with_go(faults_probe, document)
        assert read_back["accepted"] is True
        assert read_back["unavailableCapabilities"] == document["unavailableCapabilities"]
    finally:
        runtime.close()


def test_a_robot_with_an_active_map_blocks_nothing(faults_probe):
    """The other direction, which the test above used to be.

    A robot that has a map must not claim navigation is unavailable. Reporting a
    capability as blocked when it works is how a fault list trains an operator to
    ignore it.
    """
    runtime = RgbdRuntimeService(RgbdTabletopWorld.seeded(7, scene="home_task"))
    try:
        assert runtime.workflow.active, "this world is expected to have an active map"
        document = published_faults(runtime)
        assert document["count"] == 0
        assert document["unavailableCapabilities"] == []
        assert document["capabilityBlockers"] == {}
    finally:
        runtime.close()


def test_go_refuses_a_fault_document_that_does_not_add_up(faults_probe):
    runtime = RgbdRuntimeService(RgbdTabletopWorld.seeded(7))
    try:
        runtime.EmergencyStop(robot_pb2.EStopRequest(reason="contract test"), None)
        healthy = published_faults(runtime)

        def tampered(mutate):
            document = json.loads(json.dumps(healthy))
            mutate(document)
            return decode_with_go(faults_probe, document)

        assert tampered(lambda d: d.update(count=d["count"] + 1))["accepted"] is False
        assert tampered(lambda d: d.update(severity="degraded"))["accepted"] is False
        assert tampered(lambda d: d["faults"][0].update(remedy="pray"))["accepted"] is False
        assert tampered(lambda d: d["faults"][0].update(severity="catastrophic"))["accepted"] is False
        # A null where a number belongs is refused rather than decoded as zero:
        # "no age reported" and "zero milliseconds old" are different claims.
        assert tampered(lambda d: d["faults"][0].update(ageMs=None))["accepted"] is False
        # And a consumer that does not understand the document must not guess.
        assert tampered(lambda d: d.update(rootCause="probably the cable"))["accepted"] is False
    finally:
        runtime.close()


def test_the_real_robots_fault_report_is_read_by_go(faults_probe, tmp_path):
    """The seam that did not exist until now.

    `FaultLedger` used to be instantiated in exactly one place — the simulator — so
    a real robot published no `robot.faults.v1` document at all. The agent's
    component-fault rule reads that document, so on hardware it could never fire:
    the agent could see an emergency stop and a failed action, and it could not see
    "this robot is not commissioned yet", which is the fault a new owner has.

    The driver here is disconnected on purpose. Attesting a fault must not require
    reading hardware, or the robot would have to work before it could say why it
    does not.
    """
    from tangying_robot_gateway.xlerobot_backend import XLeRobotDirectBackend
    from xlerobot_adapter.driver import XLeRobotDriver

    driver = XLeRobotDriver(
        upstream_root=tmp_path, calibration_root=tmp_path,
        ports=("/dev/ttyACM0", "/dev/ttyACM1"), path_exists=lambda path: True,
    )
    observation = XLeRobotDirectBackend(driver, robot_id="xlerobot-contract").observe(
        ObservationRequest())
    document = observation.robot_state["faults"]

    read_back = decode_with_go(faults_probe, document)
    assert read_back["accepted"] is True, read_back
    # The three preconditions plus whatever the driver itself reports, which on this
    # disconnected driver is its own blocker list.
    codes = {fault["code"] for fault in document["faults"]}
    assert {"ROBOT_NOT_ARMED", "ENTITY_PROVIDER_REQUIRED", "VERIFIER_REQUIRED"} <= codes, codes
    assert read_back["blocking"] == len(codes), read_back
    # The robot's own sentence survives the boundary. That sentence is what the
    # console, the model and the incident record all quote, so a paraphrase on
    # either side would be a fourth version of the same advice.
    assert "使能" in read_back["firstInstruction"] or "现场安全" in read_back["firstInstruction"]

    # The fault document itself carries nothing the contract does not define. This
    # assertion is why the remedy record lives beside it: the first version put
    # `remedyOutcomes` inside the document and the Go decoder — which refuses
    # unknown fields on purpose — rejected the robot's entire fault report.
    assert set(document) == {"schemaVersion", "severity", "count", "faults", "operatorActions"}, sorted(document)

    # The remedy record travels as a sibling, so "it fixed itself" is never an
    # unexplained event even though nothing consumes it yet.
    outcomes = observation.robot_state["remedyOutcomes"]
    assert outcomes, "the engine was not driven"
    for outcome in outcomes:
        assert outcome["resolved"] is False, "a fault needing a person was reported as self-resolved"
        assert outcome["escalated"] is True
