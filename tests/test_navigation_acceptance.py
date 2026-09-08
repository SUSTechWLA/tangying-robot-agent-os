"""Offline lifecycle checks for the live navigation acceptance client."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import URLError

import pytest

SPEC = importlib.util.spec_from_file_location(
    "navigation_acceptance", Path(__file__).parents[1] / "scripts/run_navigation_acceptance.py"
)
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)
GOAL_POSE = [0, .05, .035, 2 ** -.5, 0, 0, 2 ** -.5]


class SimulationAPI:
    def __init__(self, *, pause=False, shared_capture=False, failure=None):
        self.pause = pause
        self.failure = failure
        self.now = 0.0
        self.calls = []
        self.paused_at = None
        self.resumed_at = None
        self.cancelled = False
        self.failed_poll = False
        self.records = []
        self.details = {}
        self.images = {}
        self.events = []
        steps = ["observe", "resolve", "navigate", "observe_after_navigation", "plan_grasp",
                 "pick", "verify_grasp", "place", "verify_place"]
        ordered = [(prefix + "-" + step, step) for prefix in ("task01", "task02") for step in steps]
        if pause:
            ordered[3:3] = [("task01-observe", "observe"), ("task01-resolve", "resolve")]
        for index, (step_id, step) in enumerate(ordered):
            capture_id = "capture-0" if shared_capture and index == 1 else f"capture-{index}"
            record_id = f"record-{index}"
            record = {"id": record_id, "stepId": step_id, "captureId": capture_id}
            for kind in ("rgb", "depth"):
                image = f"{record_id}-{kind}-original".encode()
                self.images[record_id, kind] = image
                record[kind + "Sha256"] = hashlib.sha256(image).hexdigest()
            detail = {**record, "observedAtUnixMs": 1000 + index, "snapshot": {
                "robotState": {
                    "navigation": {"passed": True, "position_error_m": 0, "yaw_error_rad": 0,
                                   "pose_source": "sim_proprioceptive_odom", "base_pose": GOAL_POSE.copy(),
                                   "goal_pose": GOAL_POSE.copy(), "observed_at_unix_ms": 1000 + index,
                                   "map_receipt": {"completion_source": "nav2_action" if step_id.startswith("task01-") else "pose_confirmation",
                                                   "checked_at_unix_ms": 1000 + index,
                                                   "pose_observed_at_unix_ms": 950 + index,
                                                   "completion_pose_observed_at_unix_ms": 940 + index,
                                                   "pose_source": "rtabmap_tf", "map_pose": GOAL_POSE.copy(),
                                                   "goal_pose_map": GOAL_POSE.copy(), "position_error_m": 0,
                                                   "yaw_error_rad": 0, "goal_id": f"goal-{index}", "map_revision": "map-1"}},
                    "verification": {"passed": True, "sample_count": 3, "stable_duration_s": .1,
                                     "max_displacement_m": 0, "observation_id": capture_id}},
                "reconstruction": {"entities": [
                    {"entityId": "red-cup", "relation": "inside:right-bin"},
                    {"entityId": "blue-bottle", "relation": "inside:front-tray"}]}}}
            self.records.append(record)
            self.details[record_id] = detail
            self.events.append({"type": "TOOL_ACTIVITY", "stepId": step_id, "payload": {
                "activityStatus": "CONFIRMED", "toolName": {
                    "navigate": "navigation.navigate", "pick": "manipulation.pick",
                    "place": "manipulation.place"}.get(step, step),
                "commandId": f"command-{index}", "evidenceSource": "command_observation",
                "receiptObservationId": capture_id, "evidenceIds": [capture_id]}})

    def sleep(self, seconds):
        self.now += 60 if self.failure == "timeout" else seconds

    def urlopen(self, request, timeout):
        url = request if isinstance(request, str) else request.full_url
        path = url.removeprefix("http://127.0.0.1:8787")
        self.calls.append(path)
        if path.startswith("/v1/telemetry?"):
            value = {"latest": {"robotProfile": {"adapterId": "mujoco", "modelId": "xlerobot-rgbd-reference"},
                                "robotState": {"perception": {"mode": "rgbd"}, "base_pose": [0, -.6, *GOAL_POSE[2:]],
                                               "navigation": {"backend": "rtabmap_nav2", "approach_goal_pose": GOAL_POSE}}}}
        elif path == "/v1/navigation/map":
            value = {"ready": True, "poseSource": "rtabmap_tf", "mode": "mapping"}
        elif path == "/v1/tasks":
            value = {"id": "owned-task", "state": "AWAITING_APPROVAL", "events": []}
        elif path.endswith("/approve"):
            if self.failure == "approve_response":
                raise URLError("approval applied, response lost")
            value = {}
        elif path.endswith("/cancel"):
            if self.failure == "cancel_error":
                raise URLError("cancellation endpoint unavailable")
            self.cancelled = True
            value = {"state": "CANCELLED"}
        elif path.endswith("/pause"):
            self.paused_at = self.now
            value = {}
        elif path.endswith("/resume"):
            self.resumed_at = self.now
            value = {}
        elif path.endswith("/recovery"):
            value = {"canResume": True, "state": "PAUSED"}
        elif path == "/v1/tasks/owned-task":
            if self.failure in {"poll", "cancel_error", "interrupt"} and not self.failed_poll:
                self.failed_poll = True
                if self.failure == "interrupt":
                    raise KeyboardInterrupt("operator interrupted acceptance")
                raise URLError("poll disconnected")
            state, events = "SUCCEEDED", self.events
            if self.cancelled:
                state = "CANCELLED"
            elif self.failure == "terminal":
                state = "FAILED"
            elif self.failure:
                state = "EXECUTING"
            elif self.pause and self.resumed_at is None:
                state = "PAUSED" if self.paused_at is not None else "EXECUTING"
                events = self.events[:3]
                if state == "EXECUTING":
                    events = [*events[:2], {"type": "TOOL_ACTIVITY", "stepId": "task01-navigate",
                                           "payload": {"activityStatus": "RUNNING"}}]
            value = {"id": "owned-task", "state": state, "events": events}
        elif path.endswith("/observations?limit=200"):
            value = {"records": list(reversed(self.records))}
        elif path.endswith(("/rgb", "/depth")):
            record_id, kind = path.split("/")[-2:]
            return io.BytesIO(self.images[record_id, kind])
        else:
            value = self.details[path.rsplit("/", 1)[1]]
        return io.BytesIO(json.dumps(value).encode())


def setup_api(monkeypatch, **kwargs):
    api = SimulationAPI(**kwargs)
    monkeypatch.setattr(acceptance, "urlopen", api.urlopen)
    monkeypatch.setattr(acceptance.time, "monotonic", lambda: api.now)
    monkeypatch.setattr(acceptance.time, "sleep", api.sleep)
    return api


@pytest.mark.parametrize("shared_capture", [False, True])
def test_complete_task_retains_all_original_step_scoped_evidence(tmp_path, monkeypatch, shared_capture):
    api = setup_api(monkeypatch, shared_capture=shared_capture)
    output = tmp_path / "run"
    acceptance.run("http://127.0.0.1:8787", output)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["passed"] and summary["confirmedSteps"] == summary["confirmedEvents"] == 18
    assert summary["verifiedImageHashes"] == 36
    assert [call["completionSource"] for call in summary["navigationCalls"]] == ["nav2_action", "pose_confirmation"]
    assert [call["motionExecuted"] for call in summary["navigationCalls"]] == [True, False]
    assert summary["physicalToolCalls"] == 6
    assert summary["motionToolSteps"] == ["task01-navigate", "task01-pick", "task01-place", "task02-pick", "task02-place"]
    assert summary["poseConfirmationSteps"] == ["task02-navigate"]
    for (record_id, kind), image in api.images.items():
        assert (output / f"{record_id}-{kind}.png").read_bytes() == image
    assert not api.cancelled


def test_long_pause_refreshes_reads_without_repeating_physical_steps(tmp_path, monkeypatch):
    api = setup_api(monkeypatch, pause=True)
    output = tmp_path / "run"
    acceptance.run("http://127.0.0.1:8787", output, 65)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["confirmedSteps"] == 18 and summary["confirmedEvents"] == 20
    assert summary["pauseSeconds"] == 65 and summary["verifiedImageHashes"] == 40
    assert api.resumed_at - api.paused_at >= 65
    assert api.calls.count("/v1/tasks/owned-task/resume") == 1
    assert (output / "paused-task.json").is_file() and (output / "paused-recovery.json").is_file()


@pytest.mark.parametrize("target,field,value,match", [
    ("task01", "completion_source", "pose_confirmation", "first navigation must execute Nav2"),
    ("task02", "completion_source", "nav2_action", "second navigation must confirm the existing pose"),
    ("task02", "completion_source", "", "second navigation must confirm the existing pose"),
    ("task02", "pose_observed_at_unix_ms", 1, "localization receipt is stale"),
    ("task02", "completion_pose_observed_at_unix_ms", 0, "completion localization is stale"),
    ("task02", "completion_pose_observed_at_unix_ms", 2000, "completion localization is stale"),
    ("task02", "pose_source", "sim_truth", "RTAB-Map pose source"),
    ("task02", "position_error_m", .02, "map position"),
    ("task02", "yaw_error_rad", .05, "map yaw"),
])
def test_navigation_source_and_receipt_freshness_are_required(tmp_path, monkeypatch, target, field, value, match):
    api = setup_api(monkeypatch)
    detail = next(detail for detail in api.details.values() if detail["stepId"] == target + "-navigate")
    detail["snapshot"]["robotState"]["navigation"]["map_receipt"][field] = value
    output = tmp_path / "run"
    with pytest.raises(AssertionError, match=match):
        acceptance.run("http://127.0.0.1:8787", output)
    assert (output / "failure.json").is_file() and not (output / "summary.json").exists()
    assert not api.cancelled


@pytest.mark.parametrize("field,value,match", [
    ("position_error_m", .02, "independent position"),
    ("yaw_error_rad", .05, "independent yaw"),
    ("base_pose", [0, .10, *GOAL_POSE[2:]], "independent position"),
    ("base_pose", [0, .05, .035, 1, 0, 0, 0], "independent yaw"),
    ("pose_source", "rtabmap_tf", "independent odometry"),
    ("observed_at_unix_ms", 999, "capture time"),
])
def test_pose_confirmation_cannot_bypass_independent_capture_checks(tmp_path, monkeypatch, field, value, match):
    api = setup_api(monkeypatch)
    detail = next(detail for detail in api.details.values() if detail["stepId"] == "task02-navigate")
    detail["snapshot"]["robotState"]["navigation"][field] = value
    with pytest.raises(AssertionError, match=match):
        acceptance.run("http://127.0.0.1:8787", tmp_path / "run")


def test_navigation_and_manipulation_tool_classifications_cannot_be_substituted(tmp_path, monkeypatch):
    api = setup_api(monkeypatch)
    event = next(event for event in api.events if event["stepId"] == "task02-navigate")
    event["payload"]["toolName"] = "manipulation.pick"
    with pytest.raises(AssertionError, match="physical tool classifications"):
        acceptance.run("http://127.0.0.1:8787", tmp_path / "run")


@pytest.mark.parametrize("failure,match", [
    ("approve_response", "approval applied"), ("poll", "poll disconnected"), ("timeout", "timed out")])
def test_uncertain_response_or_timeout_cancels_only_owned_task_and_retains_evidence(
        tmp_path, monkeypatch, failure, match):
    api = setup_api(monkeypatch, failure=failure)
    output = tmp_path / "run"
    with pytest.raises((URLError, AssertionError, TimeoutError), match=match):
        acceptance.run("http://127.0.0.1:8787", output)
    assert api.calls.count("/v1/tasks") == api.calls.count("/v1/tasks/owned-task/approve") == 1
    assert api.calls.count("/v1/tasks/owned-task/cancel") == 1
    assert not any(path.endswith("/resume") for path in api.calls)
    assert json.loads((output / "final-task.json").read_text())["state"] == "CANCELLED"
    assert (output / "observation-index.json").is_file()
    assert (output / "record-0-rgb.png").read_bytes() == api.images["record-0", "rgb"]
    assert match in json.loads((output / "failure.json").read_text())["message"]
    assert not (output / "summary.json").exists()


def test_interruption_stops_owned_task_and_retains_journal(tmp_path, monkeypatch):
    api = setup_api(monkeypatch, failure="interrupt")
    output = tmp_path / "run"
    with pytest.raises(KeyboardInterrupt, match="operator interrupted"):
        acceptance.run("http://127.0.0.1:8787", output)
    assert api.cancelled and (output / "observation-index.json").is_file()
    assert json.loads((output / "failure.json").read_text())["errorType"] == "KeyboardInterrupt"


def test_cleanup_failure_does_not_hide_original_error_or_retry_mutations(tmp_path, monkeypatch):
    api = setup_api(monkeypatch, failure="cancel_error")
    output = tmp_path / "run"
    with pytest.raises(URLError, match="poll disconnected"):
        acceptance.run("http://127.0.0.1:8787", output)
    failure = json.loads((output / "failure.json").read_text())
    assert failure["cancellationRequested"]
    assert any("cancellation endpoint unavailable" in error["error"] for error in failure["cleanupErrors"])
    assert api.calls.count("/v1/tasks/owned-task/cancel") == 1
    assert json.loads((output / "final-task.json").read_text())["state"] == "EXECUTING"
    assert (output / "record-0-rgb.png").is_file()


def test_terminal_failure_retains_evidence_without_cancelling_completed_task(tmp_path, monkeypatch):
    api = setup_api(monkeypatch, failure="terminal")
    output = tmp_path / "run"
    with pytest.raises(AssertionError):
        acceptance.run("http://127.0.0.1:8787", output)
    assert not api.cancelled
    assert json.loads((output / "final-task.json").read_text())["state"] == "FAILED"
    assert (output / "record-0-depth.png").is_file()
    assert not (output / "summary.json").exists()


def test_bad_hash_fails_acceptance_but_retains_corrupt_bytes_and_other_frames(tmp_path, monkeypatch):
    api = setup_api(monkeypatch)
    api.images["record-17", "rgb"] = b"corrupt-original-response"
    output = tmp_path / "run"
    with pytest.raises(AssertionError, match="hash mismatch"):
        acceptance.run("http://127.0.0.1:8787", output)
    assert (output / "record-17-rgb.png").read_bytes() == b"corrupt-original-response"
    assert (output / "record-0-rgb.png").read_bytes() == api.images["record-0", "rgb"]
    assert not api.cancelled and not (output / "summary.json").exists()


def test_existing_output_directory_never_creates_or_approves_task(tmp_path, monkeypatch):
    api = setup_api(monkeypatch)
    with pytest.raises(FileExistsError):
        acceptance.run("http://127.0.0.1:8787", tmp_path)
    assert api.calls == []
