from __future__ import annotations

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
        lambda snapshot: snapshot.get("entities", {})
        .get("red-block", {})
        .get("relations", {})
        .get("inside")
        == "right-target-zone"
    )
    assert world["revision"] > initial["revision"]
    assert world["resources"]["block:red-block"]["owner"] == "environment"
    assert all(source["freshness"] == "FRESH" for source in world["sources"].values())
    events = robocasa_stack.api(f"/v1/tasks/{task_id}/domain-events")
    event_types = [event["eventType"] for event in events]
    assert event_types.count("BLOCK_AVAILABLE") == 1
    assert event_types.count("BLOCK_DELIVERED") == 1
