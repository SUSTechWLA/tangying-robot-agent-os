import io
import json
import urllib.error

import pytest

from scripts import run_gazebo_stop_checks as checks
from scripts.run_gazebo_stop_checks import measured_stop, wait_for_readiness


def state(time, x, velocity, *, angular=0):
    return {"simulationTime": time, "position": [x, 0, 1],
            "linearVelocity": [velocity, 0, 0], "angularVelocity": [0, 0, angular],
            "robotId": "ground-a", "clockDomain": "simulation", "frameId": "world", "worldId": "field-validation"}


def test_ack_or_repeated_snapshot_is_not_measured_stop():
    trigger = state(1., 0., .3)
    assert not measured_stop([], trigger)["measuredStopped"]
    assert not measured_stop([state(1., 0., 0)] * 10, trigger)["measuredStopped"]


def test_stopping_requires_low_linear_and_angular_velocity_for_dwell():
    trigger = state(1., 0., .3)
    samples = [state(1.1, .02, .2), state(1.2, .04, .01, angular=.1),
               state(1.3, .05, .01), state(1.5, .052, .005), state(1.7, .053, 0)]
    result = measured_stop(samples, trigger)
    assert result["measuredStopped"]
    assert result["settledSinceSimulationTime"] == 1.3
    assert result["maximumDisplacementMeters"] == pytest.approx(.053)
    assert result["elapsedSimulationSeconds"] == pytest.approx(.7)
    assert not result["landingVerified"]


def test_motion_resumption_restarts_stop_dwell():
    trigger = state(1., 0., .3)
    samples = [state(1.1, .02, 0), state(1.5, .02, 0), state(1.6, .04, .2)]
    assert not measured_stop(samples, trigger)["measuredStopped"]


def test_nonmonotonic_measurements_do_not_fabricate_dwell_or_travel():
    trigger = state(1., 0., .3)
    result = measured_stop([state(1.1, .02, 0), state(.5, 100, 0), state(1.5, .02, 0)], trigger)
    assert result["sampleCount"] == 2
    assert result["measuredStopped"]
    assert result["maximumDisplacementMeters"] == pytest.approx(.02)


class ReadinessClock:
    def __init__(self):
        self.now = 0.

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class SnapshotClient:
    def __init__(self, snapshots):
        self.snapshots = iter(snapshots)
        self.last = {}
        self.calls = []

    def call(self, path, payload=None, **kwargs):
        self.calls.append((path, payload, kwargs))
        self.last = next(self.snapshots, self.last)
        return self.last


def ready_snapshot(at):
    own = state(at, 0, 0)
    own.update(robotId="ground-a", clockDomain="simulation", quaternionWXYZ=[1., 0., 0., 0.])
    return {"states": {"ground-a": own}, "stoppedRobots": []}


def test_readiness_waits_for_states_and_two_advancing_measurements_without_motion():
    clock = ReadinessClock()
    client = SnapshotClient([{}, ready_snapshot(1.), ready_snapshot(1.), ready_snapshot(1.1)])
    result = wait_for_readiness(client, ["ground-a"], timeout=1., clock=clock.clock, sleep=clock.sleep)
    assert result["ready"] and result["attempts"] == 4
    assert result["readySimulationTimes"] == {"ground-a": 1.1}
    assert not result["motionCommandsSent"]
    assert all(path == "/snapshot" and payload is None for path, payload, _ in client.calls)


def test_stalled_or_missing_states_timeout_is_only_setup_failure():
    clock = ReadinessClock()
    client = SnapshotClient([ready_snapshot(1.)])
    result = wait_for_readiness(client, ["ground-a"], timeout=.5, clock=clock.clock, sleep=clock.sleep)
    assert not result["ready"] and result["failureType"] == "setup_failure"
    assert result["elapsedWallSeconds"] <= .5 + 1e-9
    assert not result["motionCommandsSent"]


def test_setup_failure_creates_zero_physical_trials(monkeypatch):
    monkeypatch.setattr(checks, "wait_for_readiness", lambda *a, **kw: {
        "ready": False, "failureType": "setup_failure", "motionCommandsSent": False})
    monkeypatch.setattr(checks, "run_trial", lambda *a, **kw: pytest.fail("motion trial must not start"))
    result = checks.run_campaign(SnapshotClient([{}]), ["ground-a", "ground-b", "uav-a"], io.StringIO())
    assert result["outcome"] == "setup_failure"
    assert result["trials"] == [] and not result["passed"]


def test_latched_robot_fails_setup_without_attempting_motion(monkeypatch):
    snapshot = ready_snapshot(1.)
    snapshot["stoppedRobots"] = ["ground-a"]
    client = SnapshotClient([snapshot])
    monkeypatch.setattr(checks, "run_trial", lambda *a, **kw: pytest.fail("latched robot must not move"))
    result = checks.run_campaign(client, ["ground-a"], io.StringIO())
    assert result["outcome"] == "setup_failure" and result["trials"] == []
    assert result["setup"]["latchedRobots"] == ["ground-a"]
    assert result["setup"]["motionCommandsSent"] is False
    assert len(client.calls) == 1 and client.calls[0][0] == "/snapshot"


def test_missing_latch_observation_cannot_claim_readiness():
    snapshot = ready_snapshot(1.)
    del snapshot["stoppedRobots"]
    clock = ReadinessClock()
    result = wait_for_readiness(SnapshotClient([snapshot]), ["ground-a"], timeout=.5,
                                clock=clock.clock, sleep=clock.sleep)
    assert not result["ready"] and not result["motionCommandsSent"]


def test_http_rejection_body_is_retained_and_reported(monkeypatch):
    journal = io.StringIO()
    client = checks.HubClient("http://127.0.0.1:18128", journal)
    body = b'{"error":"robot is latched stopped"}'

    def reject(*args, **kwargs):
        raise urllib.error.HTTPError("http://127.0.0.1:18128/goal", 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(client.opener, "open", reject)
    with pytest.raises(ValueError, match="robot is latched stopped"):
        client.call("/goal", {"robotId": "ground-a"})
    record = json.loads(journal.getvalue())
    assert record["httpStatus"] == 400 and record["responseBody"] == body.decode()
    assert record["payload"] == {"robotId": "ground-a"}


@pytest.mark.parametrize("field,value", [
    ("position", [float("nan"), 0, 0]), ("linearVelocity", [0, float("inf"), 0]),
    ("angularVelocity", [0, 0]), ("simulationTime", float("nan")),
    ("simulationTime", float("inf")), ("robotId", "foreign"),
    ("clockDomain", "wall"), ("worldId", "foreign"), ("frameId", "foreign"),
])
def test_invalid_or_foreign_stop_evidence_cannot_establish_dwell(field, value):
    trigger = state(1., 0., .3)
    samples = [state(1.1, .01, 0), state(1.5, .01, 0)]
    samples[-1][field] = value
    with pytest.raises(checks.MeasurementError):
        measured_stop(samples, trigger)


def test_missing_legacy_identity_preserves_metrics_without_claiming_verified_stop():
    trigger, first, last = state(1., 0, .3), state(1.1, .02, 0), state(1.5, .02, 0)
    del first["worldId"]
    result = measured_stop([first, last], trigger)
    assert not result["measuredStopped"] and not result["measurementIdentityVerified"]
    assert result["maximumDisplacementMeters"] == .02


def test_admission_rejection_is_not_counted_as_physical_stop_failure():
    class RejectingClient:
        def call(self, path, payload=None, **kwargs):
            if path == "/snapshot":
                return ready_snapshot(1.)
            if path == "/goal":
                raise ValueError("fixture goal rejected")
            return {}

    result = checks.run_trial(RejectingClient(), "ground-a", "watchdog", io.StringIO())
    assert result["failureType"] == "admission_failure"
    assert result["goalAdmissionAttempted"] and not result["goalAdmitted"] and not result["stopTriggered"]


def test_trial_denominators_separate_skips_admission_and_actual_stops(monkeypatch):
    monkeypatch.setattr(checks, "wait_for_readiness", lambda *a, **kw: {"ready": True})
    monkeypatch.setattr(checks, "run_trial", lambda *a, **kw: {
        "passed": False, "goalAdmissionAttempted": True, "goalAdmitted": False, "stopTriggered": False})
    result = checks.run_campaign(None, ["ground-a", "ground-b", "uav-a"], io.StringIO())
    assert result["requestedTrialCount"] == 6 and len(result["trials"]) == 6
    assert result["goalAdmissionAttemptCount"] == 3 and result["skippedTrialCount"] == 3
    assert result["admittedTrialCount"] == result["physicallyTriggeredTrialCount"] == 0


def test_finite_inputs_cannot_overflow_into_nonfinite_stop_metrics():
    trigger = state(1., -1e308, .3)
    with pytest.raises(checks.MeasurementError, match="derived"):
        measured_stop([state(1.1, 1e308, 0)], trigger)
