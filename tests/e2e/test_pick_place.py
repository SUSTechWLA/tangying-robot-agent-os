from __future__ import annotations

from tests.e2e.helpers import run_simulation_task


def test_natural_language_reaches_verified_simulation_result(tmp_path):
    finished = run_simulation_task("把红色杯子放进右侧收纳盒", tmp_path)
    assert [event["type"] for event in finished["events"]] == [
        "TASK_CREATED",
        "TASK_APPROVED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "STATE_CHANGED",
        "LOCAL_RUN_SUCCEEDED",
    ]
