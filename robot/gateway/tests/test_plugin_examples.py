from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from google.protobuf.json_format import ParseDict
from tangying_robot_gateway.runtime import Command, ObservationRequest
from tangying_robot_gateway.service import RobotRuntimeService
from tangying_robot_proto.robot.v1 import robot_pb2

from .test_service import valid_command

ROOT = Path(__file__).resolve().parents[3]


def test_two_structures_share_the_observation_contract_and_simulation_is_explicit():
    from examples.robots.simulated import arm, mobile_sensor

    for backend, model in ((arm(), "example-six-axis-arm"), (mobile_sensor(), "example-mobile-sensor")):
        info = backend.capabilities()
        observed = backend.observe(ObservationRequest())
        assert info.robot_profile["modelId"] == model
        assert observed.reconstruction["sourceType"] == "sim_ground_truth"
        assert observed.semantic_state.mode == "SIMULATION"
        assert observed.reconstruction["frameId"] == "world"
        assert observed.reconstruction["units"] == "m"


def test_arm_example_verifies_actual_simulated_state_instead_of_placeholder_success():
    from examples.robots.simulated import arm

    backend = arm()
    verify = Command("robot.v1", "verify-1", "task-1", "verify_grasp", parameters={"objectId": "red-block"})
    assert not backend.execute(verify).success
    pick = Command("robot.v1", "pick-1", "task-1", "manipulation.pick", target_ref="red-block",
                   parameters={"action_chunk": [{"axis1.position": 0.5}]})
    assert backend.execute(pick).success
    assert backend.execute(verify).success
    place = Command("robot.v1", "place-1", "task-1", "manipulation.place", target_ref="tray",
                    parameters={"action_chunk": [{"axis1.position": 0.8}]})
    assert backend.execute(place).success
    final = backend.execute(Command("robot.v1", "verify-2", "task-1", "verify_placement",
                                    parameters={"objectId": "red-block", "destinationId": "tray"}))
    assert final.success
    assert not backend.execute(verify).success
    observed = backend.observe(ObservationRequest())
    assert observed.entities[0].relation == "inside:tray"


def test_mobile_example_rejects_unbounded_goal_and_updates_its_declared_state():
    from examples.robots.simulated import mobile_sensor

    backend = mobile_sensor()
    unsafe = Command("robot.v1", "nav-1", "task-1", "navigation.navigate",
                     parameters={"goalPose": [100, 0, 0, 1, 0, 0, 0]})
    assert not backend.execute(unsafe).success
    safe = Command("robot.v1", "nav-2", "task-1", "navigation.navigate",
                   parameters={"goalPose": [1, 1, 0, 1, 0, 0, 0]})
    assert backend.execute(safe).success
    assert backend.observe(ObservationRequest()).robot_state["base_pose"] == [1, 1, 0, 1, 0, 0, 0]


def test_plugin_cli_checks_examples_and_reports_actionable_schema_errors(tmp_path):
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "robot/gateway"), str(ROOT / "python"), str(ROOT)]))
    command = [sys.executable, "-m", "tangying_robot_gateway.run_plugin", "check", "--factory", "examples.robots.simulated:arm"]
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["valid"] is True
    assert report["robotId"] == "example-arm-1"
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schemaVersion":"wrong"}')
    result = subprocess.run(command[:-2] + ["--profile", str(invalid)], cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert json.loads(result.stdout)["valid"] is False


def test_all_seven_existing_agent_steps_with_policy_evidence_execute_and_verify():
    from examples.robots.simulated import arm

    backend = arm()
    service = RobotRuntimeService(backend)
    info = service.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), None)
    policy = {
        "policyId": "simulation-handoff", "policyVersion": "1.0.0",
        "framework": "deterministic", "artifactSha256": "deterministic:simulation-handoff-v1",
        "manifestRevision": "a" * 64, "inferenceId": "infer-1",
        "observationId": "scene-1", "elapsedMs": 0.0,
    }
    steps = [
        ("observe_scene", "", {}),
        ("resolve_targets", "red-block", {"objectId": "red-block", "destinationId": "tray", "objectConfidence": 1.0, "destinationConfidence": 1.0}),
        ("plan_grasp", "red-block", {"objectId": "red-block", "destinationId": "tray", "keepUpright": True}),
        ("manipulation.pick", "red-block", {"targetRef": "red-block", "keepUpright": True, "action_chunk": [{"axis1.position": 0.5}], "policy_execution": policy}),
        ("verify_grasp", "red-block", {"objectId": "red-block"}),
        ("manipulation.place", "tray", {"targetRef": "tray", "keepUpright": True, "action_chunk": [{"axis1.position": 0.8}], "policy_execution": policy}),
        ("verify_placement", "tray", {"objectId": "red-block", "destinationId": "tray"}),
    ]
    for index, (name, target, parameters) in enumerate(steps):
        command = valid_command()
        command.command_id = command.idempotency_key = f"step-{index}"
        command.skill, command.target_ref = name, target
        command.robot_id, command.catalog_revision = info.robot_id, info.catalog_revision
        ParseDict(parameters, command.parameters)
        final = list(service.execute_for_test(command))[-1]
        assert final.type == robot_pb2.SKILL_EVENT_SUCCEEDED, f"{name}: {final.code}"
    final_scene = backend.observe(ObservationRequest())
    assert final_scene.entities[0].relation == "inside:tray"
    assert final_scene.robot_state["held"] == ""
