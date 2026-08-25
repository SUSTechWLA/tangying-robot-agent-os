from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.e2e.robocasa_harness import RoboCasaHandoffStack

pytest_plugins = ("tests.e2e.robocasa_harness",)


def test_chinese_handoff_is_harness_verified(robocasa_stack: RoboCasaHandoffStack):
    initial = robocasa_stack.api("/v1/world")
    task_id = robocasa_stack.create_and_approve()

    task = robocasa_stack.wait_task(task_id)
    assert task["state"] == "SUCCEEDED", robocasa_stack.log_tail()
    intents = robocasa_stack.api(f"/v1/tasks/{task_id}/intents")["intents"]
    assert [intent["harnessStatus"] for intent in intents] == ["SATISFIED", "SATISFIED"]
    assert all(intent["harnessEvidenceIds"] for intent in intents)
    world = robocasa_stack.wait_world(
        lambda snapshot: (
            snapshot.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside")
            == "right-target-zone"
        )
    )
    assert world["revision"] > initial["revision"]
    assert set(world["robots"]) >= {"robot-1", "robot-2"}
    for robot_id in ("robot-1", "robot-2"):
        joints = {
            key: value
            for key, value in world["robots"][robot_id].get("state", {}).items()
            if key.startswith("joint.")
        }
        assert len(joints) >= 12, (robot_id, joints)
        assert all(
            isinstance(value, (int, float)) and math.isfinite(value) for value in joints.values()
        )
    assert world["resources"]["block:red-block"]["owner"] == "environment"
    assert all(source["freshness"] == "FRESH" for source in world["sources"].values())
    events = robocasa_stack.api(f"/v1/tasks/{task_id}/domain-events")
    event_types = [event["eventType"] for event in events]
    assert event_types.count("BLOCK_AVAILABLE") == 1
    assert event_types.count("BLOCK_DELIVERED") == 1


def test_same_stack_completes_two_new_rgbd_episodes(robocasa_stack: RoboCasaHandoffStack):
    first = robocasa_stack.run_handoff(new_episode=True)
    second = robocasa_stack.run_handoff(new_episode=True)

    assert first.task["state"] == second.task["state"] == "SUCCEEDED"
    assert second.initial_capture["episodeId"] != first.initial_capture["episodeId"]
    assert (
        first.initial_world["entities"]["red-block"]["relations"]["inside"]
        == second.initial_world["entities"]["red-block"]["relations"]["inside"]
        == "left-start-zone"
    )
    for run in (first, second):
        assert run.final_world["entities"]["red-block"]["relations"]["inside"] == "right-target-zone"
        assert run.tool_names[:3] == [
            "simulation.reset_episode",
            "observe_scene",
            "verify_episode_ready",
        ]
        assert run.rgbd_advanced_for("robot-1")
        assert run.rgbd_advanced_for("robot-2")
        assert run.world_changed_while("manipulation.pick")
        assert run.world_changed_while("manipulation.place")


def test_task_update_does_not_reset_the_active_simulation_episode(
    robocasa_stack: RoboCasaHandoffStack,
):
    task_id = robocasa_stack.create_and_approve(new_episode=True)
    running = robocasa_stack.wait_experience_step(
        task_id, step_index=1, status="RUNNING", timeout=30
    )
    proposal = robocasa_stack.propose_update(
        task_id,
        int(running["revision"]),
        "最后放到右侧蓝色垫子上",
    )
    confirmation = robocasa_stack.confirm_update(
        task_id,
        int(proposal["proposal"]["revision"]["revision"]),
        int(running["revision"]),
    )
    assert confirmation["revision"]["status"] == "WAITING_SAFE_POINT"

    task = robocasa_stack.wait_task(task_id)
    experience = robocasa_stack.wait_experience(
        task_id,
        lambda projected: projected.get("revision") == 2
        and all(step.get("status") == "SATISFIED" for step in projected.get("steps", [])),
        timeout=120,
    )

    assert task["state"] == "SUCCEEDED", robocasa_stack.log_tail()
    assert experience["steps"][0]["status"] == "SATISFIED"
    reset_commands = {
        activity.get("commandId")
        for activity in experience["professional"]["activities"]
        if activity.get("toolName") == "simulation.reset_episode"
    }
    assert len(reset_commands) == 1
    world = robocasa_stack.api("/v1/world")
    assert world["entities"]["red-block"]["relations"]["inside"] == "right-target-zone"


def test_completed_episode_keeps_both_head_rgbd_cameras_live(
    robocasa_stack: RoboCasaHandoffStack,
):
    task_id = robocasa_stack.create_and_approve(new_episode=True)
    assert robocasa_stack.wait_task(task_id)["state"] == "SUCCEEDED"

    for robot_id in ("robot-1", "robot-2"):
        completed = robocasa_stack.latest_capture(robot_id)
        refreshed = robocasa_stack.wait_capture_after(
            robot_id, str(completed["captureId"])
        )
        assert refreshed["episodeId"] == completed["episodeId"]
        assert refreshed["sourceSequence"] > completed["sourceSequence"]
        assert refreshed["simulationStep"] == completed["simulationStep"]
