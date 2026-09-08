from __future__ import annotations

from tests.e2e.fleet_harness import FleetHandoffStack

pytest_plugins = ("tests.e2e.fleet_harness",)


def test_natural_language_shared_block_handoff_is_world_verified(
    fleet_handoff_stack: FleetHandoffStack,
):
    stack = fleet_handoff_stack
    initial = stack.api("/v1/world")
    task_id = stack.create_and_approve()

    task = stack.wait_task(task_id)
    assert task["state"] == "SUCCEEDED", stack.log_tail()
    task = stack.wait_task_projection(
        task_id,
        lambda current: (
            sum(event["type"] == "INTENT_SUCCEEDED" for event in current.get("events", []))
            == 2
            and sum(
                event["type"] == "TOOL_ACTIVITY"
                and event.get("payload", {}).get("activityStatus") == "CONFIRMED"
                for event in current.get("events", [])
            )
            == 14
        ),
    )

    intents = stack.api(f"/v1/tasks/{task_id}/intents")["intents"]
    assert [node["status"] for node in intents] == ["SUCCEEDED", "SUCCEEDED"]
    assert [node["claimed"] for node in intents] == ["robot-1", "robot-2"]
    assert intents[0]["predicateState"] == "VERIFIED"
    assert intents[1]["predicateState"] == "BLOCK_DELIVERED"
    assert intents[1]["fencingToken"] == intents[0]["fencingToken"] + 1

    world = stack.wait_world(
        lambda snapshot: (
            snapshot.get("entities", {}).get("red-block", {}).get("relations", {}).get("inside")
            == "right-target-zone"
            and snapshot.get("resources", {}).get("block:red-block", {}).get("owner")
            == "environment"
        )
    )
    assert world["revision"] > initial["revision"]
    assert world["eventCursor"]
    assert world["robots"]["robot-1"].get("held", "") == ""
    assert world["robots"]["robot-2"].get("held", "") == ""
    assert world["resources"]["block:red-block"]["fencingToken"] == intents[1]["fencingToken"] + 1

    domain_events = stack.api(f"/v1/tasks/{task_id}/domain-events")
    event_types = [event["eventType"] for event in domain_events]
    assert event_types.count("BLOCK_AVAILABLE") == 1, domain_events
    assert event_types.count("BLOCK_DELIVERED") == 1, domain_events
    assert [event["aggregateVersion"] for event in domain_events] == list(
        range(1, len(domain_events) + 1)
    )

    task_events = [event["type"] for event in task.get("events", [])]
    assert task_events.count("INTENT_STARTED") == 2
    assert task_events.count("INTENT_SUCCEEDED") == 2
    tool_events = [event for event in task["events"] if event["type"] == "TOOL_ACTIVITY"]
    assert len(tool_events) == 56
    assert {
        status: sum(event["payload"]["activityStatus"] == status for event in tool_events)
        for status in ("SENDING", "RUNNING", "AWAITING_EVIDENCE", "CONFIRMED")
    } == {
        "SENDING": 14,
        "RUNNING": 14,
        "AWAITING_EVIDENCE": 14,
        "CONFIRMED": 14,
    }


def test_authoritative_world_contains_live_harness_inputs(
    fleet_handoff_stack: FleetHandoffStack,
):
    required_sources = {
        "robot-1/proprioception",
        "robot-1/scene",
        "robot-2/proprioception",
        "robot-2/scene",
    }
    # Fixture readiness describes an earlier HTTP snapshot. Under load those
    # inputs can expire before this fetch; assert against one fresh snapshot,
    # while retaining the harness deadline and the original freshness budget.
    world = fleet_handoff_stack.wait_world(
        lambda snapshot: required_sources.issubset(snapshot.get("sources", {}))
        and all(source.get("freshness") == "FRESH" for source in snapshot["sources"].values())
    )
    assert set(world["robots"]) == {"robot-1", "robot-2"}
    assert required_sources.issubset(world["sources"])
    assert all(source["freshness"] == "FRESH" for source in world["sources"].values())
    assert world["entities"]["red-block"]["evidence"]["sourceId"] == "robot-1/scene"
    assert all(
        robot["evidence"]["transformRevision"] == "handoff-world-v1"
        for robot in world["robots"].values()
    )
