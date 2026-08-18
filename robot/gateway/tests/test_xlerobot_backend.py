from __future__ import annotations

import time

from tangying_robot_gateway.backend import BackendResult
from tangying_robot_gateway.runtime import Command, ObservationRequest
from tangying_robot_gateway.xlerobot_backend import XLeRobotDirectBackend


class FakeDriver:
    def __init__(self):
        self.sent: list[list[dict[str, float]]] = []
        self.stop_count = 0

    def capabilities(self):
        class Capabilities:
            manipulation_ready = True
            blockers: tuple[str, ...] = ()
            skills = ("manipulation.pick", "manipulation.place")

        return Capabilities()

    def execute_action_chunk(self, actions):
        self.sent.append(actions)
        return _Result(True, "OK", "")

    def stop(self, reason):
        self.stop_count += 1


class _Result:
    def __init__(self, success, code, message):
        self.success = success
        self.code = code
        self.message = message


def command(skill: str, parameters: dict | None = None) -> Command:
    return Command(
        schema_version="robot.v1",
        command_id="cmd-1",
        task_id="task-1",
        capability=skill,
        parameters=parameters or {},
        deadline_unix_ms=int(time.time() * 1000) + 10_000,
        lease_ms=5_000,
        idempotency_key="task-1-skill",
        safety_profile="desktop_standard",
        approval_id="approval-1",
    )


def test_direct_backend_executes_provided_action_chunk():
    backend = XLeRobotDirectBackend(FakeDriver())
    result = backend.execute(command("manipulation.pick", {"action_chunk": [{"left_arm_1.pos": 10.0}]}))
    assert result.success
    assert backend.driver.sent == [[{"left_arm_1.pos": 10.0}]]


def test_direct_backend_fails_closed_without_laptop_action_chunk():
    backend = XLeRobotDirectBackend(FakeDriver())
    result = backend.execute(command("manipulation.pick"))
    assert not result.success
    assert result.code == "POLICY_ACTION_CHUNK_REQUIRED"
    assert backend.driver.sent == []


def test_direct_backend_read_only_skills_do_not_require_policy():
    backend = XLeRobotDirectBackend(FakeDriver())
    assert backend.execute(command("observe_scene")).success


def test_direct_backend_requires_explicit_verification():
    backend = XLeRobotDirectBackend(FakeDriver())
    result = backend.execute(command("verify_grasp"))
    assert not result.success
    assert result.code == "VERIFICATION_UNAVAILABLE"


def test_direct_backend_can_be_given_a_verifier():
    backend = XLeRobotDirectBackend(
        FakeDriver(),
        verifier=lambda skill, target, parameters: BackendResult(True, confidence=0.98),
    )
    result = backend.execute(command("verify_grasp"))
    assert result.success
    assert result.confidence == 0.98


def test_direct_backend_observes_entity_provider():
    backend = XLeRobotDirectBackend(
        FakeDriver(),
        entity_provider=lambda: [
            {
                "entity_id": "red-cup",
                "category": "cup",
                "attributes": {"color": "red"},
                "pose_xyz_quat": [0.1, 0.2, 0.3, 1, 0, 0, 0],
                "confidence": 0.95,
                "relation": "",
            }
        ],
    )
    observation = backend.observe(ObservationRequest())
    assert len(observation.entities) == 1
    assert observation.entities[0].entity_id == "red-cup"
    assert observation.entities[0].attributes["color"] == "red"


def test_direct_backend_advertises_structured_capabilities():
    backend = XLeRobotDirectBackend(
        FakeDriver(),
        entity_provider=list,
        verifier=lambda *_: BackendResult(True),
    )
    capabilities = backend.capabilities()
    by_name = {item.name: item for item in capabilities.capabilities}
    assert set(by_name) == {
        "observe_scene",
        "resolve_targets",
        "plan_grasp",
        "manipulation.pick",
        "verify_grasp",
        "manipulation.place",
        "verify_placement",
        "recover_to_safe_pose",
        "emergency_stop",
    }
    assert by_name["observe_scene"].available
    assert by_name["manipulation.pick"].available
    assert by_name["verify_grasp"].available
    assert by_name["manipulation.pick"].safety_level == "physical_motion"


def test_direct_backend_marks_perception_and_verification_unavailable_without_providers():
    backend = XLeRobotDirectBackend(FakeDriver())
    capabilities = backend.capabilities()
    by_name = {item.name: item for item in capabilities.capabilities}
    assert not by_name["observe_scene"].available
    assert "ENTITY_PROVIDER_REQUIRED" in by_name["observe_scene"].blockers
    assert not by_name["verify_grasp"].available
    assert "VERIFIER_REQUIRED" in by_name["verify_grasp"].blockers


def test_direct_backend_rejects_invalid_laptop_chunk_before_driver_call():
    backend = XLeRobotDirectBackend(FakeDriver())
    result = backend.execute(command("manipulation.pick", {"action_chunk": [{"x.vel": 0.1}]}))
    assert not result.success
    assert result.code == "MOBILE_BASE_DISABLED"
    assert backend.driver.sent == []


def test_direct_backend_maps_verifier_exception_to_fail_closed_code():
    def broken_verifier(skill, target, parameters):
        raise RuntimeError("verifier crashed")

    backend = XLeRobotDirectBackend(FakeDriver(), verifier=broken_verifier)
    result = backend.execute(command("verify_grasp"))
    assert not result.success
    assert result.code == "VERIFIER_FAILED"
    assert result.confidence == 0.0


def test_direct_backend_observe_survives_entity_provider_fault():
    def broken_entities():
        raise RuntimeError("camera unavailable")

    backend = XLeRobotDirectBackend(FakeDriver(), entity_provider=broken_entities)
    observation = backend.observe(ObservationRequest())
    assert len(observation.entities) == 0
    assert observation.semantic_state.anomalies == ["ENTITY_PROVIDER_FAILED"]
    assert observation.semantic_state.last_error == "camera unavailable"
