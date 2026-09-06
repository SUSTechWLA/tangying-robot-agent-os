from __future__ import annotations

import copy

import pytest

from tests.e2e.fleet_harness import FleetHandoffStack


def startup_world():
    return {
        "robots": {"robot-1": {}, "robot-2": {}},
        "sources": {
            f"robot-{robot}/{kind}": {"freshness": "FRESH"}
            for robot in (1, 2)
            for kind in ("proprioception", "scene")
        },
        "entities": {"red-block": {"evidence": {"sourceId": "robot-1/scene"}}},
    }


@pytest.mark.parametrize("initial_state", ["stale", "partial-robot", "partial-entity"])
def test_startup_waits_for_complete_fresh_world_inputs(tmp_path, monkeypatch, initial_state):
    stack = FleetHandoffStack(tmp_path, 1, 2, 3, 4)
    fresh = startup_world()
    first = copy.deepcopy(fresh)
    if initial_state == "stale":
        first["sources"]["robot-1/scene"]["freshness"] = "STALE"
    elif initial_state == "partial-robot":
        first["robots"].pop("robot-2")
    else:
        first["entities"].clear()
    snapshots = iter((first, fresh))
    observed = []

    def api(path):
        if path == "/v1/devices":
            return [{"robotId": robot, "online": True} for robot in ("robot-1", "robot-2")]
        assert path == "/v1/world"
        world = next(snapshots)
        observed.append(world)
        return world

    monkeypatch.setattr(stack, "api", api)

    stack.wait_ready(timeout=1)

    assert observed[-1] == fresh, "startup returned before the ready world was observed"


def test_startup_rejects_persistently_stale_world_without_changing_budget(tmp_path, monkeypatch):
    stack = FleetHandoffStack(tmp_path, 1, 2, 3, 4)
    stale = startup_world()
    stale["sources"]["robot-2/proprioception"]["freshness"] = "STALE"

    def api(path):
        if path == "/v1/devices":
            return [{"robotId": robot, "online": True} for robot in ("robot-1", "robot-2")]
        return stale

    monkeypatch.setattr(stack, "api", api)

    with pytest.raises(AssertionError, match="did not become observation-ready"):
        stack.wait_ready(timeout=0.01)
