"""Frontier exploration: the policy that decides where a survey looks next.

These tests use synthetic grids rather than a robot, because the questions that
matter are geometric - does it route around a wall, does it refuse to plan
through unmapped space, does it prefer the frontier that reveals more - and a
simulated house makes them slower to ask without making them truer.

Grid rows are written bottom-up, matching the occupancy convention: row 0 is the
lowest y, which is also how the map publishes and how the planner indexes.
"""

from __future__ import annotations

import numpy as np
import pytest
from tangying_robot_gateway.exploration import (
    Grid,
    clearance_mask,
    coverage_report,
    explore_target,
    frontier_mask,
    next_waypoint,
    plan_path,
    shorten_path,
    traversable_mask,
)

VALUES = {"#": 100, ".": 0, "?": -1}


def grid_from(rows, resolution=1.0, origin=(0.0, 0.0)):
    """Build a grid from character art: '#' occupied, '.' free, '?' unknown."""
    cells = np.array([[VALUES[character] for character in row] for row in rows], dtype=np.int16)
    return Grid(cells=cells, resolution=resolution, origin=origin)


def art(rows, resolution=1.0, origin=(0.0, 0.0)):
    return grid_from(list(reversed(rows)), resolution=resolution, origin=origin)


def test_a_frontier_is_known_floor_touching_unknown_space():
    grid = art([
        "??",
        "..",
        "..",
    ])
    mask = frontier_mask(grid.cells)
    np.testing.assert_array_equal(mask[1], [True, True])   # the row under the unknown
    np.testing.assert_array_equal(mask[0], [False, False])  # floor with no unknown neighbour


def test_clearance_is_measured_from_the_obstacle_not_from_the_row_next_to_it():
    grid = art([
        ".....",
        "..#..",
        ".....",
    ], resolution=1.0)
    one = clearance_mask(grid.cells, 1)
    assert not one[1, 2], "the obstacle itself is not clear"
    assert not one[1, 1], "a four-neighbour is exactly one metre away"
    assert not one[0, 2], "and so is the cell above it"
    two = clearance_mask(grid.cells, 2)
    assert not two[0, 1], "a diagonal neighbour is sqrt(2) metres away"
    assert two[0, 0], "two cells diagonally is sqrt(8), which clears a two-metre envelope"


def test_a_corridor_narrower_than_the_envelope_has_no_drivable_centre():
    narrow = art([
        "#####",
        ".....",
        "#####",
    ], resolution=1.0)
    assert not clearance_mask(narrow.cells, 2)[1].any()
    wide = art([
        "#######",
        ".......",
        ".......",
        ".......",
        ".......",
        ".......",
        "#######",
    ], resolution=1.0)
    assert clearance_mask(wide.cells, 2)[3].any()


def test_unknown_space_is_never_traversable():
    grid = art([
        "??..",
        "....",
    ], resolution=1.0)
    traversable = traversable_mask(grid.cells, 0)
    np.testing.assert_array_equal(traversable[1], [False, False, True, True])
    np.testing.assert_array_equal(traversable[0], [True, True, True, True])


def test_a_route_goes_around_a_wall_rather_than_through_it():
    grid = art([
        "..#...",
        "......",
        "..#...",
    ], resolution=1.0)
    traversable = traversable_mask(grid.cells, 0)
    path, length = plan_path(grid, traversable, (0.5, 0.5), (5.5, 0.5))
    assert path is not None
    assert length > 5.0, "a straight line along the bottom row would be 5 m"
    for x, y in path:
        assert grid.cells[grid.row(y), grid.column(x)] == 0


def test_no_route_is_reported_when_the_only_way_is_through_unknown_space():
    grid = art([
        "..??..",
        "..??..",
    ], resolution=1.0)
    path, length = plan_path(grid, traversable_mask(grid.cells, 0), (0.5, 0.5), (5.5, 0.5))
    assert path is None and length == float("inf")


def test_a_diagonal_step_may_not_clip_a_blocked_corner():
    grid = art([
        "#.",
        ".#",
    ], resolution=1.0)
    assert grid.cells[0, 0] == 0 and grid.cells[1, 1] == 0
    assert grid.cells[0, 1] == 100 and grid.cells[1, 0] == 100
    path, _length = plan_path(grid, traversable_mask(grid.cells, 0), (0.5, 0.5), (1.5, 1.5))
    assert path is None


def test_of_two_equally_far_frontiers_the_one_that_hides_more_wins():
    # Two openings off one corridor at the same distance: one hides a cupboard,
    # the other a room. Travel is the tie-breaker, information decides the tie,
    # so the survey does not spend a leg on the cupboard first.
    grid = art([
        "??????????????????????..........??",
        "??????????????????????..........??",
        "??????????????????????..........??",
        "......................#...........",
        "##..............................##",
        "..................................",
    ], resolution=1.0)
    # Halfway between them: the room wins on information.
    tied = explore_target(grid, robot_xy=(26.5, 2.5), sensor_radius_m=3.0, radius_m=0.0,
                          min_frontier_cells=1, candidates=8)
    assert tied is not None and tied.viewpoint is not None
    assert tied.viewpoint[0] < 25.0, f"expected the larger unknown region, got {tied.viewpoint}"
    # Standing beside the cupboard: the room is a quarter farther and well past
    # the tie allowance, so the cheap leg wins instead and the room waits.
    beside = explore_target(grid, robot_xy=(32.0, 2.5), sensor_radius_m=3.0, radius_m=0.0,
                            min_frontier_cells=1, candidates=8)
    assert beside is not None and beside.viewpoint[0] > 30.0, beside.viewpoint
    assert tied.gain > beside.gain


def test_a_fully_measured_map_has_nothing_left_to_explore():
    assert explore_target(art(["....", "...."]), robot_xy=(0.5, 0.5),
                          sensor_radius_m=3.0, radius_m=0.0) is None


def test_frontiers_smaller_than_the_threshold_are_ignored():
    grid = art([
        "#?",
        "#.",
    ], resolution=1.0)
    np.testing.assert_array_equal(frontier_mask(grid.cells)[0], [False, True])
    assert explore_target(grid, robot_xy=(1.5, 0.5), sensor_radius_m=3.0, radius_m=0.0,
                          min_frontier_cells=2) is None
    assert explore_target(grid, robot_xy=(1.5, 0.5), sensor_radius_m=3.0, radius_m=0.0,
                          min_frontier_cells=1) is not None


def test_a_frontier_the_robot_cannot_reach_is_not_chosen():
    # Two pockets of known floor, each with unknown space above it, joined only
    # by a wall. The route may not leave the pocket the robot is standing in.
    grid = art([
        "??..??",
        "..##..",
        "..##..",
    ], resolution=1.0)
    target = explore_target(grid, robot_xy=(0.5, 0.5), sensor_radius_m=3.0, radius_m=0.0,
                            min_frontier_cells=1, candidates=8)
    assert target is not None and target.path
    assert all(x < 2.0 for x, _y in target.path)


def test_an_unreachable_frontier_does_not_hide_the_reachable_ones_behind_it():
    """The near-tie band follows the nearest *plannable* frontier.

    A survey stopped at 28% unknown reporting no_reachable_frontier because the
    three nearest frontier fragments were slivers behind furniture with no route,
    and the band the nearest of them set excluded a plannable fragment further
    out. Distance is a preference, not a veto: a target the robot cannot drive to
    must not decide how far the robot is willing to look.
    """
    grid = art([
        "###############??#",   # far unknown pocket at x=15..16
        "#.?#.............#",   # free corridor x=4..16; sealed pocket free x=1, unknown x=2
        "###..............#",   # robot stands at x=4..16
        "##################",
    ], resolution=1.0)
    target = explore_target(grid, robot_xy=(4.5, 1.5), sensor_radius_m=3.0, radius_m=0.0,
                            min_frontier_cells=1, candidates=8)
    assert target is not None, "a reachable frontier 11 m away must still be offered"
    assert target.viewpoint[0] > 10.0, target.viewpoint


def test_a_frontier_walled_off_from_every_route_is_not_offered():
    grid = art([
        "?????",
        "#####",
        ".....",
    ], resolution=1.0)
    assert explore_target(grid, robot_xy=(0.5, 0.5), sensor_radius_m=3.0,
                          radius_m=0.0, min_frontier_cells=1) is None


def test_coverage_counts_every_settled_state_separately():
    report = coverage_report(art([
        "?.",
        "#.",
    ]).cells)
    assert report == {"cells": 4, "unknown": 1, "occupied": 1, "free": 2,
                      "unknownFraction": 0.25}


def test_the_next_waypoint_is_a_short_step_not_the_whole_route():
    path = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
    assert next_waypoint(None, path, lookahead_m=0.5) == (1.0, 0.0)
    assert next_waypoint(None, [(0.0, 0.0)], lookahead_m=0.5) == (0.0, 0.0)
    assert next_waypoint(None, [], lookahead_m=0.5) is None


def test_a_reported_route_keeps_its_endpoints_when_thinned():
    path = [(0.0, 0.0), (0.1, 0.0), (0.2, 0.0), (1.0, 0.0)]
    assert shorten_path(path, spacing_m=0.5) == [(0.0, 0.0), (1.0, 0.0)]


def test_an_unknown_fraction_is_high_where_nothing_has_been_seen():
    grid = art([
        "????",
        "?...",
        "....",
    ], resolution=1.0)
    assert grid.unknown_fraction(0.5, 0.5, 2.0) > 0.4
    assert grid.unknown_fraction(3.5, 0.5, 1.0) < 0.4


def test_an_out_of_grid_pose_is_reported_rather_than_clamped():
    grid = art([".."], resolution=1.0)
    assert grid.nearest_free_cell(100.0, 100.0) is None
    with pytest.raises(ValueError):
        Grid(cells=np.zeros((0, 2), dtype=np.int16), resolution=1.0, origin=(0.0, 0.0))
    with pytest.raises(ValueError):
        Grid(cells=np.zeros((2, 2), dtype=np.int16), resolution=0.0, origin=(0.0, 0.0))


def test_space_the_camera_can_never_see_is_not_a_frontier():
    """A forward camera never measures the floor under its own path.

    Those cells stay unknown for the whole survey. Counting them as frontier
    makes the robot drive at its own footprint: every pose has a ring of
    unmeasurable floor around it, and that ring is always the nearest thing to
    drive at, so the survey never leaves the spot it started from.
    """
    grid = art([
        "??????????",
        "?........?",
        "??????????",
    ], resolution=1.0)
    blind = np.zeros(grid.cells.shape, dtype=bool)
    blind[1, :] = True          # the strip the camera has proved it cannot see
    assert explore_target(grid, robot_xy=(5.5, 1.5), sensor_radius_m=3.0, radius_m=0.0,
                          min_frontier_cells=1) is not None
    assert explore_target(grid, robot_xy=(5.5, 1.5), sensor_radius_m=3.0, radius_m=0.0,
                          min_frontier_cells=1, blind=blind) is None


def test_a_step_that_would_cut_a_corner_is_shortened():
    """A robot drives at a point, not along a polyline.

    Aiming half a metre ahead on a route that just turned a corner asks it to
    cut that corner, and the driver refuses - because the corner is exactly the
    clearance the route was computed to preserve.
    """
    from tangying_robot_gateway.exploration import traversable_for

    grid = art([
        ".#.",
        ".#.",
        "...",
    ], resolution=1.0)
    # A route that goes up and then right, around the end of a wall.
    path = [(0.5, 0.5), (0.5, 1.5), (0.5, 2.5), (1.5, 2.5)]
    traversable = traversable_for(grid, 0.0)
    blind_step = next_waypoint(grid, path, lookahead_m=3.0)
    assert blind_step == (1.5, 2.5), "without the check the whole route is one step"
    guarded = next_waypoint(grid, path, lookahead_m=3.0, traversable=traversable)
    assert guarded == (0.5, 2.5), f"the corner-cutting step must be withheld, got {guarded}"
    # The straight line from (0.5, 0.5) to (1.5, 2.5) passes through the wall at
    # x=1, so the guarded step has to stay on the column the wall is not in.
    from tangying_robot_gateway.exploration import _straight_line_clear

    assert not _straight_line_clear(grid, (0.5, 0.5), (1.5, 2.5), traversable)
    assert _straight_line_clear(grid, (0.5, 0.5), (0.5, 2.5), traversable)
