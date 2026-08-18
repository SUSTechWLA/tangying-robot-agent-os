from __future__ import annotations

import json
import subprocess
import time

import pytest

from tests.e2e.helpers import REPO, IsolatedSimulationStack, free_port


@pytest.fixture
def sim_stack(tmp_path):
    local_agent = tmp_path / "bin/local-agent"
    local_agent.parent.mkdir()
    built = subprocess.run(
        ["go", "build", "-o", str(local_agent), "./cmd/local-agent"],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert built.returncode == 0, built.stderr
    agent_port = free_port()
    robot_port = free_port()
    while robot_port == agent_port:
        robot_port = free_port()
    stack = IsolatedSimulationStack(
        agent_port=agent_port,
        robot_port=robot_port,
        artifacts_dir=tmp_path / "stack",
        local_agent=local_agent,
    )
    started = stack.run_lifecycle("start")
    assert started.returncode == 0, started.stdout + started.stderr
    try:
        yield stack
    finally:
        stopped = stack.run_lifecycle("stop")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr


def test_live_stack_observes_scene_before_approval_and_completes_two_goals(sim_stack):
    initial = sim_stack.wait_for_telemetry()
    latest = initial["latest"]
    ids = {entity["entityId"] for entity in latest["entities"]}
    assert {"xlerobot", "red-cup", "blue-bottle", "right-bin", "front-tray"} <= ids
    assert latest["robotState"]["model_revision"] == "3d14695e40c9c68229c0aacffca6053c75cd3eb6"

    frame, media_type = sim_stack.get_bytes("/v1/scene/frame?adapter=mujoco")
    assert media_type == "image/png"
    assert frame.startswith(b"\x89PNG\r\n\x1a\n")

    task = sim_stack.run_task("把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来")
    assert task["state"] == "SUCCEEDED", json.dumps(task, ensure_ascii=False)
    event_types = [event["type"] for event in task["events"]]
    assert event_types[:2] == ["TASK_CREATED", "TASK_APPROVED"]
    assert event_types.count("STATE_CHANGED") >= 5
    assert event_types[-1] == "LOCAL_RUN_SUCCEEDED"

    deadline = time.monotonic() + 10
    final = {}
    while time.monotonic() < deadline:
        final = sim_stack.get_json("/v1/telemetry?adapter=mujoco&limit=1")["latest"]
        placements = final["robotState"].get("placements", {})
        if placements.get("red-cup") == "right-bin" and placements.get("blue-bottle") == "front-tray":
            break
        time.sleep(0.1)
    assert final["activity"] == "IDLE"
    assert final["robotState"]["held"] == ""
    assert final["robotState"]["placements"]["red-cup"] == "right-bin"
    assert final["robotState"]["placements"]["blue-bottle"] == "front-tray"
    assert final["robotState"]["verification_confidence"] >= 0.7
