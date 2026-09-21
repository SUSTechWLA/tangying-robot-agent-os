"""Turning kept board views into the numbers a calibration document holds.

:func:`solve_intrinsics` is Zhang's method, which needs nothing but correspondences
between a planar board and the pixels it landed on.

:func:`solve_handeye` is ``AX = XB``: ``A`` is how the link under the camera moved
between two views, ``B`` is how the board moved in the camera frame, and ``X`` — the
answer — is where the camera sits relative to that link. ``A`` comes from
``arm_kinematics``, which does forward kinematics from the simulator's MuJoCo model;
``B`` comes from the same homography decomposition the intrinsics solve already uses.
Neither is invented, and both are checked against something outside this file: the FK
against MuJoCo's own body poses, the solver against synthesised mounts it has to
recover.

Two things this module will not do, because both produce a plausible answer rather
than an error: solve hand-eye from motions that are too small or too parallel to
determine a rotation, and solve it from views whose board poses are inconsistent with
any rigid mount. Both are refused by name.

Only numpy is used. OpenCV is not a dependency of this gateway, and adding one to
solve a 6-unknown linear system would be a poor trade. SciPy is used for the
non-linear refinement, which the linear solve alone is not accurate enough for.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from tangying_robot_gateway import arm_kinematics
from tangying_robot_gateway.arm_kinematics import KinematicsError

__all__ = [
    "MAXIMUM_HANDEYE_RESIDUAL_PX",
    "MINIMUM_AXIS_SEPARATION_DEGREES",
    "MINIMUM_BOARD_POINTS",
    "MINIMUM_HANDEYE_VIEWS",
    "MINIMUM_INTRINSICS_VIEWS",
    "MINIMUM_MOTION_DEGREES",
    "SIMULATED_BOARD_COLUMNS",
    "SIMULATED_BOARD_ROWS",
    "SIMULATED_BOARD_SQUARE_M",
    "SIMULATED_CAMERA",
    "CalibrationSolveError",
    "IntrinsicsSolver",
    "simulated_camera",
    "solve_handeye",
    "solve_intrinsics",
]

#: The lens the simulated unit looks through. Written as a function of the frame
#: size because a fixed pixel value is only right for one resolution: the reference
#: robot's RGB-D cameras are 320×240, and a 640×480 lens applied to them puts the
#: principal point outside the image.
#:
#: The principal point is deliberately *not* the geometric centre. A solve that
#: merely echoed the frame dimensions would land exactly on centre, so an off-centre
#: truth is what makes "it recovered the lens" mean something.
SIMULATED_FOCAL_PER_PIXEL = 0.957
SIMULATED_FOCAL_ASPECT = 0.996
SIMULATED_PRINCIPAL_OFFSET = (1.5, -0.75)


def simulated_camera(width: int = 640, height: int = 480) -> dict[str, float]:
    """The simulated unit's lens at this frame size."""
    return {
        "fx": SIMULATED_FOCAL_PER_PIXEL * width,
        "fy": SIMULATED_FOCAL_PER_PIXEL * width * SIMULATED_FOCAL_ASPECT,
        "cx": (width - 1) / 2.0 + SIMULATED_PRINCIPAL_OFFSET[0],
        "cy": (height - 1) / 2.0 + SIMULATED_PRINCIPAL_OFFSET[1],
    }


SIMULATED_CAMERA = simulated_camera()
SIMULATED_BOARD_COLUMNS = 7
SIMULATED_BOARD_ROWS = 5
SIMULATED_BOARD_SQUARE_M = 0.025


class CalibrationSolveError(Exception):
    """A refusal a person can act on, in the same shape the wizard raises."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


#: Fewer than this many views cannot constrain a four-parameter camera. Three
#: views give six equations for five unknowns in the absolute conic and the
#: system is rank-deficient in practice for a board that never tilted.
MINIMUM_INTRINSICS_VIEWS = 3

#: A board needs at least this many points per view for its homography to be
#: determined at all. Four is the algebraic floor; five is the smallest number
#: that leaves a residual to notice a mis-detected corner with.
MINIMUM_BOARD_POINTS = 4


def _as_array(observed: dict[str, Any], key: str, width: int, view: int) -> np.ndarray:
    """Read one flat numeric field from a view, or refuse it by name."""
    raw = observed.get(key)
    if not isinstance(raw, (list, tuple)) or not raw:
        raise CalibrationSolveError(
            "VIEW_INCOMPLETE", f"第 {view + 1} 个视角缺少 {key}")
    try:
        values = np.asarray([float(value) for value in raw], dtype=float)
    except (TypeError, ValueError) as error:
        raise CalibrationSolveError(
            "VIEW_INVALID", f"第 {view + 1} 个视角的 {key} 含非数值：{error}") from error
    if values.size % width:
        raise CalibrationSolveError(
            "VIEW_INVALID",
            f"第 {view + 1} 个视角的 {key} 长度 {values.size} 不是 {width} 的整数倍")
    if not np.all(np.isfinite(values)):
        raise CalibrationSolveError("VIEW_INVALID", f"第 {view + 1} 个视角的 {key} 含非有限值")
    return values.reshape(-1, width)


def _normalise(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Hartley normalisation: centre the points and scale their mean distance to √2.

    Not cosmetic. A homography estimated from raw pixel coordinates is badly
    conditioned — the pixel magnitudes dominate the singular vectors — and the
    intrinsics that come out of it drift by percent, which is the difference
    between a usable calibration and one that quietly ruins every projection.
    """
    centroid = points.mean(axis=0)
    shifted = points - centroid
    distance = float(np.sqrt((shifted ** 2).sum(axis=1)).mean())
    scale = math.sqrt(2.0) / distance if distance > 1e-12 else 1.0
    transform = np.array([
        [scale, 0.0, -scale * centroid[0]],
        [0.0, scale, -scale * centroid[1]],
        [0.0, 0.0, 1.0],
    ])
    return transform, shifted * scale


def _homography(object_points: np.ndarray, image_points: np.ndarray) -> np.ndarray:
    """The planar homography mapping board coordinates to pixels, by DLT."""
    rows = []
    for (x, y), (u, v) in zip(object_points, image_points):
        rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u])
        rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v])
    _, _, vt = np.linalg.svd(np.asarray(rows))
    return vt[-1].reshape(3, 3)


def _v_ij(h: np.ndarray, i: int, j: int) -> np.ndarray:
    """Zhang's ``v_ij``: the row that, dotted with ``b``, gives ``h_iᵀ B h_j``."""
    return np.array([
        h[0, i] * h[0, j],
        h[0, i] * h[1, j] + h[1, i] * h[0, j],
        h[1, i] * h[1, j],
        h[2, i] * h[0, j] + h[0, i] * h[2, j],
        h[2, i] * h[1, j] + h[1, i] * h[2, j],
        h[2, i] * h[2, j],
    ])


def _intrinsics_from_b(b: np.ndarray) -> np.ndarray:
    """The closed form that reads a calibration matrix out of the image of the conic."""
    # B is only determined up to scale, and the singular vector that carries it has
    # an arbitrary sign. Pinning the sign first is not cosmetic: the formulas below
    # take a square root, and a negated B would be rejected as "no real solution"
    # even though the geometry is perfectly fine.
    if b[0] < 0:
        b = -b
    b11, b12, b22, b13, b23, b33 = b
    denominator = b11 * b22 - b12 * b12
    if abs(denominator) < 1e-15:
        raise CalibrationSolveError(
            "DEGENERATE_VIEWS",
            "这些视角解不出相机内参：板子的姿态几乎相同，或者始终正对相机。"
            "请让板子在画面里换几个不同的倾斜角度和距离，再拍一遍。")
    v0 = (b12 * b13 - b11 * b23) / denominator
    lambda_ = b33 - (b13 * b13 + v0 * (b12 * b13 - b11 * b23)) / b11
    if lambda_ <= 0 or b11 <= 0:
        raise CalibrationSolveError(
            "DEGENERATE_VIEWS",
            "解出的相机内参不是实数解：视角之间的姿态差异不够。"
            "请让板子倾斜到明显不同的角度后重新拍摄。")
    fx = math.sqrt(lambda_ / b11)
    fy = math.sqrt(lambda_ * b11 / denominator)
    skew = -b12 * fx * fx * fy / lambda_
    u0 = skew * v0 / fy - b13 * fx * fx / lambda_
    return np.array([
        [fx, skew, u0],
        [0.0, fy, v0],
        [0.0, 0.0, 1.0],
    ])


def _view_homography(object_points: np.ndarray, image_points: np.ndarray) -> np.ndarray:
    """One view's board-to-pixel homography, in the pixel frame.

    Both sides are normalised first, which is what keeps the DLT well conditioned,
    and the result is then pulled back. The pull-back is not optional: the
    constraints it feeds are statements about a camera in pixels, and a homography
    left in a per-view normalised frame would describe a different camera for every
    view.
    """
    image_transform, normalised_image = _normalise(image_points)
    object_transform, normalised_object = _normalise(object_points)
    return (np.linalg.inv(image_transform)
            @ _homography(normalised_object, normalised_image)
            @ object_transform)


def _pose_from_homography(h: np.ndarray, camera: np.ndarray,
                          object_points: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Each view's board pose, recovered from its homography and the current camera.

    Used as the starting point for the refinement below, and — for hand-eye — as the
    measurement itself: ``B`` in ``AX = XB`` is a board pose in the camera frame.

    The sign is resolved here rather than left to the caller. A homography and its
    negative are the same homography, so the DLT picks one arbitrarily, and the two
    branches differ by a half turn about the board's normal *and* a sign on the
    translation: the wrong one puts the board behind the camera. For the intrinsics
    refinement that only made the optimiser start further away; for hand-eye it makes
    ``AX = XB`` false for half the pairs, and the solver then converges on a mount
    that is tens of degrees out. A camera cannot see a board behind it, so the branch
    with the board in front is the one the data means.
    """
    try:
        inverse = np.linalg.inv(camera)
        r1 = inverse @ h[:, 0]
        r2 = inverse @ h[:, 1]
        r3 = inverse @ h[:, 2]
        scale = 2.0 / (np.linalg.norm(r1) + np.linalg.norm(r2))
        rotation = np.column_stack([r1 * scale, r2 * scale])
        translation = r3 * scale
        third = np.cross(rotation[:, 0], rotation[:, 1])
        # Gram-Schmidt back to a real rotation: the columns came from a noisy
        # homography and are only approximately orthonormal.
        u, _, vt = np.linalg.svd(np.column_stack([rotation, third]))
        rotation = u @ vt
        if np.linalg.det(rotation) < 0:
            rotation = -rotation
        if translation[2] < 0:
            # The other branch of the same homography: turn the board half a turn
            # about its normal and put it back in front of the lens.
            rotation = rotation @ np.diag([-1.0, -1.0, 1.0])
            translation = -translation
        # No recentring here. The homography was pulled back to the caller's own
        # object coordinates, so r1, r2 and t already refer to those points;
        # subtracting a board-centre offset on top would shift every view by a few
        # millimetres and hand the optimiser a starting point several pixels out.
        return rotation, translation
    except np.linalg.LinAlgError:
        return None


def _rodrigues(rotvec: np.ndarray) -> np.ndarray:
    """Axis-angle to rotation matrix.

    Written out rather than taken from ``scipy.spatial.transform`` because the
    optimiser calls this once per view per residual evaluation, and constructing a
    ``Rotation`` object each time dominated the whole solve.
    """
    theta = float(np.linalg.norm(rotvec))
    if theta < 1e-12:
        return np.eye(3)
    axis = rotvec / theta
    kx, ky, kz = axis
    cross = np.array([[0.0, -kz, ky], [kz, 0.0, -kx], [-ky, kx, 0.0]])
    return np.eye(3) + math.sin(theta) * cross + (1.0 - math.cos(theta)) * (cross @ cross)


def _refine(camera: np.ndarray, views: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """Minimise reprojection error over the intrinsics and every view's pose.

    The linear solve is exact on perfect data and several pixels off on real data,
    because it minimises an algebraic quantity that is not the thing anyone cares
    about. Nobody projects with an algebraic residual; they project with a camera,
    so this is the step that decides whether the calibration is usable.

    ``soft_l1`` rather than plain least squares: a single mis-detected corner
    otherwise drags the whole solution, and rejecting that corner is exactly what
    the robust loss does without needing an outlier decision up front.
    """
    from scipy.optimize import least_squares
    from scipy.sparse import csr_matrix

    poses = []
    for index, (object_points, image_points) in enumerate(views):
        decomposed = _pose_from_homography(
            _view_homography(object_points, image_points), camera, object_points)
        if decomposed is None:
            raise CalibrationSolveError(
                "DEGENERATE_VIEWS", f"第 {index + 1} 个视角无法解出板子位姿，无法优化内参")
        rotation, translation = decomposed
        poses.append((object_points, image_points, rotation, translation))

    def residuals(parameters: np.ndarray) -> np.ndarray:
        intrinsic = np.array([
            [parameters[0], 0.0, parameters[2]],
            [0.0, parameters[1], parameters[3]],
            [0.0, 0.0, 1.0],
        ])
        out = []
        for index, (object_points, image_points, _, _) in enumerate(poses):
            offset = 4 + index * 6
            rotation = _rodrigues(parameters[offset:offset + 3])
            translation = parameters[offset + 3:offset + 6]
            cam = object_points @ rotation[:, :2].T + translation
            projected = cam @ intrinsic.T
            projected = projected[:, :2] / projected[:, 2:3]
            out.append((projected - image_points).ravel())
        return np.concatenate(out)

    # Every residual of a view depends on the four intrinsics and on that view's own
    # six pose numbers — nothing else. Declaring that pattern lets the optimiser
    # perturb grouped columns instead of probing all 4 + 6n parameters one at a
    # time, which is the difference between a solve measured in seconds and one
    # measured in minutes for a twelve-view session.
    rows: list[np.ndarray] = []
    columns: list[np.ndarray] = []
    cursor = 0
    for index, (object_points, _, _, _) in enumerate(poses):
        count = object_points.shape[0] * 2
        block = np.arange(cursor, cursor + count)
        for column in range(4):
            rows.append(block)
            columns.append(np.full(count, column))
        for column in range(6):
            rows.append(block)
            columns.append(np.full(count, 4 + index * 6 + column))
        cursor += count
    row_index = np.concatenate(rows)
    column_index = np.concatenate(columns)
    sparsity = csr_matrix(
        (np.ones(row_index.size), (row_index, column_index)),
        shape=(cursor, 4 + 6 * len(poses)),
    )

    start = np.concatenate([
        [camera[0, 0], camera[1, 1], camera[0, 2], camera[1, 2]],
        *[np.concatenate([
            _rotvec_from_matrix(pose[2]), pose[3]]) for pose in poses],
    ])
    # Tight tolerances on purpose. The default ``ftol`` is satisfied after four
    # evaluations because the cost surface is nearly flat along the directions the
    # principal point lives in, and stopping there leaves it where the linear guess
    # happened to land.
    result = least_squares(residuals, start, method="trf", loss="soft_l1",
                           jac_sparsity=sparsity, max_nfev=200,
                           ftol=1e-12, xtol=1e-12, gtol=1e-12)
    return np.array([
        [result.x[0], 0.0, result.x[2]],
        [0.0, result.x[1], result.x[3]],
        [0.0, 0.0, 1.0],
    ])


def _rotvec_from_matrix(rotation: np.ndarray) -> np.ndarray:
    """Rotation matrix to axis-angle, by the standard stable branch selection."""
    trace = float(np.trace(rotation))
    if trace > 3.0 - 1e-12:
        # Only a genuinely zero rotation needs the special case. A wider tolerance
        # would silently return zero for small-but-real rotations and throw away
        # precision the optimiser then has to re-find.
        return np.zeros(3)
    if trace < -0.999999:
        # A half turn: sin(angle) vanishes, so the usual formula divides by zero.
        # Here (R + I)/2 = a·aᵀ, which gives the axis components directly.
        diagonal = np.maximum((np.diag(rotation) + 1.0) / 2.0, 0.0)
        axis = np.sqrt(diagonal)
        largest = int(np.argmax(axis))
        if axis[largest] > 1e-9:
            axis = (rotation[largest, :] + rotation[:, largest]) / (4.0 * axis[largest])
            axis[largest] = np.sqrt(diagonal[largest])
        return axis / max(float(np.linalg.norm(axis)), 1e-12) * math.pi
    angle = math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))
    axis = np.array([
        rotation[2, 1] - rotation[1, 2],
        rotation[0, 2] - rotation[2, 0],
        rotation[1, 0] - rotation[0, 1],
    ]) / (2.0 * math.sin(angle))
    return axis * angle


def solve_intrinsics(views: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Recover focal length and principal point from planar board views.

    ``views`` are the records the wizard kept, each holding a flat
    ``observed.objectPoints`` (board-plane ``x, y`` pairs, interleaved) and the
    ``observed.imagePoints`` they were seen at (pixel ``u, v`` pairs).

    Distortion is not estimated. The document's distortion model is separable, and
    a five-parameter fit from the same twelve views is an order of magnitude
    noisier than the four-parameter fit it would be layered on — the honest
    sequence is a distortion-free solve first, then a separate pass once there is
    a real lens to measure.
    """
    if len(views) < MINIMUM_INTRINSICS_VIEWS:
        raise CalibrationSolveError(
            "NOT_ENOUGH_VIEWS",
            f"内参至少需要 {MINIMUM_INTRINSICS_VIEWS} 个视角，现在只有 {len(views)} 个")

    constraints = []
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    for index, view in enumerate(views):
        observed = view.get("observed") if isinstance(view, dict) else None
        if not isinstance(observed, dict):
            raise CalibrationSolveError("VIEW_INCOMPLETE", f"第 {index + 1} 个视角没有观测数据")
        object_points = _as_array(observed, "objectPoints", 2, index)
        image_points = _as_array(observed, "imagePoints", 2, index)
        if object_points.shape != image_points.shape:
            raise CalibrationSolveError(
                "VIEW_INVALID",
                f"第 {index + 1} 个视角的板面点与图像点数量不一致"
                f"（{object_points.shape[0]} 对 {image_points.shape[0]}）")
        if object_points.shape[0] < MINIMUM_BOARD_POINTS:
            raise CalibrationSolveError(
                "VIEW_INVALID",
                f"第 {index + 1} 个视角只认出 {object_points.shape[0]} 个点，"
                f"至少需要 {MINIMUM_BOARD_POINTS} 个")

        # Both sides are normalised, which is what keeps the DLT well conditioned,
        # and the homography is then pulled back to the pixel frame. The pull-back
        # is not optional: the constraints below are statements about K in pixels,
        # and a homography left in a per-view normalised frame would constrain a
        # different camera for every view.
        h = _view_homography(object_points, image_points)
        constraints.append(_v_ij(h, 0, 1))
        constraints.append(_v_ij(h, 0, 0) - _v_ij(h, 1, 1))
        pairs.append((object_points, image_points))

    _, singular, vt = np.linalg.svd(np.asarray(constraints))
    # B has five degrees of freedom, so a usable stack must have at least five
    # independent constraints. Checking the *smallest* singular value instead would
    # miss the case that matters: views that are near-copies leave a four-dimensional
    # null space, the smallest value is tiny, and the solve happily returns whichever
    # direction the noise picked — a plausible calibration that is simply wrong.
    rank = int((singular > singular[0] * 1e-6).sum())
    if rank < 5:
        raise CalibrationSolveError(
            "DEGENERATE_VIEWS",
            f"这些视角只提供了 {rank} 个独立约束，解不出唯一的内参（需要 5 个）。"
            "请让板子在画面里换几个明显不同的倾斜角度和距离后重新拍摄。")
    b = vt[-1]
    camera = _intrinsics_from_b(b)
    if camera[0, 0] <= 0 or camera[1, 1] <= 0:
        raise CalibrationSolveError("DEGENERATE_VIEWS", "解出的焦距不是正数，视角数据无法支持标定")
    camera = _refine(camera, pairs)
    return {
        "fx": float(camera[0, 0]),
        "fy": float(camera[1, 1]),
        "cx": float(camera[0, 2]),
        "cy": float(camera[1, 2]),
    }


#: Views below this many cannot determine ``X``: each pair of views gives one
#: motion, and one motion leaves the camera free to rotate about that motion's axis.
#: Three views are two motions, which is the algebraic floor. The wizard asks for
#: eight (``HANDEYE_POSES``) because a hand-moved arm rarely produces two motions
#: that are far enough apart on its own.
MINIMUM_HANDEYE_VIEWS = 3

#: How far the link must have turned between two views before that pair says
#: anything. The encoder resolves 0.088° per count, so 5° is 57 counts of real
#: motion — a pair below this is a copy of a view, not a measurement of a motion.
MINIMUM_MOTION_DEGREES = 5.0

#: How far apart two motions' axes must be. Motions about a single axis leave the
#: camera's rotation about that same axis unconstrained: the solver would be free to
#: spin the camera on the arm's axis and every equation would still hold.
MINIMUM_AXIS_SEPARATION_DEGREES = 10.0

#: A rigid camera on a rigid arm projects the same board identically from every
#: view. This is the median reprojection error above which no rigid mount explains
#: the session — the board was moved, or the arm's reported poses are not its real
#: ones. Deliberately generous: it is a check against a broken session, not a
#: detector quality target.
MAXIMUM_HANDEYE_RESIDUAL_PX = 3.0


def _rotation_angle(rotation: np.ndarray) -> float:
    """The rotation's angle in radians, through the numerically stable trace form."""
    cosine = (float(np.trace(rotation)) - 1.0) / 2.0
    return math.acos(max(-1.0, min(1.0, cosine)))


def _rotation_axis(rotation: np.ndarray) -> np.ndarray | None:
    """The rotation's axis, or ``None`` for a rotation too small to have one."""
    vector = np.array([
        rotation[2, 1] - rotation[1, 2],
        rotation[0, 2] - rotation[2, 0],
        rotation[1, 0] - rotation[0, 1],
    ])
    norm = float(np.linalg.norm(vector))
    if norm < 1e-9:
        return None
    return vector / norm


def _intrinsic_matrix(intrinsics: Mapping[str, Any] | None) -> np.ndarray:
    """The 3×3 pinhole matrix, or a refusal naming what is missing.

    Hand-eye cannot be solved without it, and it cannot be recovered from the same
    views: the board-pose decomposition needs a lens, and a guessed one would tilt
    every board pose by the guess.
    """
    if not isinstance(intrinsics, Mapping):
        raise CalibrationSolveError(
            "MISSING_INTRINSICS",
            "手眼标定需要这台相机的内参（fx/fy/cx/cy）才能解出板子在相机里的位姿；"
            "这一份视角没有带内参。请先完成这台相机的内参步骤，或把内参一并交给解算器。",
        )
    try:
        values = {key: float(intrinsics[key]) for key in ("fx", "fy", "cx", "cy")}
    except (KeyError, TypeError, ValueError) as error:
        raise CalibrationSolveError(
            "MISSING_INTRINSICS", f"内参不是可用的 fx/fy/cx/cy：{error}") from error
    if not all(math.isfinite(value) for value in values.values()) \
            or values["fx"] <= 0 or values["fy"] <= 0:
        raise CalibrationSolveError("MISSING_INTRINSICS", f"内参不是有效的相机参数：{values}")
    return np.array([
        [values["fx"], 0.0, values["cx"]],
        [0.0, values["fy"], values["cy"]],
        [0.0, 0.0, 1.0],
    ])


def _board_pose_in_camera(observed: dict[str, Any], index: int, camera: np.ndarray
                          ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """One view's board pose, as the rotation and translation of board-in-camera."""
    object_points = _as_array(observed, "objectPoints", 2, index)
    image_points = _as_array(observed, "imagePoints", 2, index)
    if object_points.shape != image_points.shape:
        raise CalibrationSolveError(
            "VIEW_INVALID",
            f"第 {index + 1} 个视角的板面点与图像点数量不一致"
            f"（{object_points.shape[0]} 对 {image_points.shape[0]}）")
    if object_points.shape[0] < MINIMUM_BOARD_POINTS:
        raise CalibrationSolveError(
            "VIEW_INVALID",
            f"第 {index + 1} 个视角只认出 {object_points.shape[0]} 个点，"
            f"至少需要 {MINIMUM_BOARD_POINTS} 个")
    decomposed = _pose_from_homography(
        _view_homography(object_points, image_points), camera, object_points)
    if decomposed is None:
        raise CalibrationSolveError(
            "DEGENERATE_VIEWS", f"第 {index + 1} 个视角无法解出板子相对相机的位姿")
    rotation, translation = decomposed
    return object_points, image_points, _pose(rotation, translation)


def _pose(rotation: np.ndarray, translation: np.ndarray) -> np.ndarray:
    pose = np.eye(4)
    pose[:3, :3] = rotation
    pose[:3, 3] = translation
    return pose


def _pose_from_parameters(parameters: np.ndarray) -> np.ndarray:
    return _pose(_rodrigues(parameters[:3]), parameters[3:6])


def _parameters_from_pose(pose: np.ndarray) -> np.ndarray:
    return np.concatenate([_rotvec_from_matrix(pose[:3, :3]), pose[:3, 3]])


def _link_poses_for_views(views: Sequence[dict[str, Any]], parent_link: str,
                          motors: Mapping[str, Mapping[str, Any]] | None) -> list[np.ndarray]:
    """Where the camera's own link was at each view, from its recorded servo counts."""
    poses = []
    for index, view in enumerate(views):
        counts = view.get("pose") if isinstance(view, dict) else None
        if not isinstance(counts, Mapping) or not counts:
            raise CalibrationSolveError(
                "VIEW_INCOMPLETE",
                f"第 {index + 1} 个视角没有记录手臂的舵机读数（pose）；"
                "没有它就没有 AX=XB 里的 A，这一对视角用不了。",
            )
        try:
            angles = arm_kinematics.joint_angles_from_counts(counts, motors)
            poses.append(arm_kinematics.link_pose(parent_link, angles))
        except KinematicsError as error:
            raise CalibrationSolveError(error.code, error.message) from error
    return poses


def _usable_pairs(link_poses: Sequence[np.ndarray], board_poses: Sequence[np.ndarray]
                  ) -> tuple[list[tuple[np.ndarray, np.ndarray]], float]:
    """The view pairs that carry a motion, as ``(A, B)`` with ``A X = X B``.

    ``A = T_j⁻¹ T_i`` is how the link frame moved from view *j* to view *i*, and
    ``B = T_j T_i⁻¹`` is how the board moved in the camera over the same interval.
    Writing them this way is not cosmetic: ``AX = XB`` holds for this pairing and for
    no other, and getting it backwards produces an extrinsic that is the inverse of
    the right answer and still satisfies a self-consistency test.

    Returns the pairs and the largest angle between any two axes of their rotations.
    """
    pairs: list[tuple[np.ndarray, np.ndarray]] = []
    axes: list[np.ndarray] = []
    for i in range(len(link_poses)):
        for j in range(i + 1, len(link_poses)):
            a = np.linalg.inv(link_poses[j]) @ link_poses[i]
            b = board_poses[j] @ np.linalg.inv(board_poses[i])
            if math.degrees(_rotation_angle(a[:3, :3])) < MINIMUM_MOTION_DEGREES:
                continue
            pairs.append((a, b))
            axis = _rotation_axis(a[:3, :3])
            if axis is not None:
                axes.append(axis)
    separation = 0.0
    for first in range(len(axes)):
        for second in range(first + 1, len(axes)):
            cosine = max(-1.0, min(1.0, abs(float(np.dot(axes[first], axes[second])))))
            separation = max(separation, math.degrees(math.acos(cosine)))
    return pairs, separation


def _rotation_from_pairs(pairs: Sequence[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    """``X``'s rotation from ``R_A X = X R_B``, as the null space of a 9×9 stack.

    ``vec`` is column-major, so ``vec(R_A X) = (I ⊗ R_A) vec(X)`` and
    ``vec(X R_B) = (R_Bᵀ ⊗ I) vec(X)``; each pair contributes
    ``[(I ⊗ R_A) − (R_Bᵀ ⊗ I)] vec(X) = 0``. The smallest singular vector is the
    rotation that satisfies every pair at once, and it is projected back onto SO(3)
    because a null vector of a noisy stack is only approximately a rotation.
    """
    rows = []
    for a, b in pairs:
        rows.append(np.kron(np.eye(3), a[:3, :3]) - np.kron(b[:3, :3].T, np.eye(3)))
    # Concatenated, not stacked: numpy's SVD broadcasts a stack of square matrices
    # and would silently return one null vector *per pair* instead of the shared one.
    _, _, vt = np.linalg.svd(np.concatenate(rows, axis=0))
    candidate = vt[-1].reshape(3, 3, order="F")
    u, _, vt_rotation = np.linalg.svd(candidate)
    rotation = u @ vt_rotation
    if np.linalg.det(rotation) < 0:
        rotation = -rotation
    return rotation


def _translation_from_pairs(pairs: Sequence[tuple[np.ndarray, np.ndarray]],
                            rotation: np.ndarray) -> np.ndarray:
    """``t_X`` from ``(R_A − I) t_X = R_X t_B − t_A``, one three-row block per pair."""
    rows = []
    values = []
    for a, b in pairs:
        rows.append(a[:3, :3] - np.eye(3))
        values.append(rotation @ b[:3, 3] - a[:3, 3])
    solution, *_ = np.linalg.lstsq(np.concatenate(rows, axis=0),
                                   np.concatenate(values, axis=0), rcond=None)
    return solution


def _refine_handeye(views: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray]],
                    camera: np.ndarray, start: np.ndarray) -> tuple[np.ndarray, float]:
    """Minimise every corner's reprojection error over the mount and the board's pose.

    Twelve unknowns: the camera on the link, and where the board sits in the arm's
    base frame. The board is one rigid object seen from every view, so a mount that
    is wrong cannot be compensated for view by view — which is exactly what makes
    this objective the right one, and what makes its residual a usable statement
    about whether the session describes anything real.

    ``soft_l1`` for the same reason as the intrinsics solve: one mis-detected corner
    should bend the answer, not drag it.
    """
    from scipy.optimize import least_squares

    def residuals(parameters: np.ndarray) -> np.ndarray:
        link_from_camera = _pose_from_parameters(parameters[:6])
        base_from_board = _pose_from_parameters(parameters[6:12])
        camera_from_link = np.linalg.inv(link_from_camera)
        out = []
        for object_points, image_points, base_from_link in views:
            camera_from_board = camera_from_link @ np.linalg.inv(base_from_link) @ base_from_board
            points = object_points @ camera_from_board[:3, :2].T + camera_from_board[:3, 3]
            projected = points @ camera.T
            out.append((projected[:, :2] / projected[:, 2:3] - image_points).ravel())
        return np.concatenate(out)

    result = least_squares(residuals, start, method="trf", loss="soft_l1",
                           max_nfev=400, ftol=1e-12, xtol=1e-12, gtol=1e-12)
    # The median, not the mean: the robust loss deliberately tolerates a few bad
    # corners, so the number that says "this session is nonsense" must not be the one
    # those corners dominate.
    residuals_px = np.abs(residuals(result.x)).reshape(-1, 2)
    error = float(np.median(np.sqrt((residuals_px ** 2).sum(axis=1))))
    return _pose_from_parameters(result.x[:6]), error


def solve_handeye(views: Sequence[dict[str, Any]], *,
                  intrinsics: Mapping[str, Any] | None = None,
                  motors: Mapping[str, Mapping[str, Any]] | None = None,
                  parent_link: Any = None) -> dict[str, Any]:
    """Where the camera sits on the link that carries it, from board views.

    ``views`` are the wizard's hand-eye records: each holds ``observed`` (the board
    points and the pixels they landed on) and ``pose`` (every servo count of the arm
    at that moment). ``intrinsics`` is the lens already solved for this camera,
    ``motors`` is the motor half of the calibration document in force, and
    ``parent_link`` is the link named in the document's ``extrinsics.parentLink`` —
    the frame the answer is expressed in.

    The result is the document's shape: ``{"parentLink", "xyz", "rpy"}``, with the
    rotation as extrinsic XYZ (``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)``), so it can be
    written into the same field the wizard already writes.
    """
    if len(views) < MINIMUM_HANDEYE_VIEWS:
        raise CalibrationSolveError(
            "NOT_ENOUGH_VIEWS",
            f"手眼标定至少需要 {MINIMUM_HANDEYE_VIEWS} 个视角（每两个视角给一个运动），"
            f"现在只有 {len(views)} 个")

    try:
        link = arm_kinematics.resolve_link(parent_link)
    except KinematicsError as error:
        raise CalibrationSolveError(error.code, error.message) from error

    camera = _intrinsic_matrix(intrinsics)

    board_poses: list[np.ndarray] = []
    observations: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for index, view in enumerate(views):
        observed = view.get("observed") if isinstance(view, dict) else None
        if not isinstance(observed, dict):
            raise CalibrationSolveError("VIEW_INCOMPLETE", f"第 {index + 1} 个视角没有观测数据")
        object_points, image_points, board_pose = _board_pose_in_camera(observed, index, camera)
        board_poses.append(board_pose)
        observations.append((object_points, image_points, board_pose))

    link_poses = _link_poses_for_views(views, link.link, motors)

    pairs, axis_separation = _usable_pairs(link_poses, board_poses)
    if len(pairs) < 2:
        raise CalibrationSolveError(
            "DEGENERATE_MOTIONS",
            f"{len(views)} 个视角里只有 {len(pairs)} 对姿态之间的转动超过 "
            f"{MINIMUM_MOTION_DEGREES:.0f}°：这些视角几乎是同一个姿态，"
            "解不出相机装在哪。请把手臂带到明显不同的姿态后重新采集。",
        )
    if axis_separation < MINIMUM_AXIS_SEPARATION_DEGREES:
        raise CalibrationSolveError(
            "DEGENERATE_MOTIONS",
            f"这些姿态的转动轴几乎重合（最大夹角只有 {axis_separation:.1f}°）："
            "绕单根轴的运动定不下相机绕这根轴的朝向，解出来的结果可以任意旋转而方程照样成立。"
            "请让手臂换几个不同方向的姿态（例如同时改变肩部旋转和抬升）。",
        )

    rotation = _rotation_from_pairs(pairs)
    translation = _translation_from_pairs(pairs, rotation)
    mount = _pose(rotation, translation)
    if not np.all(np.isfinite(mount)):
        raise CalibrationSolveError(
            "DEGENERATE_MOTIONS", "这些视角解出的相机位姿不是有限值，运动约束不足")

    # The board's pose in the base frame, from the first view and the initial mount:
    # ``base_from_board = base_from_link · link_from_camera · camera_from_board``.
    board = link_poses[0] @ mount @ board_poses[0]
    start = np.concatenate([_parameters_from_pose(mount), _parameters_from_pose(board)])
    mount, residual_px = _refine_handeye(
        [(points, pixels, link_pose)
         for (points, pixels, _board_pose), link_pose in zip(observations, link_poses)],
        camera, start)
    if not np.all(np.isfinite(mount)):
        raise CalibrationSolveError("DEGENERATE_MOTIONS", "优化后的相机位姿不是有限值")
    if residual_px > MAXIMUM_HANDEYE_RESIDUAL_PX:
        raise CalibrationSolveError(
            "INCONSISTENT_VIEWS",
            f"这 {len(views)} 个视角无法用「相机刚性固定在 {link.link} 上」解释："
            f"重投影误差中位数 {residual_px:.1f} 像素（上限 {MAXIMUM_HANDEYE_RESIDUAL_PX:.0f}）。"
            "常见原因：标定板在采集过程中动过，或者手臂位姿与相机画面不是同一时刻的。"
            "请把板子固定住、保持手臂不动再按一次。",
        )

    return {
        "parentLink": link.link,
        "xyz": [float(value) for value in mount[:3, 3]],
        "rpy": _rotation_to_rpy(mount[:3, :3]),
    }


def _rotation_to_rpy(rotation: np.ndarray) -> list[float]:
    """Roll/pitch/yaw for ``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)``, the document's form.

    The same convention the simulation derives its own documents in
    (``tangying_sim/calibration.py:44``), so a solved extrinsic and a derived one can
    be compared without either being converted.
    """
    pitch = math.asin(max(-1.0, min(1.0, -float(rotation[2, 0]))))
    if abs(rotation[2, 0]) < 0.99999:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:  # Gimbal lock: pitch is ±90°, and roll and yaw then describe one turn.
        roll = math.atan2(-rotation[1, 2], rotation[1, 1])
        yaw = 0.0
    return [roll, pitch, yaw]


class IntrinsicsSolver:
    """What this gateway solves, behind the wizard's solver interface.

    Both halves are real. Intrinsics is Zhang's method on the kept views; hand-eye is
    ``AX = XB``, with ``A`` from the arm's forward kinematics and ``B`` from the
    board's pose in each view. Both refuse rather than answer when the data cannot
    determine the result: the same refusal contract the wizard already understands.
    """

    def solve_intrinsics(self, camera: str, views: list[dict[str, Any]]) -> dict[str, Any]:
        del camera  # the views already carry everything the solve needs
        return solve_intrinsics(views)

    def solve_handeye(self, views: list[dict[str, Any]], *,
                      intrinsics: Mapping[str, Any] | None = None,
                      motors: Mapping[str, Mapping[str, Any]] | None = None,
                      parent_link: Any = None) -> dict[str, Any]:
        return solve_handeye(views, intrinsics=intrinsics, motors=motors,
                             parent_link=parent_link)
