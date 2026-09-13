"""Coverage metrics and the instructions they produce.

The values are driven by simulation data, because the geometry and the occupancy
grid are the same shapes a real run produces; only the numbers differ.
"""

from __future__ import annotations

import sys

import pytest
from tangying_robot_gateway.mapping_coverage import (
    FREE,
    UNKNOWN,
    CameraSpec,
    CoverageInputs,
    coverage_report,
    guidance_text,
    point_in_view,
    shared_view_volume,
    simulation_camera_specs,
)

MODEL_DIR = "sim/mujoco"


def a_grid(rows: int = 20, columns: int = 20, unknown_after: int | None = None) -> list[list[int]]:
    """A grid that is fully known, optionally with the right-hand side unknown."""
    grid = []
    for _ in range(rows):
        row = [FREE] * columns
        if unknown_after is not None:
            row = [FREE if column < unknown_after else UNKNOWN for column in range(columns)]
        grid.append(row)
    return grid


def inputs(**overrides) -> CoverageInputs:
    base = {
        "grid": a_grid(), "resolution_m": 0.05, "origin": (0.0, 0.0),
        "trajectory": ((0.0, 0.0), (0.2, 0.0), (0.4, 0.0)),
        "loop_closures": 2, "valid_depth_ratio": 0.9,
    }
    base.update(overrides)
    return CoverageInputs(**base)


def test_a_fully_observed_grid_is_ready_to_run_tasks():
    report = coverage_report(inputs())
    assert report["ready"] is True
    assert report["coverageRatio"] == 1.0
    assert report["problems"] == []
    assert "可以开始执行任务" in report["summary"]


def test_an_unexplored_side_is_reported_with_a_place_to_drive_to():
    report = coverage_report(inputs(grid=a_grid(unknown_after=10)))
    assert report["ready"] is False
    assert report["coverageRatio"] == pytest.approx(0.5, abs=0.01)
    assert report["frontierCount"] == 1
    target = report["nextTargets"][0]
    # The suggestion stays on the free side of the frontier, not in unknown space.
    assert target["x"] == pytest.approx(0.475, abs=0.01)
    assert target["unknownCells"] == 200
    assert not target["navigationAuthorized"]
    assert "先验证足迹和路径" in target["instruction"]
    assert "建议观察边界坐标" in guidance_text(report)


def test_a_room_that_was_only_walked_past_is_named():
    rooms = {"客厅": (0.0, 0.0, 1.0, 1.0), "厨房": (0.0, 0.0, 0.2, 1.0)}
    grid = [[FREE if column < 4 else UNKNOWN for column in range(20)] for _ in range(20)]
    report = coverage_report(inputs(grid=grid, room_of=rooms))
    assert report["rooms"]["厨房"]["ratio"] == pytest.approx(1.0, abs=0.01)
    assert report["rooms"]["客厅"]["ratio"] == pytest.approx(0.2, abs=0.01)
    assert any("客厅" in problem for problem in report["problems"])


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"loop_closures": 0}, "回环"),
        ({"valid_depth_ratio": 0.1}, "深度有效像素"),
        ({"motion_too_fast": True}, "太快"),
        ({"trajectory": ((0.0, 0.0), (3.0, 0.0))}, "最长一步"),
    ],
)
def test_each_quality_problem_becomes_an_instruction(overrides, expected):
    report = coverage_report(inputs(**overrides))
    assert report["ready"] is False
    assert any(expected in problem for problem in report["problems"])


def test_unknown_space_behind_a_wall_is_not_offered_as_a_destination():
    """A pocket the robot cannot drive to must not become a target.

    Unknown cells next to free space are reachable and worth driving to; the ones
    sealed off by occupied cells are not, and offering them would send the operator
    after a place the planner will refuse.
    """
    grid = a_grid()
    for index in range(4, 10):
        grid[4][index] = 100      # occupied wall
        grid[9][index] = 100
        grid[index][4] = 100
        grid[index][9] = 100
    for row in range(5, 9):
        for column in range(5, 9):
            grid[row][column] = UNKNOWN
    report = coverage_report(inputs(grid=grid))
    assert report["unknownCells"] == 16
    assert report["nextTargets"] == [], "a sealed pocket is not a destination"


def test_coverage_is_measured_against_the_whole_map_not_the_walked_part():
    sparse = coverage_report(inputs(grid=a_grid(unknown_after=2)))
    assert sparse["coverageRatio"] < 0.2


def test_a_camera_sees_a_point_in_front_of_it_and_not_behind_it():
    camera = CameraSpec(name="test", position=(0.0, 0.0, 0.0), forward=(0.0, 1.0, 0.0),
                        fovy_deg=60.0, aspect=1.0,
                        right=(1.0, 0.0, 0.0), up=(0.0, 0.0, 1.0))
    assert point_in_view(camera, (0.0, 1.0, 0.0)) is True
    assert point_in_view(camera, (0.0, -1.0, 0.0)) is False, "behind the camera"
    assert point_in_view(camera, (0.56, 1.0, 0.0)) is True, "tan(30deg), not 30deg in radians"
    assert point_in_view(camera, (0.59, 1.0, 0.0)) is False
    assert point_in_view(camera, (5.0, 1.0, 0.0)) is False, "outside the horizontal field"


def test_the_two_simulation_cameras_select_a_valid_extrinsic_path():
    """The mount must expose either a shared target or a motion-calibration path."""
    sys.path.insert(0, MODEL_DIR)
    from tangying_sim.home_scene import HOME_MODEL_PATH
    from tangying_sim.rgbd_navigation import load_navigation_model

    model = load_navigation_model(HOME_MODEL_PATH, scene="home_task")
    specs = simulation_camera_specs(model)
    assert [spec.name for spec in specs] == ["head_depth", "base_depth"]

    overlap = shared_view_volume(specs)
    # The furnished household mount deliberately gives the base camera a low,
    # forward view and the head camera a work-area view. Their default poses do
    # not share a calibration target; the registered calibration service uses
    # known chassis motion for the RGB-D extrinsic instead. If a future mount
    # provides a shared target, the same metric is still exposed by this helper.
    assert overlap["has_shared_view"] is False
    assert overlap["ratio"] == 0.0
    assert overlap["bounds"] is None
