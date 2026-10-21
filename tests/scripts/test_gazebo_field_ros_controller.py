"""Gazebo driver contracts; no simulated measurement here counts as physics evidence."""
from __future__ import annotations

import copy
import json
import math
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from tangying_robot_gateway.contracts import RobotProfile, validate_reconstruction
from tangying_robot_gateway.ros2_profile import navigation_state

from sim.gazebo import field_ros_controller as driver

ROOT = Path(__file__).resolve().parents[2]


def snapshot(**updates):
    result = {"sequence": 1, "capturedAtUnixMs": 100_000,
              "states": {"ground-a": {"position": [.5, -.3, 0.], "linearVelocity": [.1, 0., 0.],
                  "quaternionWXYZ": [1., 0., 0., 0.], "simulationTime": 3.2}}}
    result.update(updates)
    return result


def profile():
    value = json.loads((ROOT / "examples/robots/ugv.commissioning.profile.json").read_text())
    value["sensors"][0].update(sourceId="ground-a/sim-state", transformRevision="gazebo-ground-truth-v1")
    return RobotProfile.model_validate(value)


def goals():
    return {"mapId": "gazebo-field", "mapRevision": "d" * 64,
            "goals": {"living_room": [0., 0., 0., 1., 0., 0., 0.],
                      "kitchen": [.5, 0., 0., 1., 0., 0., 0.],
                      "bedroom": [.5, .5, 0., 1., 0., 0., 0.]},
            "aliases": {"客厅": "living_room", "厨房": "kitchen", "卧室": "bedroom"},
            "routeEdges": {"living_room": ["kitchen"], "kitchen": ["living_room", "bedroom"],
                           "bedroom": ["kitchen"]}}


def test_same_snapshot_keeps_capture_timestamp_and_identity():
    cache = driver.MeasuredSnapshot("ground-a")
    one, fresh = cache.accept(snapshot(), now_ms=100_001, max_age_ms=500)
    two, repeated = cache.accept(snapshot(), now_ms=100_100, max_age_ms=500)
    assert fresh and not repeated and one is two
    assert two["capturedAtUnixMs"] == 100_000 and two["simulationTime"] == 3.2
    with pytest.raises(ValueError, match="stale"):
        cache.accept(snapshot(), now_ms=100_501, max_age_ms=500)


@pytest.mark.parametrize("changes", [
    {"sequence": 0}, {"sequence": True}, {"capturedAtUnixMs": 99_000},
    {"capturedAtUnixMs": 101_000}, {"states": {}},
])
def test_invalid_snapshot_is_not_accepted(changes):
    with pytest.raises(ValueError):
        driver.MeasuredSnapshot("ground-a").accept(snapshot(**changes), now_ms=100_000, max_age_ms=500)


@pytest.mark.parametrize("field,value", [
    ("position", [math.nan, 0., 0.]), ("linearVelocity", [0., 0.]),
    ("quaternionWXYZ", [0., 0., 0., 0.]), ("simulationTime", -1),
])
def test_invalid_physical_measurement_is_not_reconstructed(field, value):
    value_snapshot = snapshot()
    value_snapshot["states"]["ground-a"][field] = value
    with pytest.raises(ValueError):
        driver.MeasuredSnapshot("ground-a").accept(value_snapshot, now_ms=100_000, max_age_ms=500)


@pytest.mark.parametrize("mutation", ["sequence_regressed", "clock_regressed", "reused_identity", "paused_physics"])
def test_regression_or_retimestamping_does_not_create_fresh_evidence(mutation):
    cache = driver.MeasuredSnapshot("ground-a")
    cache.accept(snapshot(sequence=2), now_ms=100_000, max_age_ms=500)
    later = snapshot(sequence=3, capturedAtUnixMs=100_010)
    later["states"]["ground-a"]["simulationTime"] = 3.3
    if mutation == "sequence_regressed":
        later["sequence"] = 1
    elif mutation == "clock_regressed":
        later["states"]["ground-a"]["simulationTime"] = 3.1
    elif mutation == "reused_identity":
        later["sequence"] = 2
    else:
        later["states"]["ground-a"]["simulationTime"] = 3.2
    with pytest.raises(ValueError):
        cache.accept(later, now_ms=100_020, max_age_ms=500)
    assert cache.sample["sequence"] == 2


def test_measured_yaw_conversion_and_no_hidden_orientation_default():
    value = snapshot()
    del value["states"]["ground-a"]["quaternionWXYZ"]
    with pytest.raises(ValueError, match="yaw"):
        driver.MeasuredSnapshot("ground-a").accept(value, now_ms=100_000, max_age_ms=500)
    value["states"]["ground-a"]["yaw"] = math.pi
    actual, _ = driver.MeasuredSnapshot("ground-a").accept(value, now_ms=100_000, max_age_ms=500)
    assert actual["pose"][3:] == pytest.approx([0., 0., 0., 1.])


def test_navigation_state_matches_existing_gateway_strict_contract():
    robot = profile()
    named = driver.load_goals(goals(), robot)
    measured, _ = driver.MeasuredSnapshot("ground-a").accept(snapshot(), now_ms=100_000, max_age_ms=500)
    state = driver.navigation_state_for_sample(robot, named, measured)
    assert navigation_state(json.dumps(state), robot) == state
    assert state["base_pose"][:3] == [.5, -.3, 0.]
    assert set(state) == {"base_pose", "base_pose_frame", "active_map", "semantic_navigation"}
    assert "simulationTime" not in state


@pytest.mark.parametrize("mutation", ["unknown_alias", "missing_route_node", "bad_goals", "bad_tolerance"])
def test_malformed_named_goals_fail_before_ros_motion(mutation):
    document = goals()
    if mutation == "unknown_alias":
        document["aliases"]["new"] = "unknown"
    elif mutation == "missing_route_node":
        document["routeEdges"].pop("kitchen")
    elif mutation == "bad_goals":
        document["goals"]["kitchen"][0] = 1e5
    else:
        document["arrivalToleranceM"] = 10
    with pytest.raises(ValueError):
        driver.load_goals(document, profile())


@pytest.mark.parametrize("url", ["https://127.0.0.1:1234", "http://localhost:1234", "http://127.0.0.1",
                                "http://127.0.0.1:1234/path", "http://u:p@127.0.0.1:1234"])
def test_hub_client_is_local_and_not_redirectable(url):
    with pytest.raises(ValueError):
        driver.HubClient(url)


@pytest.fixture
def node(monkeypatch):
    class Message(SimpleNamespace):
        pass

    class Publisher:
        def __init__(self):
            self.messages = []

        def publish(self, message):
            self.messages.append(message)

    class Node:
        def __init__(self, *args, **kwargs):
            pass

        def create_publisher(self, *args):
            return Publisher()

        def create_subscription(self, *args, **kwargs):
            return None

        def create_service(self, *args, **kwargs):
            return None

        def create_timer(self, *args, **kwargs):
            return None

    for name, attributes in {
        "rclpy.action": {"ActionServer": lambda *a, **k: None,
                         "CancelResponse": SimpleNamespace(ACCEPT="accept"),
                         "GoalResponse": SimpleNamespace(ACCEPT="accept", REJECT="reject")},
        "rclpy.callback_groups": {"ReentrantCallbackGroup": object},
        "rclpy.node": {"Node": Node},
        "tangying_robot_msgs.action": {"ProfiledExecute": SimpleNamespace(Result=Message)},
        "tangying_robot_msgs.msg": {"CommandLease": Message, "ProfiledDriverState": Message,
                                    "ProfiledObservation": Message},
        "tangying_robot_msgs.srv": {"EnterSafeStop": Message},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    clock = {"wall": 100., "monotonic": 50.}
    monkeypatch.setattr(driver.time, "time", lambda: clock["wall"])
    monkeypatch.setattr(driver.time, "monotonic", lambda: clock["monotonic"])
    instance = driver.controller_class()(profile().to_wire(), "commission-1", robot_id="ground-a",
        hub_url="http://127.0.0.1:18118", goals=goals())
    instance.calls = []
    instance.clock = clock

    def request(route, payload=None):
        instance.calls.append((route, copy.deepcopy(payload)))
        return snapshot() if route == "/snapshot" else {"ok": True, "commandId": payload.get("commandId")}

    instance.hub.request = request
    instance._capture()
    return instance


def goal(node, **updates):
    values = {"schema_version": "ros2.execution.v1", "robot_id": node.profile.robot_id,
        "adapter_id": node.profile.adapter_id, "profile_revision": node.revision, "commissioning_id": "commission-1",
        "command_id": "command-1", "idempotency_key": "idempotency-1", "catalog_revision": "catalog-1", "task_id": "task-1",
        "deadline_unix_ms": 101_000, "lease_ms": 500, "capability": "navigation.navigate", "approval_id": "approval-1",
        "navigation_plan_json": "", "parameters_json": json.dumps({"goalPose": [.5, 0., 0., 1., 0., 0., 0.]}), "target_ref": ""}
    return SimpleNamespace(**{**values, **updates})


def lease(node, **updates):
    values = {"robot_id": node.profile.robot_id, "profile_revision": node.revision, "command_id": "command-1",
                  "observed_at_unix_ms": 100_000, "deadline_unix_ms": 101_000, "lease_ms": 500}
    return SimpleNamespace(**{**values, **updates})


def test_real_gateway_accepts_declared_sim_state_capture_and_does_not_retimestamp(node):
    message = node._obs.messages[-1]
    observed = validate_reconstruction(json.loads(message.reconstruction_json), node.profile, now_ms=100_000)
    assert observed.entities[0].pose[:3] == [.5, -.3, 0.]
    assert observed.entities[0].attributes["source"] == "gazebo_ground_truth_link_state"
    assert observed.points == []
    navigation_state(message.robot_state_json, node.profile)
    old_id, _ = node._capture()
    assert old_id == observed.observation_id and len(node._obs.messages) == 1


def test_single_active_goal_and_replay_are_rejected(node):
    assert node._goal(goal(node)) == "accept"
    assert node._goal(goal(node, command_id="command-2", idempotency_key="key-2")) == "reject"
    node._active = None
    assert node._goal(goal(node)) == "reject"


def test_wrong_or_replayed_leases_never_forward_to_hub(node):
    assert node._goal(goal(node)) == "accept"
    node._dispatched = True
    node._lease_ack_expires = node.clock["monotonic"] + .5
    node._lease(lease(node, command_id="other"))
    node._lease(lease(node, deadline_unix_ms=102_000))
    node._lease(lease(node, observed_at_unix_ms=99_000))
    assert not node._leases and all(route != "/lease" for route, _ in node.calls)
    node._lease(lease(node))
    assert [route for route, _ in node.calls].count("/lease") == 1
    node._lease(lease(node))
    assert [route for route, _ in node.calls].count("/lease") == 1


def test_forwarding_lease_preserves_remaining_lifetime_not_new_full_window(node):
    assert node._goal(goal(node)) == "accept"
    node._lease(lease(node, observed_at_unix_ms=99_800))
    assert node._hub_lease(node._active)["leaseMs"] == 300
    assert node._hub_lease(node._active)["observedAtUnixMs"] == 99_800
    assert node._lease_valid(node._active)


def test_lost_goal_response_becomes_unknown_and_latches_stop(node):
    command = goal(node)
    assert node._goal(command) == "accept"
    node._lease(lease(node))
    original = node.hub.request

    def lose_response(route, payload=None):
        if route == "/goal":
            raise TimeoutError("ambiguous goal dispatch")
        return original(route, payload)

    node.hub.request = lose_response
    handle = SimpleNamespace(request=command, is_cancel_requested=False, abort=lambda: None)
    result = node._execute(handle)
    assert result.code == "EXECUTION_OUTCOME_UNKNOWN" and not result.success
    assert node._stopped and node._active is None
    assert any(route == "/stop" for route, _ in node.calls)
    assert "idempotency-1" in node._executed


def stop_request(node, **updates):
    return SimpleNamespace(**{"robot_id": node.profile.robot_id, "profile_revision": node.revision,
        "commissioning_id": node.commissioning_id, "procedure_id": node.profile.mobility.safe_stop.procedure_id,
        "command_id": "command-1", "reason": "test controlled stop", **updates})


def test_invalid_stop_identity_does_not_enter_procedure(node):
    response = node._stop(stop_request(node, profile_revision="different"), SimpleNamespace())
    assert not response.accepted and not node._stopped
    assert all(route != "/stop" for route, _ in node.calls)


def test_stop_acknowledges_procedure_not_physical_rest_and_blocks_new_motion(node):
    response = node._stop(stop_request(node), SimpleNamespace())
    assert response.accepted and node._stopped
    assert "physicalStopVerified=false" in response.message
    assert node._goal(goal(node)) == "reject"
    assert node.calls[-1] == ("/stop", {"robotId": "ground-a", "commandId": "command-1"})


def test_failed_hub_stop_is_not_acknowledged(node):
    def unavailable(*args):
        raise TimeoutError("hub unavailable")

    node.hub.request = unavailable
    response = node._stop(stop_request(node), SimpleNamespace())
    assert not response.accepted and node._stopped
    assert "hub unavailable" in response.message


def test_cancelled_before_dispatch_never_sends_goal_even_with_live_lease(node):
    command = goal(node)
    assert node._goal(command) == "accept"
    node._lease(lease(node))
    canceled = []
    handle = SimpleNamespace(request=command, is_cancel_requested=True, canceled=lambda: canceled.append(True))
    result = node._execute(handle)
    assert result.code == "CANCELLED" and canceled == [True]
    assert all(route != "/goal" for route, _ in node.calls)
    assert not node._executed


def test_expired_lease_in_flight_enters_cancel_for_same_command(node):
    command = goal(node)
    assert node._goal(command) == "accept"
    node._lease(lease(node))
    checks = iter([True, True, False])
    node._lease_valid = lambda _: next(checks)
    aborted = []
    handle = SimpleNamespace(request=command, is_cancel_requested=False, abort=lambda: aborted.append(True))
    result = node._execute(handle)
    assert result.code == "LEASE_EXPIRED_OR_STOPPED" and aborted == [True]
    assert node.calls[-1] == ("/cancel", {"robotId": "ground-a", "commandId": "command-1"})
    assert node._active is None and not node._leases


def test_hub_watchdog_block_cannot_turn_into_success_or_wait_until_task_deadline(node):
    command = goal(node)
    assert node._goal(command) == "accept"
    node._lease(lease(node))
    original = node.hub.request

    def blocked(route, payload=None):
        if route.startswith("/status"):
            return {"commandId": "command-1", "arrived": True, "blockedReason": "lease_expired"}
        return original(route, payload)

    node.hub.request = blocked
    handle = SimpleNamespace(request=command, is_cancel_requested=False, abort=lambda: None)
    result = node._execute(handle)
    assert result.code == "EXECUTION_OUTCOME_UNKNOWN" and not result.success
    assert node._stopped and any(route == "/stop" for route, _ in node.calls)


@pytest.mark.parametrize("updates", [
    {"deadline_unix_ms": 99_000}, {"approval_id": ""}, {"profile_revision": "wrong"},
    {"robot_id": "other"}, {"navigation_plan_json": "{}"}, {"lease_ms": 0},
])
def test_goal_invalid_identity_or_authority_is_rejected_without_hub_motion(node, updates):
    assert node._goal(goal(node, **updates)) == "reject"
    assert node._active is None and all(route != "/goal" for route, _ in node.calls)


def test_transient_read_timeout_reuses_only_still_fresh_original_capture(node):
    original = node._measured.sample
    old_id = node._observation
    published = len(node._obs.messages)
    node.clock["monotonic"] += .051

    def unavailable(*args):
        raise TimeoutError("one read timed out")

    node.hub.request = unavailable
    observation_id, sample = node._capture()
    assert observation_id == old_id and sample is original
    assert sample["capturedAtUnixMs"] == 100_000 and sample["sequence"] == 1
    assert len(node._obs.messages) == published
    node._publish()
    assert node._state.messages[-1].healthy
    assert not node._state.messages[-1].faults
    assert len(node._obs.messages) == published


def test_transport_fallback_never_extends_expired_measurement(node, monkeypatch):
    monkeypatch.setattr(driver.time, "time", lambda: 100.501)

    def unavailable(*args):
        raise TimeoutError("source remains unavailable")

    node.hub.request = unavailable
    with pytest.raises(TimeoutError):
        node._capture()
    node._publish()
    assert not node._state.messages[-1].healthy
    assert node._state.messages[-1].faults == ["gazebo_measurement_unavailable"]
    assert node._measured.sample["capturedAtUnixMs"] == 100_000


def test_read_http_rejection_is_not_treated_as_transient_cached_success(node):
    node.clock["monotonic"] += .051
    def rejected(*args):
        raise driver.urllib.error.HTTPError("http://127.0.0.1/snapshot", 400, "hub failure", {}, None)

    node.hub.request = rejected
    with pytest.raises(driver.urllib.error.HTTPError):
        node._capture()


def test_status_timeout_retries_without_claiming_arrival_and_cancel_still_works(node, monkeypatch):
    command = goal(node)
    assert node._goal(command) == "accept"
    node._lease(lease(node))
    original = node.hub.request
    status_calls = []
    canceled = []
    handle = SimpleNamespace(request=command, is_cancel_requested=False, canceled=lambda: canceled.append(True))

    def timed_out(route, payload=None):
        if route.startswith("/status"):
            status_calls.append(route)
            raise TimeoutError("status transport gap")
        return original(route, payload)

    def next_tick(_):
        handle.is_cancel_requested = True

    node.hub.request = timed_out
    monkeypatch.setattr(driver.time, "sleep", next_tick)
    result = node._execute(handle)
    assert len(status_calls) == 1 and result.code == "CANCELLED" and not result.success
    assert canceled == [True] and not node._stopped
    assert node.calls[-1] == ("/cancel", {"robotId": "ground-a", "commandId": "command-1"})


def admit_dispatched(node):
    command = goal(node)
    assert node._goal(command) == "accept"
    node._lease(lease(node))
    node._acknowledge_lease(command, node._hub_lease(command), node.clock["monotonic"])
    node._dispatched = True
    return command


def test_timed_out_renewal_preserves_only_previous_confirmed_window(node, capsys):
    command = admit_dispatched(node)
    confirmed_expiry = node._lease_ack_expires
    original = node.hub.request

    def timeout(route, payload=None):
        if route == "/lease":
            raise TimeoutError("renewal response lost")
        return original(route, payload)

    node.clock.update(wall=100.1, monotonic=50.1)
    node.hub.request = timeout
    node._lease(lease(node, observed_at_unix_ms=100_100))
    assert node._lease_ack_expires == confirmed_expiry
    assert node._lease_valid(command) and not node._lease_fault
    node._publish()
    assert node._state.messages[-1].healthy
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    retry = next(event for event in events if event["type"] == "LEASE_FORWARD_RETRY")
    assert not retry["leaseExtended"] and retry["confirmedRemainingMs"] == pytest.approx(400)

    # A new DDS lease, not a replay of the motion command, can renew while the
    # last acknowledged window is still valid.
    node.clock.update(wall=100.2, monotonic=50.2)
    node.hub.request = original
    node._lease(lease(node, observed_at_unix_ms=100_200))
    assert node._lease_ack_expires == pytest.approx(50.7)
    assert node._lease_valid(command)
    assert all(route != "/goal" for route, _ in node.calls)


def test_unconfirmed_renewal_cannot_revive_after_previous_ack_expires(node):
    command = admit_dispatched(node)
    node.clock.update(wall=100.2, monotonic=50.501)
    # Deliberately do not call publish/lease_valid before the new DDS callback.
    # Expiration must be enforced independently of timer scheduling.
    node._lease(lease(node, observed_at_unix_ms=100_200))
    assert not node._lease_valid(command)
    assert node._lease_fault == "hub_confirmed_lease_expired"
    assert all(route != "/lease" for route, _ in node.calls)
    node._publish()
    assert not node._state.messages[-1].healthy
    assert "hub_confirmed_lease_expired" in node._state.messages[-1].faults


def test_renewal_ack_arriving_after_prior_window_does_not_resurrect(node):
    command = admit_dispatched(node)
    node.clock.update(wall=100.1, monotonic=50.1)
    payload = node._hub_lease(command)
    node.clock["monotonic"] = 50.501
    with pytest.raises(ValueError, match="cannot be revived"):
        node._acknowledge_lease(command, payload, 50.1)
    assert node._lease_fault == "hub_confirmed_lease_expired"
    assert node._lease_ack_expires == 50.5


def test_acknowledgement_never_extends_by_response_latency(node):
    command = goal(node)
    assert node._goal(command) == "accept"
    node._lease(lease(node, observed_at_unix_ms=99_800))
    payload = node._hub_lease(command)
    node.clock["monotonic"] = 50.1
    node._acknowledge_lease(command, payload, 50.)
    assert node._lease_ack_expires == pytest.approx(50.3)


def test_expired_initial_ack_is_uncertain_not_a_fresh_lease(node):
    command = goal(node)
    assert node._goal(command) == "accept"
    node._lease(lease(node))
    node.clock["monotonic"] = 50.501
    with pytest.raises(ValueError, match="after its bounded window"):
        node._acknowledge_lease(command, {"leaseMs": 500}, 50.)
    assert node._lease_fault == "hub_confirmed_lease_expired"


def test_explicit_lease_rejection_fails_closed_and_new_capture_cannot_hide_it(node):
    command = admit_dispatched(node)
    node.clock.update(wall=100.1, monotonic=50.1)
    newer = snapshot(sequence=2, capturedAtUnixMs=100_100)
    newer["states"]["ground-a"]["simulationTime"] = 3.3

    def rejected(route, payload=None):
        if route == "/lease":
            raise driver.urllib.error.HTTPError("http://127.0.0.1/lease", 400, "rejected", {}, None)
        return newer

    node.hub.request = rejected
    node._lease(lease(node, observed_at_unix_ms=100_100))
    assert node._lease_fault == "hub_lease_rejected" and not node._lease_valid(command)
    node._fault = "gazebo_measurement_unavailable"
    node._publish()
    assert node._measured.sample["sequence"] == 2 and not node._fault
    assert not node._state.messages[-1].healthy
    assert node._state.messages[-1].faults == ["hub_lease_rejected"]


def test_snapshot_requests_are_coalesced_for_at_most_50ms_without_retimestamping(node):
    original = node._measured.sample
    old_id = node._observation
    for elapsed in (.01, .02, .049):
        node.clock["monotonic"] = 50. + elapsed
        observation_id, sample = node._capture()
        assert observation_id == old_id and sample is original
    assert [route for route, _ in node.calls].count("/snapshot") == 1
    node.clock["monotonic"] = 50.051
    node._capture()
    assert [route for route, _ in node.calls].count("/snapshot") == 2
    assert len(node._obs.messages) == 1 and original["capturedAtUnixMs"] == 100_000


def test_lease_callback_does_not_wait_for_snapshot_read_lock(node):
    command = admit_dispatched(node)
    node.clock.update(wall=100.1, monotonic=50.1)
    with node._sample_lock:
        node._lease(lease(node, observed_at_unix_ms=100_100))
    assert node._lease_valid(command) and node._lease_ack_expires == pytest.approx(50.6)
