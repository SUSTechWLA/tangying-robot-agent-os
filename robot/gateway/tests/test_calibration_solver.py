"""The camera solver: does it actually recover a camera?

Written as a round trip. A board is projected through a known lens, the solver is
handed only the pixels, and the answer it returns is compared with the lens that
produced them. There is no other way to tell a calibration that works from one that
merely produces numbers in a plausible range — and a plausible-but-wrong intrinsic
is the expensive kind of wrong, because nothing downstream can detect it.
"""

from __future__ import annotations

import math

import pytest
from tangying_robot_gateway.calibration_solver import (
    MINIMUM_INTRINSICS_VIEWS,
    SIMULATED_BOARD_COLUMNS,
    SIMULATED_BOARD_ROWS,
    SIMULATED_BOARD_SQUARE_M,
    SIMULATED_CAMERA,
    CalibrationSolveError,
    IntrinsicsSolver,
    solve_intrinsics,
)

BOARD = [
    (column * SIMULATED_BOARD_SQUARE_M, row * SIMULATED_BOARD_SQUARE_M)
    for row in range(SIMULATED_BOARD_ROWS)
    for column in range(SIMULATED_BOARD_COLUMNS)
]

#: Board poses that sweep tilt and distance. A set of copies would solve nothing,
#: which is the point of the degeneracy tests below.
POSES = [
    (0.15, 0.20, -0.10, 0.020, -0.010, 0.55),
    (-0.25, 0.10, 0.30, -0.040, 0.030, 0.62),
    (0.35, -0.30, 0.20, 0.050, 0.020, 0.48),
    (-0.10, -0.20, -0.40, 0.000, -0.050, 0.70),
    (0.20, 0.40, 0.15, -0.030, 0.040, 0.58),
    (-0.30, 0.25, -0.25, 0.010, 0.000, 0.66),
    (0.05, -0.35, 0.35, -0.020, -0.030, 0.52),
    (0.30, 0.05, 0.45, 0.040, 0.050, 0.60),
    (-0.20, 0.35, 0.10, -0.050, -0.020, 0.45),
    (0.40, -0.15, -0.20, 0.030, 0.010, 0.72),
    (0.10, 0.30, 0.25, 0.000, 0.020, 0.68),
    (-0.35, -0.05, 0.05, -0.010, 0.030, 0.56),
]


def a_view(pose, *, camera=SIMULATED_CAMERA, noise=0.0, seed=0) -> dict:
    """One board view, as a captured session record."""
    import random

    generator = random.Random(seed)
    rx, ry, rz, tx, ty, tz = pose
    cos, sin = math.cos, math.sin
    rotation = [
        [cos(rz) * cos(ry), cos(rz) * sin(ry) * sin(rx) - sin(rz) * cos(rx),
         cos(rz) * sin(ry) * cos(rx) + sin(rz) * sin(rx)],
        [sin(rz) * cos(ry), sin(rz) * sin(ry) * sin(rx) + cos(rz) * cos(rx),
         sin(rz) * sin(ry) * cos(rx) - cos(rz) * sin(rx)],
        [-sin(ry), cos(ry) * sin(rx), cos(ry) * cos(rx)],
    ]
    object_points: list[float] = []
    image_points: list[float] = []
    for x, y in BOARD:
        point = [rotation[row][0] * x + rotation[row][1] * y + (tx, ty, tz)[row]
                 for row in range(3)]
        object_points.extend((x, y))
        u = camera["fx"] * point[0] / point[2] + camera["cx"]
        v = camera["fy"] * point[1] / point[2] + camera["cy"]
        if noise:
            u += generator.gauss(0.0, noise)
            v += generator.gauss(0.0, noise)
        image_points.extend((u, v))
    return {"camera": "head-rgbd", "observed": {
        "objectPoints": object_points, "imagePoints": image_points,
        "boardColumns": SIMULATED_BOARD_COLUMNS, "boardRows": SIMULATED_BOARD_ROWS,
        "squareM": SIMULATED_BOARD_SQUARE_M}}


def test_the_solver_recovers_the_lens_that_took_the_pictures():
    solved = solve_intrinsics([a_view(pose) for pose in POSES])
    for key, expected in SIMULATED_CAMERA.items():
        assert abs(solved[key] - expected) < 0.05, f"{key} came back as {solved[key]}"


def test_it_needs_the_views_the_plan_asks_for():
    with pytest.raises(CalibrationSolveError) as failure:
        solve_intrinsics([a_view(pose) for pose in POSES[:MINIMUM_INTRINSICS_VIEWS - 1]])
    assert failure.value.code == "NOT_ENOUGH_VIEWS"


def test_views_that_are_copies_of_each_other_are_refused():
    """The failure that matters. Near-identical poses leave the system rank-deficient,
    and a solver that answered anyway would return a confident wrong camera."""
    with pytest.raises(CalibrationSolveError) as failure:
        solve_intrinsics([a_view(POSES[0]) for _ in POSES])
    assert failure.value.code == "DEGENERATE_VIEWS"
    assert "角度" in failure.value.message


def test_a_board_that_only_slides_sideways_is_refused_too():
    sliding = [a_view((0.0, 0.0, 0.0, -0.02 + 0.004 * index, 0.0, 0.55))
               for index in range(len(POSES))]
    with pytest.raises(CalibrationSolveError) as failure:
        solve_intrinsics(sliding)
    assert failure.value.code == "DEGENERATE_VIEWS"


def test_a_view_with_too_few_points_is_named_not_guessed_at():
    broken = a_view(POSES[0])
    broken["observed"]["imagePoints"] = broken["observed"]["imagePoints"][:4]
    with pytest.raises(CalibrationSolveError) as failure:
        solve_intrinsics([broken] + [a_view(pose) for pose in POSES[1:]])
    assert failure.value.code == "VIEW_INVALID"
    assert "第 1 个视角" in failure.value.message


def test_half_a_view_is_refused_rather_than_solved_around():
    broken = a_view(POSES[0])
    broken["observed"]["imagePoints"] = broken["observed"]["imagePoints"][:-2]
    with pytest.raises(CalibrationSolveError) as failure:
        solve_intrinsics([broken] + [a_view(pose) for pose in POSES[1:]])
    assert failure.value.code == "VIEW_INVALID"


def test_sub_pixel_corner_noise_does_not_move_the_answer_far():
    """A real detector is not exact, so the useful question is not "is it exact" but
    "how far does it drift". This pins the drift so a change that wrecks conditioning
    is caught here rather than on a robot."""
    for seed in range(5):
        solved = solve_intrinsics([a_view(pose, noise=0.3, seed=seed) for pose in POSES])
        assert abs(solved["fx"] - SIMULATED_CAMERA["fx"]) < 10.0, f"seed {seed}: {solved}"
        assert abs(solved["fy"] - SIMULATED_CAMERA["fy"]) < 10.0, f"seed {seed}: {solved}"


def test_the_shipped_solver_takes_a_camera_name_and_the_session_records():
    solved = IntrinsicsSolver().solve_intrinsics(
        "head-rgbd", [a_view(pose) for pose in POSES])
    assert set(solved) == {"fx", "fy", "cx", "cy"}
    assert all(isinstance(value, float) for value in solved.values())


def test_hand_eye_refuses_a_single_view_and_names_what_is_missing():
    """This used to refuse always, for want of a kinematic model. Now it solves, and
    what is left is the refusal the data earns: one view is not a motion, and a
    solver that answered from it would be answering from nothing. The round trip
    that shows it recovers a real mount lives in ``test_calibration_handeye.py``."""
    with pytest.raises(CalibrationSolveError) as failure:
        IntrinsicsSolver().solve_handeye([a_view(POSES[0])])
    assert failure.value.code == "NOT_ENOUGH_VIEWS"
    assert "视角" in failure.value.message
