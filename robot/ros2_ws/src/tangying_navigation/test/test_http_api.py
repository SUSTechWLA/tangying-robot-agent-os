import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request

import pytest
from tangying_navigation.http_api import GoalRegistry, create_http_server

TOKEN = "test-navigation-token-do-not-print"
GOAL = {"commandId": "command-1", "goalPose": [0, 0.05, 0, 1, 0, 0, 0], "frameId": "odom"}


class Driver:
    def __init__(self):
        self.ready = True
        self.started = []
        self.cancelled = []
        # This fake models a live publisher unless a test explicitly freezes
        # its timestamp to exercise stale/future sample rejection.
        self._stamp = None

    @property
    def stamp(self):
        return int(time.time() * 1000) if self._stamp is None else self._stamp

    @stamp.setter
    def stamp(self, value):
        self._stamp = value

    def map_status(self, include_grid=False):
        return {
            **({"cells": [-1, 0, 100], "origin": [0, 0, 0, 1, 0, 0, 0]} if include_grid else {}),
            "ready": self.ready,
            "mapRevision": "map-1",
            "mapPose": [0, 0, 0, 1, 0, 0, 0],
            "poseSource": "rtabmap_tf",
            "poseObservedAtUnixMs": int(time.time() * 1000),
            "robotId": "robot-a",
        }

    def velocity(self):
        return {"linearX": 0.02, "linearY": 0.0, "angularZ": 0.0, "stampUnixMs": self.stamp}

    def goal_pose_map(self, goal_id):
        return GOAL["goalPose"]

    def start(self, goal_id, request):
        self.started.append((goal_id, request))

    def cancel(self, goal_id):
        self.cancelled.append(goal_id)


@pytest.fixture
def boundary(tmp_path):
    driver = Driver()
    registry = GoalRegistry(driver, tmp_path / "goals.sqlite")
    server = create_http_server("127.0.0.1", 0, TOKEN, registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None, token=TOKEN):
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}" + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    yield driver, registry, request
    server.shutdown()
    server.server_close()
    registry.close()
    thread.join()


def test_real_http_auth_validation_idempotency_and_cancel(boundary):
    driver, registry, request = boundary
    status, response = request("/v1/navigation/goals", GOAL, "incorrect")
    assert status == 401 and TOKEN not in json.dumps(response) and not driver.started
    assert request("/v1/navigation/goals", {**GOAL, "goalPose": [0]})[0] == 400
    status, response = request("/v1/navigation/goals", GOAL)
    assert status == 202 and response["state"] == "PENDING"
    goal_id = response["goalId"]
    assert (
        request("/v1/navigation/goals", GOAL)[1]["goalId"] == goal_id and len(driver.started) == 1
    )
    assert request("/v1/navigation/goals", {**GOAL, "frameId": "map"})[0] == 409
    assert request("/v1/navigation/goals", {**GOAL, "commandId": "second"})[0] == 409
    registry.update(goal_id, "RUNNING")
    assert request("/v1/navigation/goals/" + goal_id)[1]["latestCmdVel"]["linearX"] == 0.02
    assert request("/v1/navigation/goals/" + goal_id + "/cancel", {})[1]["state"] == "CANCELLED"
    assert driver.cancelled == [goal_id]
    assert request("/v1/navigation/goals/" + goal_id)[1]["latestCmdVel"]["linearX"] == 0


def test_observation_loss_cancels_immediately_during_status_read(boundary):
    driver, registry, request = boundary
    goal_id = request("/v1/navigation/goals", GOAL)[1]["goalId"]
    registry.update(goal_id, "RUNNING")
    driver.ready = False
    response = request("/v1/navigation/goals/" + goal_id)[1]
    assert response["state"] == "FAILED" and response["message"] == "NAVIGATION_OBSERVATION_LOST"
    assert response["latestCmdVel"]["linearX"] == 0 and driver.cancelled == [goal_id]


@pytest.mark.parametrize("trigger", ["status", "watchdog"])
def test_observation_loss_keeps_original_cause_after_recovery_and_restart(tmp_path, trigger):
    driver = Driver()
    path = tmp_path / "goals.sqlite"
    registry = GoalRegistry(driver, path)
    goal_id = registry.submit(GOAL)["goalId"]
    registry.update(goal_id, "RUNNING")
    original = driver.map_status()
    lost = {
        **original,
        "ready": False,
        "checkedAtUnixMs": 123456,
        "readinessBlockers": ["MAP_POSE_STALE"],
        "inputAgeMs": {"mapPose": 1030, "base": 80, "head": 110},
        "sensorObservedAtUnixMs": {"base": 123376, "head": 123346},
        "cells": [100] * 1000,
        "secret": "do-not-store",
    }
    driver.map_status = lambda **_kwargs: lost
    if trigger == "status":
        registry.status(goal_id)
    else:
        registry.watchdog()
    driver.map_status = lambda **_kwargs: original
    receipt = registry.status(goal_id)["failureObservation"]
    assert receipt["checkedAtUnixMs"] == 123456
    assert receipt["readinessBlockers"] == ["MAP_POSE_STALE"]
    assert receipt["inputAgeMs"]["mapPose"] == 1030
    assert not receipt["ready"]
    assert "cells" not in receipt and "secret" not in receipt
    assert driver.cancelled == [goal_id]
    registry.close()
    restarted = GoalRegistry(driver, path)
    assert restarted.status(goal_id)["failureObservation"] == receipt
    assert len(driver.started) == 1
    restarted.close()


def test_existing_goal_ledger_migrates_without_rewriting_history(tmp_path):
    path = tmp_path / "old-goals.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE goals (id TEXT PRIMARY KEY, command TEXT UNIQUE NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO goals VALUES (?,?,?,?,?)",
            ("historical", "old-command", "{}", "FAILED", "NAVIGATION_OBSERVATION_LOST"),
        )
        db.execute(
            "INSERT INTO goals VALUES (?,?,?,?,?)",
            ("old-success", "old-success-command", "{}", "SUCCEEDED", ""),
        )
    registry = GoalRegistry(Driver(), path)
    result = registry.status("historical")
    assert result["message"] == "NAVIGATION_OBSERVATION_LOST"
    assert result["failureObservation"] == {}  # Missing history must not be backfilled.
    assert result["completionSource"] == "" and result["completionPoseObservedAtUnixMs"] == 0
    old_success = registry.status("old-success")
    assert old_success["state"] == "SUCCEEDED"
    assert old_success["completionSource"] == "" and old_success["completionPoseObservedAtUnixMs"] == 0
    registry.close()


@pytest.mark.parametrize("source", ["pose_confirmation", "nav2_action"])
def test_completion_source_and_original_pose_time_persist_without_replay(tmp_path, source):
    driver = Driver()
    path = tmp_path / "completion.sqlite"
    registry = GoalRegistry(driver, path)
    goal_id = registry.submit(GOAL)["goalId"]
    stamp = int(time.time() * 1000)
    registry.update(goal_id, "SUCCEEDED", completion_source=source, completion_pose_stamp=stamp)
    registry.update(goal_id, "SUCCEEDED", completion_source="nav2_action", completion_pose_stamp=stamp + 100)
    registry.close()
    restarted = GoalRegistry(driver, path)
    result = restarted.submit(GOAL)
    assert result["state"] == "SUCCEEDED" and not result["velocityValid"]
    assert result["completionSource"] == source
    assert result["completionPoseObservedAtUnixMs"] == stamp
    assert len(driver.started) == 1
    restarted.close()


def test_success_cannot_be_recorded_without_provenance(boundary):
    _driver, registry, request = boundary
    goal_id = request("/v1/navigation/goals", GOAL)[1]["goalId"]
    with pytest.raises(ValueError):
        registry.update(goal_id, "SUCCEEDED")
    assert registry.status(goal_id)["state"] == "PENDING"


def test_stale_command_not_retimed_and_abandoned_client_cancelled(boundary):
    driver, registry, request = boundary
    goal_id = request("/v1/navigation/goals", GOAL)[1]["goalId"]
    registry.update(goal_id, "RUNNING")
    driver.stamp -= 3000
    response = request("/v1/navigation/goals/" + goal_id)[1]
    assert response["latestCmdVel"]["stampUnixMs"] == driver.stamp and not response["velocityValid"]
    registry.last_poll -= 3
    registry.watchdog()
    assert registry.status(goal_id)["state"] == "FAILED" and driver.cancelled == [goal_id]


def test_goal_ledger_restart_never_replays_active_command(tmp_path):
    driver = Driver()
    path = tmp_path / "goals.sqlite"
    registry = GoalRegistry(driver, path)
    result = registry.submit(GOAL)
    # Simulate process death without a clean shutdown.
    registry.db.close()
    restarted_driver = Driver()
    restarted = GoalRegistry(restarted_driver, path)
    result = restarted.submit(GOAL)
    assert result["state"] == "FAILED" and result["message"] == "NAVIGATION_SERVICE_RESTARTED"
    assert not restarted_driver.started
    restarted.close()


def test_map_grid_is_explicitly_requested_and_never_in_native_status(boundary):
    _, _, request = boundary
    assert "cells" not in request("/v1/navigation/map")[1]
    full = request("/v1/navigation/map?includeGrid=1")[1]
    assert full["cells"] == [-1, 0, 100] and full["origin"] == [0, 0, 0, 1, 0, 0, 0]
    assert request("/v1/navigation/map?includeGrid=2")[0] == 404


def test_watchdog_cancels_stale_nav2_velocity_even_without_a_status_poll(boundary):
    driver, registry, request = boundary
    goal_id = request("/v1/navigation/goals", GOAL)[1]["goalId"]
    registry.update(goal_id, "RUNNING")
    driver.stamp -= 1000
    registry.watchdog()
    result = registry.status(goal_id)
    assert result["state"] == "FAILED" and result["message"] == "NAV2_VELOCITY_STALE"
    assert driver.cancelled == [goal_id]


@pytest.mark.parametrize("trigger", ["status", "watchdog"])
def test_velocity_timeout_freezes_pre_camera_timeout_diagnostics_after_recovery(tmp_path, trigger):
    driver = Driver()
    stamp = int(time.time() * 1000)
    original = {**driver.map_status(), "checkedAtUnixMs": stamp,
                "readinessBlockers": [], "inputAgeMs": {"base": 700, "head": 750, "odometry": 700},
                "sensorObservedAtUnixMs": {"base": stamp - 700, "head": stamp - 750}}
    driver.map_status = lambda **_kwargs: original
    path = tmp_path / "velocity-loss.sqlite"
    registry = GoalRegistry(driver, path)
    goal_id = registry.submit(GOAL)["goalId"]
    registry.update(goal_id, "RUNNING")
    driver.stamp = stamp - 300
    if trigger == "status":
        first_terminal = registry.status(goal_id)
        assert first_terminal["failureObservation"]["checkedAtUnixMs"] == stamp
    else:
        registry.watchdog()
    failed = registry.status(goal_id)
    assert failed["state"] == "FAILED" and failed["message"] == "NAV2_VELOCITY_STALE"
    frozen = failed["failureObservation"]
    assert frozen["checkedAtUnixMs"] == stamp
    assert frozen["ready"] is True  # Velocity lease can expire before the 1 s sensor watchdog.
    assert frozen["inputAgeMs"]["base"] == 700
    driver.map_status = lambda **_kwargs: {**original, "checkedAtUnixMs": stamp + 2000,
                                          "inputAgeMs": {"base": 20, "head": 30}}
    assert registry.status(goal_id)["failureObservation"] == frozen
    registry.close()
    restarted = GoalRegistry(driver, path)
    assert restarted.status(goal_id)["failureObservation"] == frozen
    assert len(driver.started) == 1
    restarted.close()


def test_diagnostic_exception_cannot_prevent_failure_or_backfill_terminal_goal(tmp_path):
    driver = Driver()
    healthy = driver.map_status
    registry = GoalRegistry(driver, tmp_path / "diagnostic-error.sqlite")
    goal_id = registry.submit(GOAL)["goalId"]
    attempts = []

    def unavailable():
        attempts.append(True)
        raise RuntimeError("diagnostic input unavailable")

    driver.map_status = unavailable
    registry.update(goal_id, "FAILED", "NAV2_ACTION_ENDED")
    assert attempts == [True] and registry.active_id is None
    # A delayed callback cannot acquire another snapshot or rewrite the cause.
    registry.update(goal_id, "FAILED", "LATE_CALLBACK")
    assert attempts == [True]
    driver.map_status = healthy
    failed = registry.status(goal_id)
    assert failed["state"] == "FAILED" and failed["message"] == "NAV2_ACTION_ENDED"
    assert failed["failureObservation"] == {}
    registry.close()


def test_client_lease_expires_even_when_nav2_keeps_producing_fresh_velocity(boundary):
    driver, registry, request = boundary
    goal_id = request("/v1/navigation/goals", GOAL)[1]["goalId"]
    registry.update(goal_id, "RUNNING")
    driver.stamp = int(time.time() * 1000)
    registry.last_poll -= 3
    registry.watchdog()
    result = registry.status(goal_id)
    assert result["state"] == "FAILED" and result["message"] == "CLIENT_LEASE_EXPIRED"
    assert result["latestCmdVel"]["linearX"] == 0 and not result["velocityValid"]
    assert driver.cancelled == [goal_id]


def test_previous_goal_late_callback_cannot_reactivate_or_change_current_goal(boundary):
    driver, registry, request = boundary
    first = request("/v1/navigation/goals", GOAL)[1]["goalId"]
    request("/v1/navigation/goals/" + first + "/cancel", {})
    second = request("/v1/navigation/goals", {**GOAL, "commandId": "second"})[1]["goalId"]
    registry.update(first, "RUNNING")
    registry.update(first, "SUCCEEDED")
    assert registry.status(first)["state"] == "CANCELLED"
    assert registry.status(second)["state"] == "PENDING"
    assert registry.active_id == second and len(driver.started) == 2


def test_opted_in_driver_stops_for_transient_loss_and_resumes_same_goal(tmp_path, monkeypatch):
    from tangying_navigation import http_api
    clock = [100.]
    monkeypatch.setattr(http_api.time, 'monotonic', lambda: clock[0])
    driver = Driver()
    driver.supports_observation_hold = True
    holds = []
    driver.set_observation_hold = holds.append
    original = driver.map_status
    driver.map_status = lambda: {**original(), 'readinessBlockers': [] if driver.ready else ['HEAD_RGBD_STALE']}
    registry = GoalRegistry(driver, tmp_path/'goals.sqlite')
    goal_id = registry.submit(GOAL)['goalId']
    registry.update(goal_id, 'RUNNING')
    driver.ready = False
    status = registry.status(goal_id)
    assert status['state'] == 'RUNNING' and status['observationHold'] and not status['mapReady']
    assert status['latestCmdVel']['linearX'] == 0 and not status['velocityValid']
    assert holds == [True] and not driver.cancelled
    clock[0] += .4
    driver.ready = True
    status = registry.status(goal_id)
    assert status['state'] == 'RUNNING' and not status['observationHold']
    assert holds[-1] is False and len(driver.started) == 1
    assert status['observationHolds'][0]['resumedAtUnixMs'] > 0
    driver.ready = False
    registry.status(goal_id)
    clock[0] += 2.01
    registry.last_poll = clock[0]
    registry.watchdog()
    assert registry.status(goal_id)['message'] == 'NAVIGATION_OBSERVATION_LOST'
    assert driver.cancelled == [goal_id]
    registry.close()


def test_perception_fault_never_uses_transient_hold(tmp_path):
    driver = Driver()
    driver.supports_observation_hold = True
    driver.set_observation_hold = lambda _: pytest.fail('must fail immediately')
    original = driver.map_status
    driver.map_status = lambda: {**original(), 'readinessBlockers': ['VISUAL_QUALITY_LOW']}
    registry = GoalRegistry(driver, tmp_path/'goals.sqlite')
    goal_id = registry.submit(GOAL)['goalId']
    driver.ready = False
    assert registry.status(goal_id)['state'] == 'FAILED'
    assert driver.cancelled == [goal_id]
    registry.close()


def held_boundary(tmp_path, monkeypatch, *, budget=8.0, supported=True):
    from tangying_navigation import http_api

    clock = [100.0]
    monkeypatch.setattr(http_api.time, "monotonic", lambda: clock[0])
    driver = Driver()
    driver.supports_observation_hold = supported
    holds = []
    driver.set_observation_hold = holds.append
    driver.blockers = ["HEAD_RGBD_STALE"]
    original = driver.map_status
    driver.map_status = lambda: {
        **original(),
        "readinessBlockers": [] if driver.ready else driver.blockers,
        "actuationMode": "ros_driver",
    }
    registry = GoalRegistry(driver, tmp_path / "goals.sqlite", observation_hold_seconds=budget)
    goal_id = registry.submit(GOAL)["goalId"]
    registry.update(goal_id, "RUNNING")
    return registry, goal_id, driver, holds, clock


def test_configured_hold_recovers_after_three_seconds_without_resubmission(tmp_path, monkeypatch):
    registry, goal_id, driver, holds, clock = held_boundary(tmp_path, monkeypatch)
    try:
        driver.ready = False
        for elapsed in (0, 1, 2, 3):
            clock[0] = 100.0 + elapsed
            status = registry.status(goal_id)
            registry.watchdog()
            assert status["state"] == "RUNNING" and status["observationHold"]
            assert not status["mapReady"] and not status["velocityValid"]
            assert status["latestCmdVel"]["linearX"] == 0
            assert registry.active_id == goal_id and not driver.cancelled
        assert all(holds) and status["observationHolds"][0]["maxDurationSeconds"] == 8
        driver.ready = True
        clock[0] = 103.1
        status = registry.status(goal_id)
        assert not status["observationHold"] and status["mapReady"]
        assert status["state"] == "RUNNING" and holds[-1] is False
        assert registry.active_id == goal_id and len(driver.started) == 1
    finally:
        registry.close()


@pytest.mark.parametrize("trigger", ["status", "watchdog"])
@pytest.mark.parametrize("recovered", [False, True])
def test_hold_expiry_cancels_even_if_late_check_sees_fresh_data(
    tmp_path, monkeypatch, trigger, recovered,
):
    registry, goal_id, driver, holds, clock = held_boundary(tmp_path, monkeypatch)
    try:
        driver.ready = False
        assert registry.status(goal_id)["observationHold"]
        clock[0] = 108.0
        # Isolate the observation budget from the separate, unchanged client lease.
        registry.last_poll = clock[0]
        driver.ready = recovered
        if trigger == "watchdog":
            registry.watchdog()
        result = registry.status(goal_id)
        assert result["state"] == "FAILED"
        assert result["message"] == "NAVIGATION_OBSERVATION_LOST"
        assert result["latestCmdVel"]["linearX"] == 0 and not result["velocityValid"]
        assert driver.cancelled == [goal_id] and holds == [True]
        assert registry.active_id is None and len(driver.started) == 1
        driver.ready = True
        assert registry.submit(GOAL)["state"] == "FAILED"
        assert len(driver.started) == 1  # Exact retry only returns the failed receipt.
    finally:
        registry.close()


@pytest.mark.parametrize("supported,blockers", [
    (False, ["HEAD_RGBD_STALE"]),
    (True, ["HEAD_RGBD_STALE", "VISUAL_QUALITY_LOW"]),
    (True, ["LOCALIZATION_UNAVAILABLE"]),
    (True, ["CANCELLATION_PENDING"]),
])
def test_configured_budget_never_holds_unsupported_or_unsafe_loss(
    tmp_path, monkeypatch, supported, blockers,
):
    registry, goal_id, driver, holds, _clock = held_boundary(
        tmp_path, monkeypatch, supported=supported,
    )
    try:
        driver.ready = False
        driver.blockers = blockers
        assert registry.status(goal_id)["state"] == "FAILED"
        assert driver.cancelled == [goal_id] and not holds
    finally:
        registry.close()


def test_extended_hold_does_not_extend_client_motion_lease(tmp_path, monkeypatch):
    registry, goal_id, driver, _holds, clock = held_boundary(tmp_path, monkeypatch)
    try:
        driver.ready = False
        assert registry.status(goal_id)["observationHold"]
        clock[0] += 2.01
        registry.watchdog()
        assert registry.status(goal_id)["message"] == "CLIENT_LEASE_EXPIRED"
        assert driver.cancelled == [goal_id]
    finally:
        registry.close()


@pytest.mark.parametrize("budget", [0, -1, 15.01, float("inf"), float("nan"), True, "8"])
def test_invalid_hold_configuration_fails_before_opening_journal(tmp_path, budget):
    path = tmp_path / "invalid.sqlite"
    with pytest.raises(ValueError, match="observation hold"):
        GoalRegistry(Driver(), path, observation_hold_seconds=budget)
    assert not path.exists()
