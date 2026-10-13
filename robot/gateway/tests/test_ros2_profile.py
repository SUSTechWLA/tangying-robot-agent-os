from __future__ import annotations

import json
import time

import pytest
from tangying_robot_gateway.ros2_profile import Ros2ProfileBridge
from tangying_robot_gateway.runtime import Command

from .test_mobility_contract import profile


class Transport:
    def ready(self):
        return True

    def execute(self, goal, tick):
        self.goal = goal
        tick()
        publish_observation(self.bridge, sequence=2)
        return {"robot_id": goal["robot_id"], "profile_revision": goal["profile_revision"],
                "command_id": goal["command_id"], "success": True, "code": "OK",
                "message": "", "observation_id": "ros-2", "verification_confidence": 1.0}

    def stop(self, request):
        self.stop_request = request
        return {**request, "accepted": True}

    def close(self):
        pass


def driver_state(bridge, **updates):
    mobility = bridge.profile.mobility
    value = {"robotId": bridge.profile.robot_id, "profileRevision": bridge.revision,
             "commissioningId": "commission-1", "sequence": 1, "observedAtUnixMs": int(time.time() * 1000),
             "commissioned": True, "armed": True, "healthy": True, "watchdogActive": True,
             "stopProcedureId": mobility.safe_stop.procedure_id,
             "commandLossTimeoutMs": mobility.safe_stop.command_loss_timeout_ms,
             "maxLinearSpeedMps": mobility.max_linear_speed_mps,
             "maxAngularSpeedRadps": mobility.max_angular_speed_radps,
             "supportedTools": [t for t in bridge.profile.tools if t not in {"observe_scene", "emergency_stop"}],
             "faults": []}
    return {**value, **updates}


def publish_observation(bridge, *, sequence=1, observed_ms=None):
    sensor = bridge.profile.sensors[0]
    value = {"schemaVersion": "scene.reconstruction.v1", "robotId": bridge.profile.robot_id,
             "sourceId": sensor.source_id, "sourceType": sensor.source_type, "sourceFrameId": sensor.frame_id,
             "frameId": "world", "transformRevision": sensor.transform_revision,
             "observationId": f"ros-{sequence}", "sequence": sequence, "units": "m",
             "observedAtUnixMs": observed_ms or int(time.time() * 1000), "entities": [], "points": []}
    bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(value))


def make_bridge(kind="ugv"):
    transport = Transport()
    bridge = Ros2ProfileBridge(profile(kind), transport, commissioning_id="commission-1")
    transport.bridge = bridge
    return bridge, transport


def command(bridge):
    return Command("robot.v1", "command-1", "task-1", "navigation.navigate",
                   parameters={"goalPose": [0, 0, 0, 1, 0, 0, 0]}, lease_ms=250,
                   deadline_unix_ms=int(time.time() * 1000) + 2000,
                   idempotency_key="idem-1", approval_id="approval-1", robot_id=bridge.profile.robot_id,
                   catalog_revision="catalog-1", world_revision_basis=10, resource_id="corridor-1", fencing_token=7)


@pytest.mark.parametrize("kind", ["ugv", "uav", "legged"])
def test_ros2_profile_requires_fresh_state_and_scene_then_preserves_command_binding(kind):
    bridge, transport = make_bridge(kind)
    assert not bridge.ready()
    bridge.receive_state(driver_state(bridge))
    assert not bridge.ready()
    publish_observation(bridge)
    assert bridge.ready()
    value = command(bridge)
    result = bridge.execute(value)
    assert result.success and result.observation_id == "ros-2"
    for field in ["command_id", "task_id", "catalog_revision", "deadline_unix_ms", "lease_ms",
                  "idempotency_key", "approval_id", "world_revision_basis", "resource_id", "fencing_token"]:
        assert transport.goal[field] == getattr(value, field)
    assert transport.goal["profile_revision"] == bridge.revision
    bridge.stop("TEST_LINK_LOST")
    assert not bridge.ready()
    assert transport.stop_request["procedure_id"] == bridge.profile.mobility.safe_stop.procedure_id
    # A fresh ready message cannot remotely clear a local stop latch.
    bridge.receive_state(driver_state(bridge, sequence=2))
    assert not bridge.ready()


@pytest.mark.parametrize("updates", [
    {"robotId": "foreign"}, {"profileRevision": "wrong"}, {"commissioningId": "foreign"},
    {"sequence": 0}, {"observedAtUnixMs": 1}, {"observedAtUnixMs": 9_000_000_000_000},
    {"stopProcedureId": "other"}, {"commandLossTimeoutMs": 501}, {"maxLinearSpeedMps": 0.3},
    {"maxAngularSpeedRadps": 0.5}, {"supportedTools": []}, {"armed": "true"},
])
def test_invalid_ros2_state_revokes_previous_readiness(updates):
    bridge, _ = make_bridge()
    bridge.receive_state(driver_state(bridge))
    publish_observation(bridge)
    assert bridge.ready()
    with pytest.raises(ValueError):
        bridge.receive_state(driver_state(bridge, **{"sequence": 2, **updates}))
    assert not bridge.ready()


@pytest.mark.parametrize("updates", [
    {"armed": False}, {"commissioned": False}, {"healthy": False}, {"watchdogActive": False}, {"faults": ["LOW_BATTERY"]},
])
def test_explicit_controller_status_cannot_enable_motion(updates):
    bridge, _ = make_bridge()
    bridge.receive_state(driver_state(bridge, **updates))
    publish_observation(bridge)
    assert not bridge.ready()


def test_repeat_sequence_cannot_refresh_the_controller_lease():
    bridge, _ = make_bridge()
    value = driver_state(bridge)
    bridge.receive_state(value)
    with pytest.raises(ValueError, match="sequence"):
        bridge.receive_state(value)
    assert not bridge.ready()


def test_stale_state_or_observation_blocks_before_transport_execution(monkeypatch):
    bridge, transport = make_bridge()
    bridge.receive_state(driver_state(bridge))
    publish_observation(bridge)
    bridge._state_received -= 1
    assert not bridge.execute(command(bridge)).success
    assert bridge.state()["lastReadinessFailure"]["reason"] == "DRIVER_STATE_EXPIRED"
    assert not hasattr(transport, "goal")
    bridge.receive_state(driver_state(bridge, sequence=2))
    bridge._observation_received -= 1
    assert not bridge.ready()


def test_controller_loss_during_motion_propagates_unknown_instead_of_false_success():
    bridge, transport = make_bridge()
    bridge.receive_state(driver_state(bridge))
    publish_observation(bridge)
    def execute(goal, tick):
        bridge.receive_state(driver_state(bridge, sequence=2, healthy=False))
        tick()
    transport.execute = execute
    with pytest.raises(RuntimeError, match="readiness"):
        bridge.execute(command(bridge))


def test_success_without_post_command_capture_is_not_accepted():
    bridge, transport = make_bridge()
    bridge.receive_state(driver_state(bridge))
    publish_observation(bridge, observed_ms=int(time.time() * 1000) - 100)
    transport.execute = lambda goal, tick: {"robot_id": goal["robot_id"], "profile_revision": bridge.revision,
        "command_id": goal["command_id"], "success": True, "code": "OK", "message": "",
        "observation_id": "ros-1", "verification_confidence": 1.0}
    with pytest.raises((ValueError, RuntimeError)):
        bridge.execute(command(bridge))


def test_wrong_stop_acknowledgement_never_clears_local_latch():
    bridge, transport = make_bridge("uav")
    transport.stop = lambda request: {**request, "accepted": True, "procedure_id": "ground-brake"}
    with pytest.raises(RuntimeError, match="safe-stop"):
        bridge.stop("TEST")
    assert not bridge.ready()


def test_same_millisecond_cached_capture_is_not_new_execution_evidence(monkeypatch):
    bridge, transport = make_bridge()
    now = time.time()
    monkeypatch.setattr('tangying_robot_gateway.ros2_profile.time.time', lambda: now)
    bridge.receive_state(driver_state(bridge))
    publish_observation(bridge, observed_ms=int(now * 1000))
    transport.execute = lambda goal, tick: {"robot_id": goal["robot_id"], "profile_revision": bridge.revision,
        "command_id": goal["command_id"], "success": True, "code": "OK", "message": "",
        "observation_id": "ros-1", "verification_confidence": 1.0}
    with pytest.raises((ValueError, RuntimeError)):
        bridge.execute(command(bridge))


def test_navigation_preparation_must_match_registered_tools():
    from tangying_robot_gateway.contracts import RobotProfile
    for kind in ['ugv', 'uav']:
        value = profile(kind)
        value['mobility']['navigationPreparation'] = 'agent_pre_position'
        with pytest.raises(ValueError):
            RobotProfile.model_validate(value)
    value = profile('ugv')
    value['mobility']['navigationPreparation'] = 'agent_pre_position'
    value['tools'].append('navigation.pre_position')
    assert RobotProfile.model_validate(value).mobility.navigation_preparation == 'agent_pre_position'


def semantic_state(bridge, marker="map-1"):
    active = {"mapId": marker, "mapRevision": "a" * 64,
              "calibrationRevision": bridge.profile.sensors[0].transform_revision}
    return {"base_pose": [0, 0, 0, 1, 0, 0, 0], "active_map": active,
            "semantic_navigation": {"schemaVersion": "semantic.navigation.v1", "robotId": bridge.profile.robot_id,
                "frameId": "world", **active, "goals": {"dock": [0, 0, 0, 1, 0, 0, 0]},
                "aliases": {"充电站": "dock"}, "routeEdges": {"dock": []}}}


def test_reconstruction_and_semantic_state_are_paired_despite_concurrent_capture():
    bridge, _ = make_bridge()
    publish_observation(bridge)
    first = bridge.observe()
    # Use a new identity: state cannot be attached to an already published ID.
    first.update(sequence=2, observationId="ros-2")
    state_a = semantic_state(bridge)
    bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(first), json.dumps(state_a))
    bridge.observe()
    second = {**first, "sequence": 3, "observationId": "ros-3"}
    state_b = semantic_state(bridge, "map-2")
    bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(second), json.dumps(state_b))
    assert bridge.state()["active_map"] == state_a["active_map"]
    bridge.observe()
    assert bridge.state()["active_map"] == state_b["active_map"]
    with pytest.raises(ValueError, match="reused"):
        bridge.receive_observation(bridge.profile.robot_id, bridge.revision, json.dumps(second), json.dumps(state_a))


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(approval=True),
    lambda s: s["active_map"].update(calibrationRevision="foreign"),
    lambda s: s["semantic_navigation"].update(robotId="foreign"),
    lambda s: s["semantic_navigation"]["goals"].update(dock=[10, 0, 0, 1, 0, 0, 0]),
    lambda s: s["semantic_navigation"]["routeEdges"].update(dock=["unknown"]),
    lambda s: s["semantic_navigation"]["aliases"].update(unsafe="unknown"),
    lambda s: s.update(base_pose=[True, 0, 0, 1, 0, 0, 0]),
])
def test_ros2_semantic_state_cannot_smuggle_unknown_identity_or_unbounded_goals(mutate):
    from tangying_robot_gateway.ros2_profile import navigation_state
    bridge, _ = make_bridge()
    state = semantic_state(bridge)
    mutate(state)
    with pytest.raises(ValueError):
        navigation_state(json.dumps(state), bridge.profile)


def test_ros2_runtime_embeds_exact_post_completion_capture_for_go_evidence():
    import threading

    from tangying_robot_gateway.service import RobotRuntimeService
    from tangying_robot_proto.robot.v1 import robot_pb2

    from .test_mobility_contract import nav_command

    bridge, transport = make_bridge()
    bridge.receive_state(driver_state(bridge))
    publish_observation(bridge)
    original_execute = transport.execute
    timers = []
    def execute(goal, tick):
        result = original_execute(goal, tick)
        timer = threading.Timer(.03, lambda: publish_observation(bridge, sequence=3))
        timers.append(timer)
        timer.start()
        return result
    transport.execute = execute
    service = RobotRuntimeService(bridge.backend())
    try:
        terminal = list(service.execute_for_test(nav_command(service.GetRuntimeInfo(None, None))))[-1]
    finally:
        for timer in timers:
            timer.join()
    assert terminal.type == robot_pb2.SKILL_EVENT_SUCCEEDED
    assert terminal.observation_id == terminal.evidence_observation.observation_id == "ros-3"
    assert terminal.evidence_observation.reconstruction["sequence"] == 3
