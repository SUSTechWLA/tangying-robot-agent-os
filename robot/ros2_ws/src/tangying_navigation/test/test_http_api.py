import json
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
        self.stamp = int(time.time() * 1000)

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
