"""Control-lease and independently measured arrival tests without a simulator."""

import copy
import importlib
import io
import sys
import threading
from pathlib import Path

import pytest


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "sim/gazebo"))
    module = importlib.import_module("field_hub")
    monkeypatch.setattr(module.time, "time", lambda: 1000.)
    monkeypatch.setattr(module.time, "monotonic", lambda: 10.)
    instance = module.Hub.__new__(module.Hub)
    instance.lock = threading.RLock()
    instance.goals = {}
    instance.stopped = set()
    instance.injections = {}
    instance.sequence = 7
    instance.events = io.StringIO()
    instance.snapshot = {
        "capturedAtUnixMs": 1_000_000,
        "states": {"ground-a": {"position": [0., 0., .15],
                                  "quaternionWXYZ": [1., 0., 0., 0.],
                                  "linearVelocity": [0., 0., 0.],
                                  "simulationTime": 1.}},
    }
    yield instance
    # Avoid caching a module with a sibling import from a transient sys.path.
    sys.modules.pop("field_hub", None)


def goal(**overrides):
    value = {"robotId": "ground-a", "commandId": "command-1", "leaseMs": 400,
             "deadlineUnixMs": 1_020_000, "goalPose": [1., 0., .15, 1., 0., 0., 0.]}
    value.update(overrides)
    return value


def test_wrong_stop_identity_cannot_change_latch_or_goal(hub):
    hub.request("/goal", goal())
    before = copy.deepcopy(hub.goals)
    with pytest.raises(ValueError, match="identity mismatch"):
        hub.request("/stop", {"robotId": "ground-a", "commandId": "wrong-command"})
    assert hub.stopped == set()
    assert hub.goals == before


def test_lease_cannot_replace_original_deadline(hub):
    hub.request("/goal", goal())
    before = copy.deepcopy(hub.goals)
    with pytest.raises(ValueError, match="deadline"):
        hub.request("/lease", goal(deadlineUnixMs=1_030_000))
    assert hub.goals == before


def test_expired_command_cannot_be_resurrected_by_late_lease(hub):
    hub.request("/goal", goal())
    hub.goals["ground-a"]["expiresMonotonic"] = 9.9
    before = hub.goals["ground-a"]["expiresMonotonic"]
    with pytest.raises(ValueError, match="expir"):
        hub.request("/lease", goal())
    assert hub.goals["ground-a"]["expiresMonotonic"] == before


def test_cancelled_command_cannot_be_resurrected_by_late_lease(hub):
    hub.request("/goal", goal())
    hub.request("/cancel", {"robotId": "ground-a", "commandId": "command-1"})
    with pytest.raises(ValueError):
        hub.request("/lease", goal())
    assert hub.goals["ground-a"]["blockedReason"] == "cancelled"


def test_arrival_requires_measured_position_and_low_velocity_not_ack(hub):
    ack = hub.request("/goal", goal())
    assert ack["ok"] and not ack["physicalStopVerified"]
    assert not hub.status("ground-a")["arrived"]
    hub.snapshot["states"]["ground-a"]["position"][0] = 1.
    hub.snapshot["states"]["ground-a"]["linearVelocity"][0] = .3
    assert not hub.status("ground-a")["arrived"]
    hub.snapshot["states"]["ground-a"]["linearVelocity"][0] = .02
    assert hub.status("ground-a")["arrived"]
    hub.goals["ground-a"]["blockedReason"] = "lease_expired"
    assert not hub.status("ground-a")["arrived"]


def test_latched_stop_blocks_new_motion_goal(hub):
    hub.request("/goal", goal())
    hub.request("/stop", {"robotId": "ground-a", "commandId": "command-1"})
    with pytest.raises(ValueError, match="latched"):
        hub.request("/goal", goal(commandId="new-command"))


def test_public_snapshot_exposes_latch_without_clearing_it(hub):
    assert hub.public_snapshot()["stoppedRobots"] == []
    hub.request("/goal", goal())
    hub.request("/stop", {"robotId": "ground-a", "commandId": "command-1"})
    public = hub.public_snapshot()
    assert public["stoppedRobots"] == ["ground-a"]
    assert "stoppedRobots" not in hub.snapshot  # No stale copy in physics capture.
    public["stoppedRobots"].clear()
    assert hub.public_snapshot()["stoppedRobots"] == ["ground-a"]
    with pytest.raises(ValueError, match="latched"):
        hub.request("/goal", goal(commandId="new-command"))


def test_unknown_robot_and_out_of_scene_goal_rejected(hub):
    with pytest.raises(ValueError, match="unknown robot"):
        hub.request("/goal", goal(robotId="other"))
    with pytest.raises(ValueError, match="envelope"):
        hub.request("/goal", goal(goalPose=[50., 0., .15, 1., 0., 0., 0.]))
    assert hub.goals == {}


def test_fault_injection_kind_is_recorded_without_event_argument_collision(hub):
    response = hub.request("/inject", {"robotId": "ground-a", "kind": "stale_peer"})
    assert response["ok"]
    assert hub.injections["ground-a"] == "stale_peer"
    assert '"kind": "stale_peer"' in hub.events.getvalue()


@pytest.mark.parametrize("expired", ["monotonic", "deadline"])
def test_new_command_atomically_retires_expired_goal_before_physics_tick(hub, expired):
    hub.request("/goal", goal())
    previous = hub.goals["ground-a"]
    hub.snapshot["states"]["ground-a"]["position"][0] = 1.
    assert hub.status("ground-a")["arrived"]
    if expired == "monotonic":
        previous["expiresMonotonic"] = 10.  # Exact boundary is expired.
    else:
        previous["deadlineUnixMs"] = 1_000_000
    old_expiry = previous["expiresMonotonic"]
    assert not hub.status("ground-a")["arrived"] and not previous.get("blockedReason")
    response = hub.request("/goal", goal(commandId="command-2", goalPose=[2., 0., .15, 1., 0., 0., 0.]))
    assert response["commandId"] == "command-2"
    assert previous["blockedReason"] == "lease_expired"
    assert previous["expiresMonotonic"] == old_expiry  # Old lease is never renewed.
    assert hub.goals["ground-a"]["commandId"] == "command-2"
    assert hub.goals["ground-a"]["expiresMonotonic"] == pytest.approx(10.4)
    assert "PREVIOUS_GOAL_EXPIRED" in hub.events.getvalue()
    assert hub.snapshot["states"]["ground-a"]["position"][0] == 1.
    before = copy.deepcopy(hub.goals)
    with pytest.raises(ValueError, match="match active"):
        hub.request("/lease", goal())
    assert hub.goals == before


def test_unexpired_moving_goal_cannot_be_replaced_by_new_command(hub):
    hub.request("/goal", goal())
    before = copy.deepcopy(hub.goals)
    with pytest.raises(ValueError, match="active goal"):
        hub.request("/goal", goal(commandId="command-2"))
    assert hub.goals == before


def test_goal_endpoint_cannot_replay_expired_motion_command(hub):
    hub.request("/goal", goal())
    hub.goals["ground-a"]["expiresMonotonic"] = 9.9
    before = copy.deepcopy(hub.goals)
    with pytest.raises(ValueError, match="replayed"):
        hub.request("/goal", goal())
    assert hub.goals == before
