from __future__ import annotations

import json

from tests.e2e.fleet_harness import FleetHandoffStack

pytest_plugins = ("tests.e2e.fleet_harness",)


def test_natural_language_handoff_records_policy_observation_and_harness_evidence(
    fleet_handoff_stack: FleetHandoffStack,
):
    stack = fleet_handoff_stack
    task_id = stack.create_and_approve()
    task = stack.wait_task(task_id)
    assert task["state"] == "SUCCEEDED", stack.log_tail()

    experience = stack.wait_experience(
        task_id,
        lambda current: (
            len(
                [
                    activity
                    for activity in current.get("activities", [])
                    if activity.get("controlMethod") == "仿真确定性策略"
                    and activity.get("controlStage") == "已由环境确认"
                ]
            )
            == 4
        ),
    )
    learned = [
        activity
        for activity in experience["activities"]
        if activity.get("controlMethod") == "仿真确定性策略"
    ]
    professional = [
        activity["policy"]
        for activity in experience["professional"]["activities"]
        if activity.get("policy")
    ]
    confirmed = [item for item in learned if item["controlStage"] == "已由环境确认"]
    unique_inferences = {item["inferenceId"] for item in professional}
    assert len(confirmed) == 4
    assert len(unique_inferences) == 4
    assert all(item["policyId"] == "tangying-simulation-handoff" for item in professional)
    assert all(item["manifestRevision"] and item["observationId"] for item in professional)
    assert {item["controlStage"] for item in learned} == {
        "安全动作已生成",
        "机器人执行",
        "等待环境确认",
        "已由环境确认",
    }
    wire = json.dumps(experience, ensure_ascii=False)
    assert "action_chunk" not in wire
    assert "left_arm_gripper.pos" not in wire

    world = stack.api("/v1/world")
    assert world["entities"]["red-block"]["relations"]["inside"] == "right-target-zone"
    assert world["resources"]["block:red-block"]["owner"] == "environment"
