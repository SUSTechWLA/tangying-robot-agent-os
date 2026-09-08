import importlib.util
import io
import json
import signal
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

SPEC = importlib.util.spec_from_file_location(
    "navigation_source_fault", Path(__file__).parents[1] / "scripts/run_navigation_source_fault.py")
fault = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fault)


def proc_entry(root, pid, *, start="1234", executable=fault.BRIDGE):
    entry = root / str(pid)
    entry.mkdir(exist_ok=True)
    (entry / "cmdline").write_bytes(b"python3\0" + executable.encode() + b"\0--ros-args\0")
    (entry / "stat").write_text(f"{pid} (runtime rgbd) " + " ".join(["S"] + ["0"] * 18 + [start]))


def test_only_exact_unique_installed_bridge_is_selected(tmp_path):
    proc_entry(tmp_path, 10, executable="unrelated_" + fault.BRIDGE)
    with pytest.raises(ValueError, match="exactly one"):
        fault.bridge_identity(tmp_path)
    proc_entry(tmp_path, 20)
    assert fault.bridge_identity(tmp_path) == {"pid": 20, "startTicks": "1234", "state": "S"}
    proc_entry(tmp_path, 21)
    with pytest.raises(ValueError, match="exactly one"):
        fault.bridge_identity(tmp_path)


def test_pid_reuse_blocks_signals_and_owned_resume_uses_same_start_time(tmp_path):
    proc_entry(tmp_path, 20)
    identity = fault.bridge_identity(tmp_path)
    signals = []
    kill = lambda *args: signals.append(args)
    fault.signal_bridge("stop", identity, proc=tmp_path, kill=kill)
    fault.signal_bridge("continue", identity, proc=tmp_path, kill=kill)
    assert signals == [(20, signal.SIGSTOP), (20, signal.SIGCONT)]
    proc_entry(tmp_path, 20, start="5678")
    with pytest.raises(ValueError, match="identity changed"):
        fault.signal_bridge("continue", identity, proc=tmp_path, kill=kill)
    assert len(signals) == 2


def observation(y=-.6):
    return {"robotProfile": {"adapterId": "mujoco", "modelId": "xlerobot-rgbd-reference"},
            "reconstruction": {"sourceId": "robot/head-rgbd", "sourceType": "rgbd_camera"},
            "robotState": {"perception": {"mode": "rgbd"},
                           "navigation": {"backend": "rtabmap_nav2", "approach_goal_pose": [0, .05, 0, 1, 0, 0, 0]},
                           "base_pose": [0, y, 0, 1, 0, 0, 0]}}


def test_hardware_profile_and_other_compose_project_are_refused(monkeypatch):
    value = observation()
    value["robotProfile"]["adapterId"] = "ros2-real"
    with pytest.raises(ValueError, match="restricted"):
        fault.validate_profile(value)
    monkeypatch.setattr(fault.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps([
        {"Id": "other-id", "State": {"Running": True}, "Config": {"Labels": {
            "com.docker.compose.project": "other-project", "com.docker.compose.service": "navigation"}}}])))
    with pytest.raises(ValueError, match="compose container"):
        fault.container_identity("other")


def test_monitor_failure_cancels_only_supplied_task_and_restores_bridge(monkeypatch, tmp_path):
    operations, cancelled = [], []
    samples = 0
    lost_response = False
    task = {"adapter": "mujoco", "state": "RUNNING", "events": [{
        "type": "TOOL_ACTIVITY", "stepId": "task01-navigate", "payload": {
            "toolName": "navigation.navigate", "activityStatus": "RUNNING"}}]}

    def request(req, **_kwargs):
        nonlocal samples, lost_response
        path = urlsplit(req.full_url).path
        if path == "/v1/telemetry":
            body = {"latest": observation(-.6 if samples == 0 else -.56)}
            samples += 1
        elif path.endswith("/cancel"):
            cancelled.append(path)
            body = {**task, "state": "CANCELLED"}
        elif path == "/v1/tasks/explicit-task":
            if "stop" in operations and not lost_response:
                lost_response = True
                raise OSError("simulated lost monitor response")
            body = task
        else:
            raise AssertionError(path)
        return io.BytesIO(json.dumps(body).encode())

    def control(_container, operation, identity=None):
        operations.append(operation)
        return {"pid": 20, "startTicks": "1234", "state": "S"}

    monkeypatch.setattr(fault, "urlopen", request)
    monkeypatch.setattr(fault, "container_identity", lambda _name: "immutable-container-id")
    monkeypatch.setattr(fault, "control", control)
    monkeypatch.setattr(fault.time, "sleep", lambda _seconds: None)
    with pytest.raises(OSError, match="lost monitor"):
        fault.run("http://127.0.0.1:18130", "explicit-task", tmp_path / "evidence", "container")
    assert operations == ["inspect", "stop", "continue"]
    assert cancelled == ["/v1/tasks/explicit-task/cancel"]
    assert (tmp_path / "evidence/source-restored.json").exists()


def test_stationary_confirmation_is_not_misclassified_as_active_motion():
    event = {"type": "TOOL_ACTIVITY", "stepId": "task01-navigate", "payload": {
        "toolName": "navigation.navigate", "activityStatus": "RUNNING"}}
    task = {"events": [event, {**event, "payload": {**event["payload"], "activityStatus": "CONFIRMED"}}]}
    assert fault.navigation_activity(task) is None
    assert fault.pose_drift([[0, 0, 0, 1, 0, 0, 0], [0, 0, 0, -1, 0, 0, 0]]) == (0, 0)


def test_failure_gate_detects_new_pick_and_place_and_map_read_is_whitelisted(monkeypatch):
    for tool in ("manipulation.pick", "manipulation.place"):
        task = {"events": [{"sequence": 21, "type": "TOOL_ACTIVITY", "payload": {"toolName": tool}}]}
        assert fault.no_manipulation_after(task, 21)
        assert not fault.no_manipulation_after(task, 20)
    monkeypatch.setenv("TANGYING_NAVIGATION_TOKEN", "private-container-test-token")
    monkeypatch.setattr(fault, "urlopen", lambda request, **_kwargs: io.BytesIO(json.dumps({
        "ready": False, "readinessBlockers": ["BASE_RGBD_STALE"], "inputAgeMs": {"base": 1500},
        "token": "private-container-test-token", "cells": [1, 2, 3]}).encode()))
    result = fault.map_diagnostics()
    assert result["readinessBlockers"] == ["BASE_RGBD_STALE"]
    assert "cells" not in result and "token" not in result


def test_stop_proof_requires_new_original_frames_not_cached_unchanged_pose():
    value = observation()
    value["reconstruction"] = {"observationId": "capture-1", "sourceId": "head-rgbd",
                               "sourceType": "rgbd_camera", "observedAtUnixMs": 1000}
    sample = fault.native_sample(value, 1050)
    with pytest.raises(ValueError, match="increasing original captures"):
        fault.validate_stopped_samples([sample] * 11)
    newer = {**sample, "captureId": "capture-2", "observedAtUnixMs": 1600, "checkedAtUnixMs": 1650}
    assert fault.validate_stopped_samples([sample, newer]) == (0, 0)
    with pytest.raises(ValueError, match="fresh original"):
        fault.native_sample(value, 2001)
    moving = {**newer, "basePose": [.003, -.6, 0, 1, 0, 0, 0]}
    with pytest.raises(ValueError, match="continued moving"):
        fault.validate_stopped_samples([sample, moving])


@pytest.mark.parametrize("invalid", [None, "source", "stale", "hardware"])
def test_stop_sampling_requests_bound_live_camera_and_preserves_original_frame(monkeypatch, invalid):
    value = observation(-.42)
    value["reconstruction"].update(observationId="original-camera-frame", observedAtUnixMs=1000)
    if invalid == "source":
        value["reconstruction"]["sourceId"] = "robot/other-rgbd"
    if invalid == "hardware":
        value["robotProfile"]["adapterId"] = "real-robot"
    monkeypatch.setattr(fault.time, "time", lambda: 2.001 if invalid == "stale" else 1.05)
    requests = []

    def api(path):
        requests.append(path)
        parsed = urlsplit(path)
        assert parsed.path == "/v1/scene/camera"
        assert parse_qs(parsed.query) == {"adapter": ["mujoco"], "sourceId": ["robot/head-rgbd"]}
        return {"snapshot": value, "rgbDataUrl": "original-image", "depthDataUrl": "original-depth"}

    if invalid:
        with pytest.raises(ValueError):
            fault.camera_sample(api, "robot/head-rgbd")
    else:
        sample = fault.camera_sample(api, "robot/head-rgbd")
        assert sample["captureId"] == "original-camera-frame"
        assert sample["observedAtUnixMs"] == 1000 and sample["checkedAtUnixMs"] == 1050
        assert sample["basePose"] == value["robotState"]["base_pose"]
    assert len(requests) == 1
