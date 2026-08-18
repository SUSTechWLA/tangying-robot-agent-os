from __future__ import annotations

from tests.e2e.helpers import run_simulation_task


def test_one_sentence_sequence_completes_multiple_manipulation_goals(tmp_path):
    finished = run_simulation_task("把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来", tmp_path)
    assert finished["state"] == "SUCCEEDED"
    tasks = finished["intent"]["sequence"]
    assert [task["action"] for task in tasks] == ["pick_and_place", "fetch"]
    assert tasks[0]["destination"]["relation"] == "right_side"
    assert tasks[1]["object"]["category"] == "bottle"
    assert finished["telemetry"]["latest"]["adapter"] == "mujoco"
    assert finished["telemetry"]["latest"]["activity"] == "IDLE"
    assert finished["telemetry"]["latest"]["robotState"]["simulation"] is True
