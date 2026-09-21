"""Hand-eye: does ``AX = XB`` recover a mount that is actually there?

The stiffness of the problem is that a wrong extrinsic looks exactly like a right
one. So this file is a round trip end to end: a known camera on a known link, arm
poses drawn from the model's own joint ranges, board corners projected through a
known lens, and then only the pixels and the servo counts handed to the solver. What
comes back is compared with the mount that produced them.

The refusals get the same treatment as the successes. A solver that answers from
near-identical arm poses, or from a session whose board moved mid-capture, returns a
confident wrong extrinsic — which is worse than an error, because nothing downstream
can tell.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from tangying_robot_gateway import arm_kinematics
from tangying_robot_gateway.calibration_solver import (
    MAXIMUM_HANDEYE_RESIDUAL_PX,
    MINIMUM_HANDEYE_VIEWS,
    SIMULATED_BOARD_COLUMNS,
    SIMULATED_BOARD_ROWS,
    SIMULATED_BOARD_SQUARE_M,
    CalibrationSolveError,
    IntrinsicsSolver,
    simulated_camera,
    solve_handeye,
)

WIDTH, HEIGHT = 640, 480
CAMERA = simulated_camera(WIDTH, HEIGHT)

#: The camera is on the last link of the left arm — the moving jaw, which is what
#: ``left_arm_link6`` means and what a wrist camera is bolted to.
PARENT_LINK = "left_arm_link6"

#: The mount the solver has to find. Deliberately not axis-aligned and not centred:
#: a solve that returned the identity, or the mount the *document* already had, would
#: pass a symmetric test.
MOUNT_XYZ = (0.011, -0.006, 0.038)
MOUNT_RPY = (2.85, 0.22, -0.31)

#: The board, fixed in the world: 0.45 m in front of where the camera starts.
BOARD_IN_CAMERA_XYZ = (0.03, -0.02, 0.45)
BOARD_IN_CAMERA_RPY = (0.15, -0.20, 0.10)

BOARD = [(column * SIMULATED_BOARD_SQUARE_M, row * SIMULATED_BOARD_SQUARE_M)
         for row in range(SIMULATED_BOARD_ROWS)
         for column in range(SIMULATED_BOARD_COLUMNS)]

#: How far the arm is moved between views. Big enough that every pair carries real
#: motion, small enough that the board stays in front of the camera.
SPREAD_RADIANS = 0.42


def rpy_to_rotation(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`` — the document's own convention."""
    cos_r, sin_r = math.cos(roll), math.sin(roll)
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    rx = np.array([[1, 0, 0], [0, cos_r, -sin_r], [0, sin_r, cos_r]])
    ry = np.array([[cos_p, 0, sin_p], [0, 1, 0], [-sin_p, 0, cos_p]])
    rz = np.array([[cos_y, -sin_y, 0], [sin_y, cos_y, 0], [0, 0, 1]])
    return rz @ ry @ rx


def a_pose(xyz, rpy) -> np.ndarray:
    pose = np.eye(4)
    pose[:3, :3] = rpy_to_rotation(*rpy)
    pose[:3, 3] = xyz
    return pose


def counts_for(angles: dict[str, float], motors: dict | None = None) -> dict[str, int]:
    """The counts a servo would report at these joint angles, as the arm would."""
    counts = {}
    for motor, angle in angles.items():
        homing = 0 if motors is None else int(motors[motor]["homing_offset"])
        counts[motor] = round(
            arm_kinematics.ZERO_COUNT + homing
            + angle * arm_kinematics.COUNTS_PER_TURN / (2 * math.pi)
        ) % arm_kinematics.COUNTS_PER_TURN
    return counts


def motion_seed(seed: int, index: int) -> dict[str, float]:
    """One arm configuration: the assembled pose, moved on the five joints that
    carry the camera. The gripper stays put — an operator holds the wrist, and a jaw
    that swings through its whole range would throw the board out of frame.

    The motion is drawn from inside each joint's mechanical range, because a pose
    outside it is not one this arm can hold and the counts for it are not readings —
    ``arm_kinematics`` refuses those, and rightly.
    """
    generator = np.random.default_rng(seed * 1000 + index)
    angles = {link.motor: 0.0 for link in arm_kinematics.all_links()}
    for link in arm_kinematics.arm_links("left")[:5]:
        angles[link.motor] = float(generator.uniform(
            max(link.range_min, -SPREAD_RADIANS), min(link.range_max, SPREAD_RADIANS)))
    return angles


def a_handeye_session(*, views: int = 8, seed: int = 3,
                      motors: dict | None = None,
                      zero_error: dict[str, float] | None = None,
                      noise: float = 0.0, noise_seed: int = 0,
                      pixels_from_counts: bool = True) -> list[dict]:
    """A captured session: board views with the arm counts that produced them.

    ``zero_error`` is how a *wrong* count-to-angle mapping enters: the servo counts
    are written as if the joint's zero were somewhere else, so the poses the solver
    reconstructs are not the poses the pixels came from. Nothing about the session
    itself looks broken, which is the whole point of testing that it is refused.

    ``pixels_from_counts`` decides which of two real questions the session asks. With
    it, the arm pose that drew the corners is the pose the counts decode to, so the
    only thing between the pixels and the mount is the solver. Without it the corners
    come from the continuous pose an operator actually held, while the counts are
    what the encoder could report — half a count of lie per joint, which is the floor
    a real capture has and is measured separately.
    """
    mount = a_pose(MOUNT_XYZ, MOUNT_RPY)
    nominal = {link.motor: 0.0 for link in arm_kinematics.all_links()}
    camera_pose = arm_kinematics.link_pose(PARENT_LINK, nominal) @ mount
    board_in_world = camera_pose @ a_pose(BOARD_IN_CAMERA_XYZ, BOARD_IN_CAMERA_RPY)

    generator = np.random.default_rng(noise_seed + 1)
    records: list[dict] = []
    attempt = 0
    while len(records) < views and attempt < views * 200:
        angles = motion_seed(seed, attempt)
        attempt += 1
        recorded = dict(angles)
        if zero_error:
            for motor, offset in zero_error.items():
                recorded[motor] = angles[motor] + offset
        # What the servos report, and what the arm was actually doing while the
        # corners were drawn. With no zero error the two agree to the encoder's own
        # resolution; with one they do not, and the session looks no different.
        counts = counts_for(recorded, motors)
        held = (arm_kinematics.joint_angles_from_counts(counts_for(angles, motors), motors)
                if pixels_from_counts else angles)
        link_pose = arm_kinematics.link_pose(PARENT_LINK, held)
        camera_from_board = np.linalg.inv(link_pose @ mount) @ board_in_world
        image_points: list[float] = []
        visible = True
        for x, y in BOARD:
            point = camera_from_board[:3, :2] @ np.array([x, y]) + camera_from_board[:3, 3]
            if point[2] < 0.05:
                visible = False
                break
            u = CAMERA["fx"] * point[0] / point[2] + CAMERA["cx"]
            v = CAMERA["fy"] * point[1] / point[2] + CAMERA["cy"]
            if not (5.0 <= u <= WIDTH - 5.0 and 5.0 <= v <= HEIGHT - 5.0):
                visible = False
                break
            if noise:
                u += generator.normal(0.0, noise)
                v += generator.normal(0.0, noise)
            image_points.extend((u, v))
        if not visible:
            continue
        records.append({
            "camera": "wrist-rgbd",
            "observed": {
                "objectPoints": [value for point in BOARD for value in point],
                "imagePoints": image_points,
                "boardColumns": SIMULATED_BOARD_COLUMNS,
                "boardRows": SIMULATED_BOARD_ROWS,
                "squareM": SIMULATED_BOARD_SQUARE_M,
            },
            "pose": counts,
        })
    assert len(records) == views, (
        f"only {len(records)} of {views} sampled poses keep the board in frame")
    return records


def rotation_error_degrees(left: np.ndarray, right: np.ndarray) -> float:
    product = left[:3, :3].T @ right[:3, :3]
    cosine = max(-1.0, min(1.0, (float(np.trace(product)) - 1.0) / 2.0))
    return math.degrees(math.acos(cosine))


def solved_mount(solved: dict) -> np.ndarray:
    return a_pose(solved["xyz"], solved["rpy"])


def test_the_solver_recovers_the_mount_that_took_the_pictures():
    solved = solve_handeye(a_handeye_session(), intrinsics=CAMERA, parent_link=PARENT_LINK)
    truth = a_pose(MOUNT_XYZ, MOUNT_RPY)
    assert solved["parentLink"] == PARENT_LINK
    assert np.abs(np.array(solved["xyz"]) - np.array(MOUNT_XYZ)).max() < 1e-4, solved
    assert rotation_error_degrees(solved_mount(solved), truth) < 0.05, solved


def test_the_encoder_is_what_limits_a_real_capture_not_the_solver():
    """The floor a real session has, measured rather than assumed.

    A servo reports whole counts, and one count is 0.088° of joint. The pose the
    operator actually held is therefore up to half a count away from the pose the
    solve reconstructs, and the pixels came from the former. Over a half-metre arm
    that is a couple of millimetres of camera position — so this is what a solved
    extrinsic is worth on this hardware, and a solver claiming microns from counts
    would be claiming more than the encoder knows.
    """
    solved = solve_handeye(a_handeye_session(pixels_from_counts=False),
                           intrinsics=CAMERA, parent_link=PARENT_LINK)
    truth = a_pose(MOUNT_XYZ, MOUNT_RPY)
    error = float(np.abs(np.array(solved["xyz"]) - np.array(MOUNT_XYZ)).max())
    assert error < 5e-3, f"the encoder floor came out at {error * 1000:.1f} mm: {solved}"
    assert rotation_error_degrees(solved_mount(solved), truth) < 0.5, solved


def test_the_shipped_solver_takes_the_names_and_the_fields_the_wizard_records():
    """The plumbing the wizard actually uses, not a private entry point."""
    solved = IntrinsicsSolver().solve_handeye(
        a_handeye_session(), intrinsics=CAMERA, parent_link=PARENT_LINK)
    assert set(solved) == {"parentLink", "xyz", "rpy"}, "the document's own field set"
    assert all(isinstance(value, float) for value in solved["xyz"] + solved["rpy"])


def test_it_survives_the_corner_noise_a_real_detector_has():
    for seed in range(3):
        solved = solve_handeye(a_handeye_session(noise=0.4, noise_seed=seed),
                               intrinsics=CAMERA, parent_link=PARENT_LINK)
        truth = a_pose(MOUNT_XYZ, MOUNT_RPY)
        assert np.abs(np.array(solved["xyz"]) - np.array(MOUNT_XYZ)).max() < 3e-3, solved
        assert rotation_error_degrees(solved_mount(solved), truth) < 1.0, solved


def test_a_nonzero_homing_offset_is_applied_from_the_document_not_guessed():
    """The wizard records counts, and counts without the document's zero are not
    angles. Reading them as if every zero were at 2048 puts every arm pose in the
    wrong place — and the numbers still look like a calibration."""
    homing = 137
    motors = {link.motor: {"id": link.index, "drive_mode": 0, "homing_offset": homing,
                           "range_min": 0, "range_max": 4095}
              for link in arm_kinematics.all_links()}
    views = a_handeye_session(motors=motors)
    solved = solve_handeye(views, intrinsics=CAMERA, motors=motors, parent_link=PARENT_LINK)
    truth = a_pose(MOUNT_XYZ, MOUNT_RPY)
    assert np.abs(np.array(solved["xyz"]) - np.array(MOUNT_XYZ)).max() < 2e-3, solved
    assert rotation_error_degrees(solved_mount(solved), truth) < 0.5, solved
    # And the same session read with the wrong document is refused, because the
    # board is then not in the same place from two different views.
    nominal = {link.motor: {"id": link.index, "drive_mode": 0, "homing_offset": 0,
                            "range_min": 0, "range_max": 4095}
               for link in arm_kinematics.all_links()}
    with pytest.raises(CalibrationSolveError) as failure:
        solve_handeye(views, intrinsics=CAMERA, motors=nominal, parent_link=PARENT_LINK)
    assert failure.value.code in ("INCONSISTENT_VIEWS", "READING_OUTSIDE_JOINT_RANGE")


def test_too_few_views_are_refused_by_name():
    views = a_handeye_session(views=MINIMUM_HANDEYE_VIEWS)
    with pytest.raises(CalibrationSolveError) as failure:
        solve_handeye(views[:MINIMUM_HANDEYE_VIEWS - 1], intrinsics=CAMERA,
                      parent_link=PARENT_LINK)
    assert failure.value.code == "NOT_ENOUGH_VIEWS"


def test_arm_poses_that_are_copies_of_each_other_are_refused():
    """Eight presses of the button without moving the arm is the shape of a session
    an operator produces by mistake, and it determines nothing."""
    views = a_handeye_session(views=1) * 8
    for index, view in enumerate(views):
        view["atUnixMs"] = index
    with pytest.raises(CalibrationSolveError) as failure:
        solve_handeye(views, intrinsics=CAMERA, parent_link=PARENT_LINK)
    assert failure.value.code == "DEGENERATE_MOTIONS"
    assert "同一个姿态" in failure.value.message or "只有 0 对" in failure.value.message


def test_motion_about_a_single_axis_is_refused():
    """One joint moving leaves the camera free to spin about that joint's axis: every
    equation still holds, and the answer is one of a whole circle of answers."""
    base = {link.motor: 0.0 for link in arm_kinematics.all_links()}
    mount = a_pose(MOUNT_XYZ, MOUNT_RPY)
    camera_pose = arm_kinematics.link_pose(PARENT_LINK, base) @ mount
    board_in_world = camera_pose @ a_pose(BOARD_IN_CAMERA_XYZ, BOARD_IN_CAMERA_RPY)
    views = []
    for step in range(8):
        angles = dict(base)
        # The wrist roll only: the camera turns about one axis, and the board stays
        # in front of it, so nothing but the axis check can notice.
        angles["left_arm_wrist_roll"] = -1.2 + 0.3 * step
        link_pose = arm_kinematics.link_pose(PARENT_LINK, angles)
        camera_from_board = np.linalg.inv(link_pose @ mount) @ board_in_world
        image_points = []
        for x, y in BOARD:
            point = camera_from_board[:3, :2] @ np.array([x, y]) + camera_from_board[:3, 3]
            assert point[2] > 0.05, "the fixture lost sight of the board"
            image_points.extend((CAMERA["fx"] * point[0] / point[2] + CAMERA["cx"],
                                 CAMERA["fy"] * point[1] / point[2] + CAMERA["cy"]))
        views.append({"camera": "wrist-rgbd",
                      "observed": {"objectPoints": [v for p in BOARD for v in p],
                                   "imagePoints": image_points},
                      "pose": counts_for(angles)})
    with pytest.raises(CalibrationSolveError) as failure:
        solve_handeye(views, intrinsics=CAMERA, parent_link=PARENT_LINK)
    assert failure.value.code == "DEGENERATE_MOTIONS"


def test_a_session_whose_board_moved_is_refused_rather_than_fitted():
    """The board is nailed to the table. If the arm's reported poses and the pixels
    disagree about that, there is no mount to find — the answer would be a compromise
    between two different sessions."""
    views = a_handeye_session(views=8, zero_error={"left_arm_elbow_flex": 0.30})
    with pytest.raises(CalibrationSolveError) as failure:
        solve_handeye(views, intrinsics=CAMERA, parent_link=PARENT_LINK)
    assert failure.value.code == "INCONSISTENT_VIEWS"
    assert f"{MAXIMUM_HANDEYE_RESIDUAL_PX:.0f}" in failure.value.message


def test_missing_intrinsics_missing_poses_and_unknown_links_are_named():
    views = a_handeye_session(views=4)
    with pytest.raises(CalibrationSolveError) as failure:
        solve_handeye(views, parent_link=PARENT_LINK)
    assert failure.value.code == "MISSING_INTRINSICS"

    with pytest.raises(CalibrationSolveError) as failure:
        solve_handeye(views, intrinsics=CAMERA)
    assert failure.value.code == "UNKNOWN_PARENT_LINK"
    assert "left_arm_link6" in failure.value.message

    without_pose = [dict(view) for view in views]
    for view in without_pose:
        del view["pose"]
    with pytest.raises(CalibrationSolveError) as failure:
        solve_handeye(without_pose, intrinsics=CAMERA, parent_link=PARENT_LINK)
    assert failure.value.code == "VIEW_INCOMPLETE"


def test_a_view_with_a_nonsense_board_is_refused():
    views = a_handeye_session(views=4)
    views[1]["observed"]["imagePoints"] = views[1]["observed"]["imagePoints"][:6]
    with pytest.raises(CalibrationSolveError) as failure:
        solve_handeye(views, intrinsics=CAMERA, parent_link=PARENT_LINK)
    assert failure.value.code == "VIEW_INVALID"
    assert "第 2 个视角" in failure.value.message
