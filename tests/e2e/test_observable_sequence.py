from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.e2e.helpers import start_isolated_simulation_stack


@pytest.fixture
def sim_stack(tmp_path):
    stack = start_isolated_simulation_stack(tmp_path)
    try:
        yield stack
    finally:
        stack.stop_and_assert_clean()


def test_live_stack_observes_scene_before_approval_and_completes_two_goals(sim_stack):
    initial = sim_stack.wait_for_telemetry()
    latest = initial["latest"]
    ids = {entity["entityId"] for entity in latest["entities"]}
    assert {"xlerobot", "red-cup", "blue-bottle", "right-bin", "front-tray"} <= ids
    assert latest["robotState"]["model_revision"] == "3d14695e40c9c68229c0aacffca6053c75cd3eb6"
    assert latest["robotState"]["placements"] == {}
    initial_joints = latest["robotState"]["joint_positions"]

    frame, media_type = sim_stack.get_bytes("/v1/scene/frame?adapter=mujoco")
    assert media_type == "image/png"
    assert frame.startswith(b"\x89PNG\r\n\x1a\n")

    samples = []
    with ThreadPoolExecutor(max_workers=1) as executor:
        task_future = executor.submit(
            sim_stack.run_task,
            "把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来",
        )
        while not task_future.done():
            telemetry = sim_stack.get_json("/v1/telemetry?adapter=mujoco&limit=100")
            samples.extend(telemetry.get("history", []))
            time.sleep(0.005)
        task = task_future.result()
    samples.extend(
        sim_stack.get_json("/v1/telemetry?adapter=mujoco&limit=100").get("history", [])
    )
    assert task["state"] == "SUCCEEDED", json.dumps(task, ensure_ascii=False)
    event_types = [event["type"] for event in task["events"]]
    assert event_types == [
        "TASK_CREATED",
        "TASK_APPROVED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "LOCAL_RUN_SUCCEEDED",
    ]
    assert [event["message"] for event in task["events"] if event["type"] == "STATE_CHANGED"] == [
        "local execution started",
        "grounding and local planning started",
        "local physical execution started",
        "post-action verification completed",
        "closed-loop task succeeded",
    ]

    states = [sample.get("robotState", {}) for sample in samples]
    assert any(sample.get("activity") != "IDLE" for sample in samples)
    assert any(
        state.get("active_tool") and state.get("joint_positions") != initial_joints
        for state in states
    )
    assert any(state.get("held") == "red-cup" for state in states)
    assert any(
        state.get("placements") == {"red-cup": "right-bin"}
        and state.get("held") in {"", "blue-bottle"}
        for state in states
    )
    assert any(
        state.get("placements", {}).get("red-cup") == "right-bin"
        and state.get("held") == "blue-bottle"
        for state in states
    )

    deadline = time.monotonic() + 10
    final = {}
    while time.monotonic() < deadline:
        final = sim_stack.get_json("/v1/telemetry?adapter=mujoco&limit=1")["latest"]
        placements = final["robotState"].get("placements", {})
        if (
            placements.get("red-cup") == "right-bin"
            and placements.get("blue-bottle") == "front-tray"
            and final["activity"] == "IDLE"
        ):
            break
        time.sleep(0.1)
    assert final["activity"] == "IDLE"
    assert final["robotState"]["held"] == ""
    assert final["robotState"]["placements"]["red-cup"] == "right-bin"
    assert final["robotState"]["placements"]["blue-bottle"] == "front-tray"
    assert final["robotState"]["verification_confidence"] >= 0.7
