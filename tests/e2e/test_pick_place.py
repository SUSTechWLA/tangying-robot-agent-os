from __future__ import annotations

from tests.e2e.helpers import run_simulation_task


def test_natural_language_reaches_verified_simulation_result(tmp_path):
    finished = run_simulation_task("把红色杯子放进右侧收纳盒", tmp_path)
    tool_events = [event for event in finished["events"] if event["type"] == "TOOL_ACTIVITY"]
    assert len(tool_events) == 21
    assert {
        status: sum(event["payload"]["activityStatus"] == status for event in tool_events)
        for status in ("SENDING", "RUNNING", "AWAITING_EVIDENCE")
    } == {"SENDING": 7, "RUNNING": 7, "AWAITING_EVIDENCE": 7}
    assert [event["type"] for event in finished["events"] if event["type"] != "TOOL_ACTIVITY"] == [
        "TASK_CREATED",
        "TASK_APPROVED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "LOCAL_RUN_SUCCEEDED",
    ]
