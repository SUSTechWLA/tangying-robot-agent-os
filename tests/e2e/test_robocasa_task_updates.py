"""Real RoboCasa proof for a natural-language task update at a safe point."""

from __future__ import annotations

import importlib

from tests.e2e.fleet_harness import HANDOFF_PROMPT
from tests.e2e.robocasa_harness import start_robocasa_handoff_stack

UPDATE_PROMPT = "最后放到右侧蓝色垫子上"
harness = importlib.import_module("scripts.run_robocasa_harness")


def test_signed_update_evidence_requires_revision_preview_safe_point_and_harness_ids():
    evidence = {
        "schemaVersion": "tangying.robocasa-task-update.v1",
        "taskId": "task-1",
        "originalRequest": HANDOFF_PROMPT,
        "updateRequest": UPDATE_PROMPT,
        "proposal": {
            "proposal": {
                "status": "PROPOSED",
                "revision": {
                    "taskId": "task-1",
                    "revision": 2,
                    "baseRevision": 1,
                    "request": UPDATE_PROMPT,
                    "changeSet": {"retained": ["sender"], "changed": ["receiver"]},
                },
            }
        },
        "confirmation": {"revision": {"status": "WAITING_SAFE_POINT"}},
        "waitingExperience": {"revision": 2, "updateStatus": "WAITING_SAFE_POINT"},
        "finalExperience": {
            "schemaVersion": "task.experience.v1",
            "taskId": "task-1",
            "revision": 2,
            "updateStatus": "ACTIVE",
            "steps": [
                {"status": "SATISFIED"},
                {"status": "SATISFIED"},
                {"status": "SATISFIED"},
            ],
            "professional": {
                "stepEvidence": [{"stepId": "sender", "evidenceIds": ["observation-1"]}]
            },
        },
        "revisionHistory": {"currentRevision": 2, "revisions": [{}, {}]},
    }
    run = {"taskId": "task-1", "request": HANDOFF_PROMPT}
    task = {
        "id": "task-1",
        "request": UPDATE_PROMPT,
        "currentRevision": 2,
        "state": "SUCCEEDED",
        "intent": {
            "sequence": [
                {"action": "prepare_simulation"},
                {"action": "pick_and_place"},
                {"action": "pick_and_place"},
            ]
        },
    }
    browser = {
        "taskUpdate": {
            "updateRequest": UPDATE_PROMPT,
            "previewVisible": True,
            "waitingSafePointVisible": True,
            "finalRevision": 2,
        }
    }

    assert harness._versioned_task_update_valid(evidence, run, task, browser)
    evidence["finalExperience"]["professional"]["stepEvidence"] = []
    assert not harness._versioned_task_update_valid(evidence, run, task, browser)


def test_policy_evidence_requires_four_confirmed_physical_commands_and_redacted_actions():
    policy = {
        "policyId": "tangying-simulation-handoff",
        "manifestRevision": "manifest-1",
        "observationId": "observation-1",
    }
    commands = [
        ("sender-pick", "manipulation.pick"),
        ("sender-place", "manipulation.place"),
        ("receiver-pick", "manipulation.pick"),
        ("receiver-place", "manipulation.place"),
    ]
    experience = {
        "activities": [{"controlMethod": "", "controlStage": ""}] + [
            {
                "controlMethod": "仿真确定性策略",
                "controlStage": "已由环境确认",
            }
            for _command_id, _tool_name in commands
        ],
        "professional": {
            "activities": [{"toolName": "simulation.reset_episode", "commandId": "reset"}]
            + [
                {
                    "toolName": tool_name,
                    "commandId": command_id,
                    "policy": {**policy, "inferenceId": f"inference-{index}"},
                }
                for index, (command_id, tool_name) in enumerate(commands)
            ],
        },
    }

    assert harness._policy_tool_evidence_valid(experience)
    experience["professional"]["activities"][0]["action_chunk"] = [{"joint": 1}]
    assert not harness._policy_tool_evidence_valid(experience)


def test_policy_evidence_allows_failed_retry_but_requires_each_physical_command_confirmation():
    policy = {
        "policyId": "tangying-simulation-handoff",
        "manifestRevision": "manifest-1",
        "observationId": "observation-1",
    }
    commands = [
        ("sender-pick", "manipulation.pick"),
        ("sender-place", "manipulation.place"),
        ("receiver-pick", "manipulation.pick"),
        ("receiver-place", "manipulation.place"),
    ]
    experience = {
        "activities": [
            {"controlMethod": "仿真确定性策略", "controlStage": "动作已停止"},
            *[
                {"controlMethod": "仿真确定性策略", "controlStage": "已由环境确认"}
                for _command_id, _tool_name in commands
            ],
        ],
        "professional": {
            "activities": [
                {
                    "toolName": "manipulation.pick",
                    "commandId": "sender-pick",
                    "policy": {**policy, "inferenceId": "failed-inference"},
                },
                *[
                    {
                        "toolName": tool_name,
                        "commandId": command_id,
                        "policy": {**policy, "inferenceId": f"confirmed-{index}"},
                    }
                    for index, (command_id, tool_name) in enumerate(commands)
                ],
            ]
        },
    }

    assert harness._policy_tool_evidence_valid(experience)
    experience["activities"][-1]["controlStage"] = "等待环境确认"
    assert not harness._policy_tool_evidence_valid(experience)


def test_mid_execution_update_preserves_sender_evidence_and_replans_receiver(tmp_path):
    stack = start_robocasa_handoff_stack(tmp_path, human_speed=0.02)
    try:
        task_id = stack.create_and_approve(HANDOFF_PROMPT)
        running = stack.wait_experience_step(task_id, step_index=0, status="RUNNING")

        proposal = stack.propose_update(task_id, running["revision"], UPDATE_PROMPT)
        proposed_revision = proposal["proposal"]["revision"]
        assert proposed_revision["changeSet"]["retained"]
        confirmed = stack.confirm_update(
            task_id,
            proposed_revision["revision"],
            running["revision"],
        )
        assert confirmed["revision"]["status"] == "WAITING_SAFE_POINT"
        waiting = stack.experience(task_id)
        assert waiting["revision"] == 2
        assert waiting["updateStatus"] == "WAITING_SAFE_POINT"
        assert waiting["recovery"]["robotSafetyState"].startswith("机器人正在完成")

        final_task = stack.wait_task(task_id)
        final = stack.wait_experience(
            task_id,
            lambda value: (
                value.get("revision") == 2
                and all(step.get("status") == "SATISFIED" for step in value.get("steps", []))
            ),
            timeout=120,
        )
        world = stack.wait_world(
            lambda value: (
                value.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside")
                == "right-target-zone"
            ),
            timeout=30,
        )

        assert final_task["currentRevision"] == 2
        assert final["steps"][0]["status"] == "SATISFIED"
        assert final["professional"]["stepEvidence"], final
        assert final["activities"], "the user view must retain real tool activity"
        assert harness._policy_tool_evidence_valid(final), final
        assert world["resources"]["block:red-block"]["owner"] == "environment"
        assert world["resources"]["block:red-block"]["fencingToken"] == 3
    finally:
        stack.stop()
