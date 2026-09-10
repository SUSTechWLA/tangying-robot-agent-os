from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.e2e.helpers import start_isolated_simulation_stack


@pytest.fixture
def sim_stack(tmp_path):
    # Camera perception: the deterministic ground-truth debug runtime does not
    # identify the observations it returns, so a physical write on it cannot be
    # confirmed from fresh evidence and fails closed by design.
    stack = start_isolated_simulation_stack(tmp_path, perception="rgbd")
    try:
        yield stack
    finally:
        stack.stop_and_assert_clean()


def test_live_stack_observes_scene_before_approval_and_completes_two_goals(sim_stack):
    initial = sim_stack.wait_for_telemetry()
    latest = initial["latest"]
    ids = {entity["entityId"] for entity in latest["entities"]}
    # The camera workcell reports the commissioned target containers and
    # objects; the robot's own body is filtered out of its own perception
    # rather than published as a scene entity.
    assert {"red-cup", "blue-bottle", "right-bin", "front-tray"} <= ids
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
    tool_events = [event for event in task["events"] if event["type"] == "TOOL_ACTIVITY"]
    assert len(tool_events) == 42
    assert {
        status: sum(event["payload"]["activityStatus"] == status for event in tool_events)
        for status in ("SENDING", "RUNNING", "CONFIRMED")
    } == {"SENDING": 14, "RUNNING": 14, "CONFIRMED": 14}
    assert [event for event in event_types if event != "TOOL_ACTIVITY"] == [
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

    # Intermediate world state is sampled through a 1 Hz telemetry observer
    # while the camera workcell finishes this task in a few seconds, so an
    # individual sample can miss a brief state: `held` in particular is derived
    # from grasp geometry and is only visible while the object is lifted. The
    # durable proof of the same facts is the task's own evidence below, where a
    # physical write without a fresh post-command observation fails closed.
    states = [sample.get("robotState", {}) for sample in samples]
    assert samples, "telemetry observer produced no samples during the task"
    observed_tools = {state.get("active_tool") for state in states if state.get("active_tool")}
    assert observed_tools, "no sample showed the robot holding an active tool"
    assert any(
        state.get("active_tool") and state.get("joint_positions") != initial_joints
        for state in states
    )
    assert any(state.get("placements", {}).get("red-cup") == "right-bin" for state in states)

    confirmed = [
        event for event in tool_events if event["payload"]["activityStatus"] == "CONFIRMED"
    ]
    assert len(confirmed) == 14
    for event in confirmed:
        payload = event["payload"]
        # Read-only tools carry their receipt; world-mutating tools must have
        # persisted post-command evidence proving the world actually changed.
        if payload["toolName"] in {
            "manipulation.pick",
            "manipulation.place",
            "navigation.navigate",
            "recover_to_safe_pose",
        }:
            evidence_ids = payload.get("evidenceIds") or []
            assert evidence_ids, f"{payload['toolName']} completed without evidence"
            assert payload.get("evidenceSource") in {"post_tool_observation", "command_observation"}
            assert payload.get("receiptObservationId"), (
                f"{payload['toolName']} did not name the runtime observation it was confirmed by"
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
