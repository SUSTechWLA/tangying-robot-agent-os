from __future__ import annotations

from tests.e2e.helpers import run_simulation_task


def test_natural_language_fetch_reaches_verified_simulation_result(tmp_path):
    finished = run_simulation_task("让xlerobot把红色杯子拿过来", tmp_path)
    assert finished["state"] == "SUCCEEDED"
    assert finished["intent"]["action"] == "fetch"
