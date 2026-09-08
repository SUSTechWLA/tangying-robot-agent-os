import threading
import time

import pytest
from tangying_sim.tools import ToolResult


def pose(y=0.05):
    return [0, y, 0.035, 2**-0.5, 0, 0, 2**-0.5]


def response(state="RUNNING", **overrides):
    result = {"goalId": "goal-1", "commandId": "command-1", "state": state,
                  "actuationMode": "native_http", "mapReady": True, "velocityValid": True,
                  "mapRevision": "rtabmap-1", "mapPose": pose(), "goalPoseMap": pose(),
                  "poseSource": "rtabmap_tf", "poseObservedAtUnixMs": int(time.time()*1000),
                  "completionSource": "nav2_action" if state == "SUCCEEDED" else "",
                  "completionPoseObservedAtUnixMs": int(time.time()*1000) if state == "SUCCEEDED" else 0,
                  "latestCmdVel": {"linearX": 0.04, "linearY": 0, "angularZ": 0,
                                    "stampUnixMs": int(time.time()*1000)}}
    result.update(overrides)
    return result


def run_client(monkeypatch, states, *, cancel=None, drive=None):
    from tangying_sim.rtabmap_client import RTABMapClient
    client = RTABMapClient("http://127.0.0.1:18790", "test-token")
    requests, pulses, stops = [], [], []
    def request(method, path, payload=None, **_kwargs):
        requests.append((method, path, payload))
        if path.endswith("/map"):
            return {"ready": True, "mode": "mapping", "mapRevision": "rtabmap-1",
                        "actuationMode": "native_http",
                        "poseSource": "rtabmap_tf", "mapPose": pose(),
                        "poseObservedAtUnixMs": int(time.time()*1000),
                        "observedAtUnixMs": int(time.time()*1000)}
        if method == "POST" and path.endswith("/goals"):
            return response("PENDING")
        if path.endswith("/cancel"):
            return response("CANCELLED")
        return states.pop(0) if len(states) > 1 else states[0]
    def apply(*args, **kwargs):
        pulses.append(args)
        if drive:
            return drive(*args, **kwargs)
        return ToolResult(True)
    monkeypatch.setattr(client, "_request", request)
    result = client.navigate("command-1", pose(), int(time.time()*1000)+5000,
                             cancel or threading.Event(), apply, lambda: stops.append(True))
    return result, requests, pulses, stops


def test_nav2_drives_then_verifies_localized_goal(monkeypatch):
    result, requests, pulses, stops = run_client(monkeypatch, [response(), response("SUCCEEDED")])
    assert result.success and result.code == "NAV_GOAL_REACHED"
    assert len(pulses) == 1 and pulses[0][0] == 0.04
    assert stops and requests[1][2]["frameId"] == "odom"
    assert result.payload["map_revision"] == "rtabmap-1"
    assert result.payload["completion_source"] == "nav2_action"


def test_already_confirmed_pose_requires_no_motor_pulse_and_identifies_source(monkeypatch):
    result, requests, pulses, stops = run_client(monkeypatch, [response(
        "SUCCEEDED", completionSource="pose_confirmation")])
    assert result.success and not pulses and stops
    assert result.payload["completion_source"] == "pose_confirmation"
    assert "no motion requested" in result.message and "Nav2 completed" not in result.message
    assert 0 <= (result.payload["checked_at_unix_ms"]
                 - result.payload["completion_pose_observed_at_unix_ms"]) <= 1000
    assert sum(method == "POST" and path.endswith("/goals") for method, path, _ in requests) == 1


@pytest.mark.parametrize("change", [
    {"completionSource": ""}, {"completionSource": "unknown"},
    {"completionSource": "pose_confirmation", "completionPoseObservedAtUnixMs": 1},
    {"completionSource": "pose_confirmation", "completionPoseObservedAtUnixMs": 0},
])
def test_unknown_or_stale_original_completion_cannot_borrow_recovered_live_pose(monkeypatch, change):
    result, _, pulses, _ = run_client(monkeypatch, [response("SUCCEEDED", **change)])
    assert not result.success and result.code == "NAV_RECEIPT_INVALID" and not pulses


def test_failed_goal_preserves_frozen_observation_cause_without_current_state_backfill(monkeypatch):
    frozen = {"ready": False, "checkedAtUnixMs": 12345, "mapRevision": "failed-map",
              "readinessBlockers": ["HEAD_RGBD_STALE"],
              "inputAgeMs": {"head": 1030, "mapPose": 50},
              "sensorObservedAtUnixMs": {"head": 11315},
              "extra": "test-token", "cells": [1, 2, 3]}
    result, requests, pulses, stops = run_client(monkeypatch, [response(
        "FAILED", message="NAVIGATION_OBSERVATION_LOST", failureObservation=frozen)])
    assert not result.success and result.code == "NAV_FAILED"
    assert not pulses and stops
    receipt = result.payload["failure_observation"]
    assert receipt["checkedAtUnixMs"] == 12345
    assert receipt["mapRevision"] == "failed-map"
    assert receipt["readinessBlockers"] == ["HEAD_RGBD_STALE"]
    assert receipt["inputAgeMs"]["head"] == 1030
    assert not receipt["ready"] and "extra" not in receipt and "cells" not in receipt
    assert not any(path.endswith("/cancel") for _, path, _ in requests)


def test_legacy_failed_goal_has_no_invented_failure_observation(monkeypatch):
    result, _, _, _ = run_client(monkeypatch, [response("FAILED", message="NAV2_ACTION_ENDED")])
    assert result.payload["failure_observation"] == {}


def test_delayed_receipt_stops_and_waits_for_new_velocity_without_reposting_goal(monkeypatch):
    delayed = response(latestCmdVel={"linearX": .05, "linearY": 0, "angularZ": 0,
                                    "stampUnixMs": int(time.time()*1000)-220})
    result, requests, pulses, stops = run_client(monkeypatch, [delayed, response(), response("SUCCEEDED")])
    assert result.success
    assert len(pulses) == 1 and pulses[0][0] == .04  # The expired .05 command was never used.
    assert len(stops) >= 3
    assert sum(method == "POST" and path.endswith("/goals") for method, path, _ in requests) == 1


def test_expiry_while_waiting_for_motor_lock_reacquires_without_ambiguous_retry(monkeypatch):
    outcomes = [ToolResult(False, "NAV_VELOCITY_STALE"), ToolResult(True)]
    result, requests, pulses, stops = run_client(
        monkeypatch, [response(), response(), response("SUCCEEDED")],
        drive=lambda *_args, **_kwargs: outcomes.pop(0))
    assert result.success and len(pulses) == 2 and len(stops) >= 3
    assert sum(method == "POST" and path.endswith("/goals") for method, path, _ in requests) == 1


@pytest.mark.parametrize("bad", [
    response(latestCmdVel={"linearX": 0.04, "linearY": 0, "angularZ": 0, "stampUnixMs": 1}),
    response(commandId="some-other-robot-command"),
    response(latestCmdVel={"linearX": float("nan"), "linearY": 0, "angularZ": 0,
                               "stampUnixMs": int(time.time()*1000)}),
    response(state="SUCCEEDED", mapPose=pose(0.25)),
    response(state="SUCCEEDED", poseObservedAtUnixMs=1),
    response(state="SUCCEEDED", poseSource="simulator_ground_truth"),
    response(state="SUCCEEDED", mapReady=False),
    response(actuationMode="ros_driver"),
])
def test_unsafe_or_false_navigation_receipt_never_moves(monkeypatch, bad):
    result, requests, pulses, stops = run_client(monkeypatch, [bad])
    assert not result.success and not pulses and stops
    assert any(path.endswith("/cancel") for _, path, _ in requests)


def test_cancel_and_controller_rejection_stop_in_place(monkeypatch):
    event = threading.Event()
    def drive(*_args, **_kwargs):
        event.set()
        return ToolResult(False, "NAV_WORKSPACE_LIMIT")
    result, requests, pulses, stops = run_client(monkeypatch, [response()], cancel=event, drive=drive)
    assert not result.success and result.code == "NAV_WORKSPACE_LIMIT"
    assert len(pulses) == 1 and stops
    assert any(path.endswith("/cancel") for _, path, _ in requests)


def test_unready_map_never_creates_goal(monkeypatch):
    from tangying_sim.rtabmap_client import RTABMapClient
    client = RTABMapClient("http://127.0.0.1:18790", "test-token")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: {"ready": False, "actuationMode": "native_http", "message": "no RGB-D map"})
    moved = []
    result = client.navigate("command-1", pose(), int(time.time()*1000)+5000,
                             threading.Event(), lambda *_: moved.append(True), lambda: None)
    assert not result.success and result.code == "NAV_MAP_NOT_READY" and not moved


def test_connection_loss_stops_without_replaying_goal(monkeypatch):
    from tangying_sim.rtabmap_client import RTABMapClient
    client = RTABMapClient("http://127.0.0.1:18790", "test-token")
    calls, stopped = [], []
    def request(method, path, payload=None, **_kwargs):
        calls.append(path)
        if path.endswith("/map"):
            return {"ready": True, "mode": "mapping", "actuationMode": "native_http", "mapRevision": "one", "poseSource": "rtabmap_tf", "mapPose": pose(), "poseObservedAtUnixMs": int(time.time()*1000), "observedAtUnixMs": int(time.time()*1000)}
        if path.endswith("/goals"):
            return response("PENDING")
        raise OSError("bridge disconnected")
    monkeypatch.setattr(client, "_request", request)
    result = client.navigate("command-1", pose(), int(time.time()*1000)+5000,
                             threading.Event(), lambda *_: pytest.fail("must not drive"),
                             lambda: stopped.append(True))
    assert not result.success and result.code == "NAV_BRIDGE_UNAVAILABLE" and stopped
    assert calls.count("/v1/navigation/goals") == 1


@pytest.mark.parametrize("phase", ["RUNNING", "SUCCEEDED"])
def test_http_response_after_deadline_cannot_move_or_succeed(monkeypatch, phase):
    from types import SimpleNamespace

    from tangying_sim import rtabmap_client

    now = time.time()
    clock = [now]
    monkeypatch.setattr(rtabmap_client, "time", SimpleNamespace(time=lambda: clock[0], monotonic=time.monotonic))
    client = rtabmap_client.RTABMapClient("http://127.0.0.1:18790", "token")
    calls = []
    def request(method, path, payload=None, **_kwargs):
        calls.append(path)
        if path.endswith("/map"):
            return {**response(), "ready": True, "poseObservedAtUnixMs": int(clock[0]*1000)}
        if path.endswith(("/goals", "/cancel")):
            return response("PENDING")
        clock[0] += .1
        return response(phase)
    monkeypatch.setattr(client, "_request", request)
    pulses = []
    result = client.navigate("command-1", pose(), int(now*1000)+50, threading.Event(),
                             lambda *_args, **_kwargs: pulses.append(True) or ToolResult(True), lambda: None)
    assert not result.success and result.code == "NAV_DEADLINE_EXCEEDED"
    assert not pulses and any(path.endswith("/cancel") for path in calls)


def test_cancel_while_reading_readiness_never_posts_goal(monkeypatch):
    from tangying_sim.rtabmap_client import RTABMapClient
    client = RTABMapClient("http://127.0.0.1:18790", "token")
    cancel = threading.Event()
    calls = []
    def request(method, path, payload=None, **_kwargs):
        calls.append(path)
        cancel.set()
        return {**response("PENDING"), "ready": True}
    monkeypatch.setattr(client, "_request", request)
    result = client.navigate("command-1", pose(), int(time.time()*1000)+5000, cancel,
                             lambda *_args, **_kwargs: pytest.fail("cancelled motion"), lambda: None)
    assert not result.success and calls == ["/v1/navigation/map"]


@pytest.mark.parametrize("change", [
    {"mapReady": False}, {"poseObservedAtUnixMs": 1}, {"poseSource": "sim_truth"},
    {"mapPose": [0, 0, 0, 0, 0, 0, 0]}, {"velocityValid": False},
])
def test_running_motion_requires_live_localization_and_valid_velocity(monkeypatch, change):
    result, _, pulses, _ = run_client(monkeypatch, [response(**change), response("SUCCEEDED")])
    assert not result.success and not pulses


def test_wrong_post_identity_is_not_polled_or_cancelled_as_our_goal(monkeypatch):
    from tangying_sim.rtabmap_client import RTABMapClient
    client = RTABMapClient("http://127.0.0.1:18790", "token")
    calls = []
    def request(method, path, payload=None, **_kwargs):
        calls.append(path)
        if path.endswith("/map"):
            return {**response(), "ready": True}
        return response("PENDING", commandId="someone-else")
    monkeypatch.setattr(client, "_request", request)
    result = client.navigate("command-1", pose(), int(time.time()*1000)+5000, threading.Event(),
                             lambda *_args, **_kwargs: pytest.fail("foreign goal"), lambda: None)
    assert not result.success
    assert calls == ["/v1/navigation/map", "/v1/navigation/goals"]


def test_real_http_lifecycle_keeps_auth_identity_and_stops_after_completion():
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from tangying_sim.rtabmap_client import RTABMapClient

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append((self.command, self.path, self.headers.get("Authorization")))
            body = {**response("SUCCEEDED"), "robotId": "robot-1", "ready": True}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self):
            requests.append((self.command, self.path, self.headers.get("Authorization")))
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["commandId"] == "command-1" and body["frameId"] == "odom"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({**response("PENDING"), "robotId": "robot-1"}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = RTABMapClient(f"http://127.0.0.1:{server.server_port}", "local-token", robot_id="robot-1")
        stopped = []
        result = client.navigate("command-1", pose(), int(time.time()*1000)+5000, threading.Event(),
                                 lambda *_args, **_kwargs: pytest.fail("terminal receipt cannot move"),
                                 lambda: stopped.append(True))
        assert result.success and stopped
        assert requests == [("GET", "/v1/navigation/map", "Bearer local-token"),
                            ("POST", "/v1/navigation/goals", "Bearer local-token"),
                            ("GET", "/v1/navigation/goals/goal-1", "Bearer local-token")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def readiness(**overrides):
    return {
        **response(), "ready": True, "robotId": "robot-1",
        "checkedAtUnixMs": int(time.time()*1000), "readinessBlockers": [],
        "inputAgeMs": {"base": 30, "head": 25},
        "visualQuality": {"currentFrameWords": 155.0, "dictionaryWords": 113.0, "ready": True},
        **overrides,
    }


def test_brief_source_loss_waits_for_fresh_readiness_before_single_dispatch(monkeypatch):
    from tangying_sim.rtabmap_client import RTABMapClient

    client = RTABMapClient("http://127.0.0.1:18790", "private-token", robot_id="robot-1")
    calls, checks, pulses = [], [], []

    def request(method, path, payload=None, **_kwargs):
        calls.append((method, path))
        if path.endswith("/map"):
            checks.append(True)
            if len(checks) < 3:
                return readiness(ready=False, readinessBlockers=["BASE_RGBD_STALE"],
                                 inputAgeMs={"base": 1160}, authorization="private-token")
            return readiness()
        assert len(checks) == 3, "a goal was dispatched before the source recovered"
        return {**response("PENDING" if method == "POST" else "SUCCEEDED"), "robotId": "robot-1"}

    monkeypatch.setattr(client, "_request", request)
    result = client.navigate("command-1", pose(), int(time.time()*1000)+5000, threading.Event(),
                             lambda *_args, **_kwargs: pulses.append(True), lambda: None)
    assert result.success and not pulses
    assert calls.count(("POST", "/v1/navigation/goals")) == 1
    assert result.payload["readiness"]["attempts"] == 3
    assert result.payload["readiness"]["last_not_ready"]["readinessBlockers"] == ["BASE_RGBD_STALE"]
    assert result.payload["readiness"]["last_status"]["visualQuality"]["currentFrameWords"] == 155
    assert "private-token" not in str(result.payload)


@pytest.mark.parametrize("timeout", [False, True])
def test_persistent_readiness_failure_is_bounded_and_preserves_cause(monkeypatch, timeout):
    from tangying_sim.rtabmap_client import RTABMapClient

    client = RTABMapClient("http://127.0.0.1:18790", "private-token", robot_id="robot-1")
    checks = []

    def request(method, path, payload=None, **_kwargs):
        assert method == "GET" and path.endswith("/map"), "unready navigation dispatched a goal"
        checks.append(True)
        if timeout:
            raise TimeoutError("private-token must never appear in history")
        return readiness(ready=False, readinessBlockers=["BASE_RGBD_STALE"],
                         inputAgeMs={"base": 1160}, authorization="private-token")

    monkeypatch.setattr(client, "_request", request)
    started = time.monotonic()
    result = client.navigate("command-1", pose(), int(time.time()*1000)+10000, threading.Event(),
                             lambda *_args, **_kwargs: pytest.fail("unready motion"), lambda: None)
    assert not result.success
    assert result.code == ("NAV_STATUS_TIMEOUT" if timeout else "NAV_MAP_NOT_READY")
    assert 2.8 <= time.monotonic()-started < 4.0
    assert len(checks) > 1
    details = result.payload["readiness"]
    assert details["last_status"]["statusCode"] == result.code
    if not timeout:
        assert details["last_status"]["inputAgeMs"]["base"] == 1160
    assert "private-token" not in str(result)


@pytest.mark.parametrize("wrong,code", [
    ({"robotId": "other-robot"}, "NAV_STATUS_ROBOT_MISMATCH"),
    ({"actuationMode": "ros_driver"}, "NAV_STATUS_ACTUATION_MISMATCH"),
    ({"ready": "true"}, "NAV_STATUS_INVALID"),
])
def test_fatal_readiness_contract_error_is_not_retried(monkeypatch, wrong, code):
    from tangying_sim.rtabmap_client import RTABMapClient

    client = RTABMapClient("http://127.0.0.1:18790", "token", robot_id="robot-1")
    calls = []

    def request(method, path, payload=None, **_kwargs):
        calls.append((method, path))
        return readiness(**wrong)

    monkeypatch.setattr(client, "_request", request)
    result = client.navigate("command-1", pose(), int(time.time()*1000)+5000, threading.Event(),
                             lambda *_args, **_kwargs: pytest.fail("wrong bridge motion"), lambda: None)
    assert not result.success and result.code == code
    assert calls == [("GET", "/v1/navigation/map")]
    assert result.payload["readiness"]["last_status"]["statusCode"] == code


@pytest.mark.parametrize("http_status", [401, 403, 404])
def test_fatal_readiness_http_error_is_not_retried(monkeypatch, http_status):
    import urllib.error

    from tangying_sim.rtabmap_client import RTABMapClient

    client = RTABMapClient("http://127.0.0.1:18790", "private-token", robot_id="robot-1")
    calls = []

    def request(method, path, payload=None, **_kwargs):
        calls.append((method, path))
        raise urllib.error.HTTPError("http://secret-host/private-token", http_status,
                                     "private-token", {}, None)

    monkeypatch.setattr(client, "_request", request)
    result = client.navigate("command-1", pose(), int(time.time()*1000)+5000, threading.Event(),
                             lambda *_args, **_kwargs: pytest.fail("HTTP failure motion"), lambda: None)
    assert not result.success and result.code == "NAV_STATUS_HTTP_ERROR"
    assert calls == [("GET", "/v1/navigation/map")]
    assert result.payload["readiness"]["last_status"]["httpStatus"] == http_status
    assert "private-token" not in str(result)


@pytest.mark.parametrize("cancelled", [False, True])
def test_slow_readiness_does_not_hold_cancellation_or_extend_deadline(monkeypatch, cancelled):
    from tangying_sim.rtabmap_client import RTABMapClient

    client = RTABMapClient("http://127.0.0.1:18790", "token", robot_id="robot-1")
    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    calls, results, stopped = [], [], []

    def request(method, path, payload=None, **_kwargs):
        calls.append((method, path))
        entered.set()
        assert release.wait(2), "test did not release slow read"
        return readiness()

    monkeypatch.setattr(client, "_request", request)
    deadline = int(time.time()*1000)+(3000 if cancelled else 150)
    worker = threading.Thread(target=lambda: results.append(client.navigate(
        "command-1", pose(), deadline, cancel,
        lambda *_args, **_kwargs: pytest.fail("late status motion"), lambda: stopped.append(True))))
    worker.start()
    try:
        assert entered.wait(1)
        if cancelled:
            cancel.set()
        worker.join(.4)
        assert not worker.is_alive(), "readiness GET delayed cancellation/deadline"
        assert results[0].code == ("CANCELLED" if cancelled else "NAV_DEADLINE_EXCEEDED")
        assert stopped
    finally:
        release.set()
        worker.join(2)
    assert calls == [("GET", "/v1/navigation/map")]


@pytest.mark.parametrize("cancelled", [False, True])
def test_cancelled_or_expired_command_never_reads_or_dispatches(monkeypatch, cancelled):
    from tangying_sim.rtabmap_client import RTABMapClient

    client = RTABMapClient("http://127.0.0.1:18790", "token", robot_id="robot-1")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: pytest.fail("ended command sent HTTP"))
    event, stops = threading.Event(), []
    if cancelled:
        event.set()
    deadline = int(time.time()*1000)+(5000 if cancelled else -1)
    result = client.navigate("command-1", pose(), deadline, event,
                             lambda *_args, **_kwargs: pytest.fail("ended command moved"),
                             lambda: stops.append(True))
    assert result.code == ("CANCELLED" if cancelled else "NAV_DEADLINE_EXCEEDED")
    assert stops


def test_readiness_wait_does_not_refresh_old_localization_or_velocity(monkeypatch):
    from tangying_sim.rtabmap_client import RTABMapClient

    client = RTABMapClient("http://127.0.0.1:18790", "token", robot_id="robot-1")
    checks, calls = [], []

    def request(method, path, payload=None, **_kwargs):
        calls.append((method, path))
        if path.endswith("/map"):
            checks.append(True)
            return readiness(poseObservedAtUnixMs=1) if len(checks) == 1 else readiness()
        assert len(checks) == 2
        return {**response("PENDING" if method == "POST" else "RUNNING", latestCmdVel={
            "linearX": .04, "linearY": 0, "angularZ": 0, "stampUnixMs": 1,
        }), "robotId": "robot-1"}

    monkeypatch.setattr(client, "_request", request)
    result = client.navigate("command-1", pose(), int(time.time()*1000)+5000, threading.Event(),
                             lambda *_args, **_kwargs: pytest.fail("stale velocity moved"), lambda: None)
    assert result.code == "NAV_COMMAND_STALE"
    assert result.payload["readiness"]["last_not_ready"]["poseObservedAtUnixMs"] == 1
    assert calls.count(("POST", "/v1/navigation/goals")) == 1
    assert calls[-1][1].endswith("/cancel")


def test_timeout_after_unready_status_retains_last_sensor_diagnostics(monkeypatch):
    import urllib.error

    from tangying_sim.rtabmap_client import RTABMapClient

    client = RTABMapClient("http://127.0.0.1:18790", "private-token", robot_id="robot-1")
    checks = []

    def request(method, path, payload=None, **_kwargs):
        assert method == "GET" and path.endswith("/map")
        checks.append(True)
        if len(checks) == 1:
            return readiness(ready=False, readinessBlockers=["BASE_RGBD_STALE"], inputAgeMs={"base": 1160})
        raise urllib.error.URLError(TimeoutError("private-token"))

    monkeypatch.setattr(client, "_request", request)
    result = client.navigate("command-1", pose(), int(time.time()*1000)+250, threading.Event(),
                             lambda *_args, **_kwargs: pytest.fail("unready motion"), lambda: None)
    assert result.code == "NAV_DEADLINE_EXCEEDED"
    details = result.payload["readiness"]
    assert details["last_status"]["statusCode"] == "NAV_STATUS_TIMEOUT"
    assert details["last_not_ready"]["inputAgeMs"]["base"] == 1160
    assert details["last_not_ready"]["readinessBlockers"] == ["BASE_RGBD_STALE"]
    assert "private-token" not in str(result)
