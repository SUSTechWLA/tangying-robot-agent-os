import math

import numpy as np
import pytest
from tangying_robot_gateway.navigation_map import merge_grids


def grid(cells, origin):
    return {"width": len(cells[0]), "height": len(cells), "resolution": .25,
            "origin": list(map(float, origin)), "cells": np.array(cells, dtype=int)}


@pytest.mark.parametrize("yaw", [0., math.pi / 2, .61])
def test_grid_merge_uses_common_rotated_lattice_and_preserves_obstacles(yaw):
    rotation = np.array([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
    base_origin = np.array([2., -1.])
    extra_origin = base_origin + rotation @ np.array([-.25, .25])
    merged = merge_grids(grid([[0, 100], [0, 0]], [*base_origin, yaw]),
                         grid([[100, -1], [0, 100]], [*extra_origin, yaw]))
    assert merged["width"] == merged["height"] == 3
    np.testing.assert_allclose(merged["origin"], [*(base_origin + rotation @ [-.25, 0.]), yaw])
    np.testing.assert_array_equal(merged["cells"], [[-1, 0, 100], [100, 0, 0], [0, 100, -1]])


def test_grid_merge_refuses_rotation_without_registered_resampling():
    with pytest.raises(ValueError, match="orientation"):
        merge_grids(grid([[0]], [0., 0., 0.]), grid([[0]], [0., 0., .1]))


@pytest.mark.parametrize("offset", [.1, .125, -.1])
def test_grid_merge_does_not_round_fractional_cell_origins(offset):
    with pytest.raises(ValueError, match="lattice"):
        merge_grids(grid([[0]], [0., 0., 0.]), grid([[100]], [offset, 0., 0.]))


def test_grid_merge_accepts_equivalent_orientation_modulo_two_pi():
    merged = merge_grids(grid([[0]], [0., 0., 0.]), grid([[100]], [.25, 0., 2 * math.pi]))
    np.testing.assert_array_equal(merged["cells"], [[0, 100]])
