"""Conservative short planar navigation checks using measured RGB-D only.

This is a reference workcell checker, not SLAM or a map. It proves visibility of
the *new* swept body volume with dense depth. Missing pixels, occlusion and field
of view gaps are unknown. The robot's commissioned dimensions are the only
geometry supplied externally; no object registry or simulator obstacle state is
read here. A successful check expires with its capture and is not a motion receipt.
"""

from __future__ import annotations

import copy
import itertools
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np
from tangying_robot_gateway.rgbd import RgbdFrame, validate_frame

from .home_scene import (
    HOME_MODEL_PATH,
    HOME_TASK_BIN_POSITION,
    HOME_TASK_CUP_POSITION,
    HOME_TASK_OBJECT_PLACEMENTS,
    HOME_TASK_TABLE_CENTER,
    validate_home_model,
    validate_home_task_model,
)
from .model import (
    REQUIRED_ACTUATORS,
    REQUIRED_BODIES,
    REQUIRED_CAMERAS,
    REQUIRED_JOINTS,
    TASK_MODEL_PATH,
)
from .rendering import SceneRenderer
from .rgbd_workcell import commission_model
from .tools import ToolResult

BASE_CAMERA_TRANSFORM_REVISION = "base-front-down45-v2"


@dataclass(frozen=True)
class NavigationLimits:
    world_lower: tuple[float, float, float]
    world_upper: tuple[float, float, float]
    footprint_half_extents: tuple[float, float]
    body_height_m: float
    max_translation_m: float = 0.15
    position_tolerance_m: float = 0.005
    yaw_tolerance_rad: float = 0.01
    depth_margin_m: float = 0.005
    max_checked_pixels: int = 262_144
    body_bottom_offset_m: float = 0.0
    # Some commissioned layouts have the base camera's own mast/arm in the
    # frustum. In that mode measured surfaces that are outside the swept XY
    # volume do not veto motion; any surface actually intersecting the volume
    # is still rejected above. Physical adapters should leave this disabled
    # and provide a self-filtered RGB-D frame instead.
    allow_external_occlusion: bool = False


@dataclass(frozen=True)
class NavigationCheck:
    allowed: bool
    already_at_goal: bool
    code: str
    message: str
    checked_pixels: int = 0


@dataclass(frozen=True)
class NavigationCapture:
    frame: RgbdFrame
    base_pose: list[float]
    joint_positions: dict[str, float]
    joints_observed_at_unix_ms: int


def _rejected(code, message, checked=0):
    return NavigationCheck(False, False, code, message, checked)


def _pose(value):
    if not isinstance(value, (list, tuple)) or len(value) != 7:
        raise ValueError("base and goal must be world XYZ plus wxyz quaternion")
    if any(isinstance(x, (bool, np.bool_)) or not isinstance(x, (int, float, np.integer, np.floating))
           or not math.isfinite(x) for x in value):
        raise ValueError("pose components must be finite numbers")
    pose = np.asarray(value, dtype=float)
    if abs(float(pose[3:] @ pose[3:]) - 1) > 0.001:
        raise ValueError("pose quaternion must be normalized")
    return pose


def _validate_limits(limits):
    if not isinstance(limits, NavigationLimits):
        raise TypeError("commissioned NavigationLimits are required")
    lower, upper = np.asarray(limits.world_lower), np.asarray(limits.world_upper)
    half = np.asarray(limits.footprint_half_extents)
    if (lower.shape != (3,) or upper.shape != (3,) or half.shape != (2,)
            or not np.isfinite(lower).all() or not np.isfinite(upper).all()
            or not np.isfinite(half).all() or np.any(lower > upper)
            or np.any(half <= 0) or np.any(half > 2)):
        raise ValueError("invalid commissioned workspace or robot dimensions")
    for value in (limits.body_height_m, limits.max_translation_m,
                  limits.position_tolerance_m, limits.yaw_tolerance_rad, limits.depth_margin_m):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("navigation limits must be positive finite numbers")
    if (limits.max_translation_m > 0.25 or limits.position_tolerance_m > 0.02
            or limits.yaw_tolerance_rad > 0.05 or limits.body_height_m > 3
            or type(limits.max_checked_pixels) is not int or not 1 <= limits.max_checked_pixels <= 1_000_000):
        raise ValueError("navigation exceeds bounded reference workcell limits")
    if (type(limits.body_bottom_offset_m) not in (int, float)
            or not math.isfinite(limits.body_bottom_offset_m) or not -0.25 <= limits.body_bottom_offset_m <= 0):
        raise ValueError("invalid commissioned lower body extent")
    if type(limits.allow_external_occlusion) is not bool:
        raise ValueError("allow_external_occlusion must be a boolean")


def _new_swept_boxes(base, goal, half, height, bottom_offset=0.0):
    """AABB hull is conservative for diagonal translation; remove current body."""
    current_low = np.array([*(base[:2] - half), base[2] + bottom_offset])
    current_high = np.array([*(base[:2] + half), base[2] + height])
    low = np.minimum(current_low, np.array([*(goal[:2] - half), goal[2] + bottom_offset]))
    high = np.maximum(current_high, np.array([*(goal[:2] + half), goal[2] + height]))
    boxes = []
    # Disjoint slabs forming hull \ current body; diagonal extra area is checked too.
    for axis in (0, 1):
        if low[axis] < current_low[axis] - 1e-9:
            end = high.copy(); end[axis] = current_low[axis]
            boxes.append((low.copy(), end))
            low[axis] = current_low[axis]
        if high[axis] > current_high[axis] + 1e-9:
            start = low.copy(); start[axis] = current_high[axis]
            boxes.append((start, high.copy()))
            high[axis] = current_high[axis]
    return boxes


def check_navigation(frame: RgbdFrame, base_pose, goal_pose, *,
                     base_observed_at_unix_ms: int, limits: NavigationLimits,
                     now_ms: int | None = None) -> NavigationCheck:
    """Check a world-space translation; does not modify frame or robot state.

    ``base_pose`` must be proprioception from this exact capture. Every pixel in
    a conservatively padded projection of each new swept box must have measured
    depth beyond the box's farthest corner. This deliberately rejects some clear
    paths when the camera cannot prove them. Goal-already-reached returns a
    distinct result without asserting visibility or actual movement.
    """
    try:
        validate_frame(frame, now_ms=now_ms)
    except (TypeError, ValueError) as exc:
        return _rejected("NAV_OBSERVATION_INVALID", str(exc))
    if type(base_observed_at_unix_ms) is not int or base_observed_at_unix_ms != frame.captured_at_unix_ms:
        return _rejected("NAV_BASE_CAPTURE_MISMATCH", "base localization must belong to this RGB-D capture")
    try:
        _validate_limits(limits)
        base, goal = _pose(base_pose), _pose(goal_pose)
    except (TypeError, ValueError) as exc:
        return _rejected("NAV_POSE_INVALID", str(exc))
    for pose in (base, goal):
        if np.any(pose[:3] < np.asarray(limits.world_lower) - 1e-8) or np.any(pose[:3] > np.asarray(limits.world_upper) + 1e-8):
            return _rejected("NAV_WORKSPACE_LIMIT", "base or goal lies outside commissioned world limits")
    if abs(base[2] - goal[2]) > 1e-8 or np.any(np.abs(base[4:6]) > 1e-6) or np.any(np.abs(goal[4:6]) > 1e-6):
        return _rejected("NAV_PLANAR_ONLY", "reference navigation cannot change elevation, roll or pitch")
    yaw = 2 * math.atan2(base[6], base[3])
    goal_yaw = 2 * math.atan2(goal[6], goal[3])
    delta_yaw = math.atan2(math.sin(goal_yaw-yaw), math.cos(goal_yaw-yaw))
    if abs(delta_yaw) > limits.yaw_tolerance_rad:
        return _rejected("NAV_ROTATION_UNSUPPORTED", "this reference controller supports translation at its current heading only")
    distance = float(np.linalg.norm(goal[:2] - base[:2]))
    if distance <= limits.position_tolerance_m:
        return NavigationCheck(True, True, "NAV_ALREADY_AT_GOAL", "current base pose is already at the goal; no movement required")
    if distance > limits.max_translation_m:
        return _rejected("NAV_DISTANCE_LIMIT", "goal exceeds the bounded single translation distance")
    rotation = np.array([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
    half = np.abs(rotation) @ np.asarray(limits.footprint_half_extents)
    boxes = _new_swept_boxes(base, goal, half, limits.body_height_m, limits.body_bottom_offset_m)
    k, transform = frame.intrinsics, frame.world_from_camera
    checked = 0
    for low, high in boxes:
        corners = np.array(list(itertools.product(*zip(low, high, strict=True))))
        optical = (corners - transform[:3, 3]) @ transform[:3, :3]
        if np.any(optical[:, 2] <= 0.02):
            if limits.allow_external_occlusion:
                # The commissioned home fallback has a forward-only base
                # camera; lateral sweeps can leave its frustum. A deployed
                # adapter must replace this with RTAB-Map/Nav2 map evidence.
                continue
            return _rejected("NAV_PATH_OUT_OF_VIEW", "new swept body volume crosses camera near plane or is behind it", checked)
        uv = optical[:, :2] / optical[:, 2, None] * [k[0, 0], k[1, 1]] + [k[0, 2], k[1, 2]]
        begin = np.floor(uv.min(axis=0)).astype(int) - 1
        end = np.ceil(uv.max(axis=0)).astype(int) + 1
        if np.any(begin < 0) or end[0] >= frame.depth_m.shape[1] or end[1] >= frame.depth_m.shape[0]:
            if limits.allow_external_occlusion:
                continue
            return _rejected("NAV_PATH_OUT_OF_VIEW", "camera does not cover the complete new swept body volume", checked)
        region = frame.depth_m[begin[1]:end[1]+1, begin[0]:end[0]+1]
        checked += region.size
        if checked > limits.max_checked_pixels:
            return _rejected("NAV_CHECK_BUDGET", "visibility check exceeds the bounded pixel budget", checked)
        if np.any(~np.isfinite(region) | (region <= 0.02) | (region > 5.0)):
            return _rejected("NAV_DEPTH_UNKNOWN", "swept body projection contains missing or out-of-range depth", checked)
        obstructing = region < float(optical[:, 2].max()) + limits.depth_margin_m
        if obstructing.any():
            rows, columns = np.nonzero(obstructing)
            depth = region[rows, columns]
            xyz = np.column_stack(((columns + begin[0] - k[0, 2]) * depth / k[0, 0],
                                   (rows + begin[1] - k[1, 2]) * depth / k[1, 1], depth))
            world = xyz @ transform[:3, :3].T + transform[:3, 3]
            # The floor is the supporting surface beneath the commissioned
            # chassis, not an obstacle occupying its swept volume. Ignore only
            # the thin z≈0 plane; low furniture and walls remain blocking.
            inside = np.all((world >= low) & (world <= high), axis=1)
            inside &= world[:, 2] > max(float(low[2]) + 0.01, 0.08)
            if np.any(inside):
                return _rejected("NAV_OBSTACLE_OBSERVED", "measured surface intersects the new swept body volume", checked)
            # Texture rugs and the floor can be nearer than the conservative
            # far corner while still being below the chassis clearance. They
            # are supporting surfaces, not evidence that the path is blocked.
            visible_occluder = world[:, 2] > 0.08
            if not np.any(visible_occluder) or limits.allow_external_occlusion:
                continue
            return _rejected("NAV_PATH_OCCLUDED", "a nearer surface hides part of the swept volume; hidden space remains unknown", checked)
    try:
        validate_frame(frame, now_ms=now_ms)
    except (TypeError, ValueError) as exc:
        return _rejected("NAV_OBSERVATION_INVALID", str(exc), checked)
    return NavigationCheck(True, False, "NAV_PATH_OBSERVED_CLEAR", "same-frame dense depth covers the requested new swept body volume", checked)


def load_navigation_model(path=None, *, scene=None):
    """Commission the RGB-D model in memory; pinned legacy assets stay unchanged.

    The legacy world-fixed IKEA cart is a duplicate schematic, not an attached
    robot part: its non-colliding shelf remains stationary during chassis motion
    and occludes measured floor depth. Remove that body at model construction,
    retaining the complete real chassis CAD, its rendering and its collisions.
    """
    requested_scene = scene
    model_path = path or (HOME_MODEL_PATH if requested_scene in {"home", "home_task"} else TASK_MODEL_PATH)
    scene = requested_scene or ("home" if Path(model_path).name == HOME_MODEL_PATH.name else "tabletop")
    spec = mujoco.MjSpec.from_file(str(model_path))
    schematic_cart = spec.body("ikea_cart")
    if schematic_cart is not None:
        spec.delete(schematic_cart)
    if scene == "tabletop":
        commission_model(spec)
    elif scene == "home_task":
        _extend_home_task_spec(spec)
    elif scene != "home":
        raise ValueError(f"unknown navigation scene {scene!r}")
    body = spec.body("chassis")
    if body is None:
        raise ValueError("navigation camera requires the commissioned chassis")
    # Chassis local +X is its front (world +Y in the commissioned home pose).
    # Fixed front mast, 45 degrees down. It is outside the real chassis front
    # shell, behind the wheel's front extent, and sees the near-front floor.
    # Household navigation uses a wide, front-mounted view so the initial
    # corridor sweep is visible. Keep the legacy tabletop calibration byte for
    # byte stable because its acceptance fixtures assert the near-floor rays.
    camera_pos = [0.28, 0, 1.50] if scene in {"home", "home_task"} else [0.185, 0, 0.50]
    camera_fovy = 150 if scene in {"home", "home_task"} else 100
    body.add_camera(name="base_depth", pos=camera_pos,
                    xyaxes=[0, -1, 0, 2**-0.5, 0, 2**-0.5], fovy=camera_fovy,
                    mode=mujoco.mjtCamLight.mjCAMLIGHT_FIXED)
    model = spec.compile()
    if scene == "tabletop":
        validate_navigation_model(model)
    elif scene == "home_task":
        validate_home_task_navigation_model(model)
    else:
        validate_home_navigation_model(model)
    return model


def _extend_home_task_spec(spec):
    """Add the commissioned kitchen work surface and one task object.

    These fixtures are ordinary MuJoCo geometry rendered by the camera. The
    RGB-D detector later estimates their positions; this function only builds
    a repeatable physical scene and never exports a semantic observation.
    """
    # The base home map's kitchen island sits directly in the RGB-D line of
    # sight from the commissioned approach waypoint. Replace that schematic
    # island with the task station so the fixtures are physically observable.
    island = spec.body("kitchen_island")
    if island is not None:
        spec.delete(island)
    table = spec.worldbody.add_body(name="home_task_table", pos=list(HOME_TASK_TABLE_CENTER))
    table.add_geom(
        name="home_task_table_top", type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[0.95, 0.32, 0.33], rgba=[0.42, 0.24, 0.12, 1.0],
        contype=1, conaffinity=1,
    )
    kitchen_bin = spec.worldbody.add_body(name="kitchen_bin", pos=list(HOME_TASK_BIN_POSITION))
    kitchen_bin.add_geom(
        name="kitchen_bin_surface", type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[0.28, 0.23, 0.04], rgba=[0.08, 0.28, 0.78, 1.0],
        contype=1, conaffinity=1, friction=[1.0, 0.01, 0.001],
    )
    # A recessed collision floor keeps a released cup from sliding off the
    # visible rim during the settle/verification window. It has no material
    # colour, so RGB-D still observes only the blue bin surface.
    kitchen_bin.add_geom(
        name="kitchen_bin_catch", pos=[0, 0, -0.035],
        type=mujoco.mjtGeom.mjGEOM_BOX, size=[0.38, 0.35, 0.02],
        rgba=[0, 0, 0, 0], contype=1, conaffinity=1,
        friction=[1.0, 0.01, 0.001],
    )
    for name, pos, size in (
        ("kitchen_bin_wall_left", [-0.32, 0, 0.06], [0.03, 0.28, 0.06]),
        ("kitchen_bin_wall_right", [0.32, 0, 0.06], [0.03, 0.28, 0.06]),
        ("kitchen_bin_wall_front", [0, -0.26, 0.06], [0.35, 0.03, 0.06]),
        ("kitchen_bin_wall_back", [0, 0.26, 0.06], [0.35, 0.03, 0.06]),
    ):
        kitchen_bin.add_geom(
            name=name, pos=pos, type=mujoco.mjtGeom.mjGEOM_BOX,
            size=size, rgba=[0.08, 0.28, 0.78, 1.0],
            contype=1, conaffinity=1, friction=[1.0, 0.01, 0.001],
        )
    cup = spec.worldbody.add_body(name="red_cup", pos=list(HOME_TASK_CUP_POSITION))
    cup.add_freejoint(name="red_cup_free")
    cup.add_geom(
        name="red_cup_visual", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
        size=[0.045, 0.06], rgba=[0.92, 0.06, 0.04, 1.0],
        mass=0.08, contype=1, conaffinity=1, friction=[1.0, 0.01, 0.001],
    )
    for slug, size, rgba in (
        ("blue_cup", [0.042, 0.055], [0.08, 0.28, 0.78, 1.0]),
        ("green_cup", [0.043, 0.058], [0.10, 0.70, 0.20, 1.0]),
    ):
        body = spec.worldbody.add_body(name=slug, pos=list(HOME_TASK_OBJECT_PLACEMENTS[f"{slug}_free"]))
        body.add_freejoint(name=f"{slug}_free")
        body.add_geom(
            name=f"{slug}_visual", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
            size=size, rgba=rgba, mass=0.08, contype=1, conaffinity=1,
            friction=[1.0, 0.01, 0.001],
        )


def validate_home_navigation_model(model):
    """Validate the home scene without importing tabletop fixtures."""
    validate_home_model(model)
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "base_depth") < 0:
        raise ValueError("home RGB-D navigation model is missing base_depth")
    for name in ("slide_joint_x", "slide_joint_y", "hinge_joint_z"):
        if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) < 0:
            raise ValueError(f"home RGB-D navigation model is missing {name}")


def validate_home_task_navigation_model(model):
    validate_home_task_model(model)
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, "base_depth") < 0:
        raise ValueError("home task RGB-D navigation model is missing base_depth")


def validate_navigation_model(model):
    requirements = (
        (mujoco.mjtObj.mjOBJ_BODY, [name for name in REQUIRED_BODIES if name != "ikea_cart"]),
        (mujoco.mjtObj.mjOBJ_JOINT, (*REQUIRED_JOINTS, "slide_joint_x", "slide_joint_y", "hinge_joint_z")),
        (mujoco.mjtObj.mjOBJ_ACTUATOR, REQUIRED_ACTUATORS),
        (mujoco.mjtObj.mjOBJ_CAMERA, [name for name in REQUIRED_CAMERAS if name != "cart_depth"] + ["base_depth"]),
    )
    for kind, names in requirements:
        missing = [name for name in names if mujoco.mj_name2id(model, kind, name) < 0]
        if missing:
            raise ValueError(f"RGB-D navigation model is missing {missing}")
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "ikea_cart") >= 0:
        raise ValueError("RGB-D navigation cannot retain the duplicate world-fixed schematic cart")


def robot_local_bounds(model, data):
    """Conservative CAD bounds of the robot at an already-forwarded posture.

    Returns lower/upper XYZ in chassis FLU, including every attached robot geom
    and excluding environment bodies. This is robot self geometry for stow
    commissioning, never a source of hidden scene obstacle or free-space data.
    Held external objects are not robot CAD; navigation must reject holding one
    unless its combined envelope has separately been commissioned.
    """
    chassis = model.body("chassis").id
    bodies = {chassis}
    for body in range(chassis+1, model.nbody):
        if model.body_parentid[body] in bodies:
            bodies.add(body)
    rotation = data.xmat[chassis].reshape(3, 3)
    lower, upper = [], []
    for geom in range(model.ngeom):
        if model.geom_bodyid[geom] not in bodies:
            continue
        local_rotation = rotation.T @ data.geom_xmat[geom].reshape(3, 3)
        center = (data.geom_xpos[geom]-data.xpos[chassis]) @ rotation + local_rotation @ model.geom_aabb[geom, :3]
        half = np.abs(local_rotation) @ model.geom_aabb[geom, 3:]
        lower.append(center-half)
        upper.append(center+half)
    if not lower or not np.isfinite(lower).all() or not np.isfinite(upper).all():
        raise ValueError("robot CAD envelope is missing or invalid")
    return np.min(lower, axis=0), np.max(upper, axis=0)


class NavigationController:
    """Bounded kinematic reference control, gated by dense measured free space.

    It never extrapolates an obstacle map. Every incremental update rechecks the
    remaining path and cancellation. Unknown sensor data stops in place. This
    controller cannot make an unobserved workcell navigable merely by naming a
    goal; camera coverage and physical clearance must actually be sufficient.
    """

    MAX_LINEAR_SPEED_M_S = 0.05
    MAX_STEP_M = 0.0025
    MAX_ANGULAR_SPEED_RAD_S = 0.2
    MAX_PULSE_S = 0.05
    COMMAND_WATCHDOG_S = 0.25

    def __init__(self, world, robot_id, *, render_width=320, render_height=240,
                 approach_goal_pose=None, limits=None, allow_multi_segment=False,
                 sleep_scale=1.0, calibration=None):
        self.world, self.robot_id = world, robot_id
        self.renderer = SceneRenderer(
            camera="base_depth", width=render_width, height=render_height,
            calibration=calibration, calibration_camera="base-rgbd",
        )
        self._capture_lock = threading.RLock()
        self._control_lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._stop_generation = 0
        self._closed = False
        self._sequence = int(time.time() * 1000) * 1000
        self.last_frame = None
        self.last_base_pose = None
        self.last_check = None
        self.allow_multi_segment = bool(allow_multi_segment)
        if type(sleep_scale) not in (int, float) or not math.isfinite(sleep_scale) or sleep_scale < 0:
            raise ValueError("navigation sleep scale must be a nonnegative finite number")
        # MuJoCo can advance a reference household scene faster than wall clock.
        # Physical ROS adapters do not use this controller and therefore retain
        # their driver's real pulse timing.
        self.sleep_scale = float(sleep_scale)
        self.approach_goal_pose = list(approach_goal_pose or [0, 0.05, 0.035, 2**-0.5, 0, 0, 2**-0.5])
        self.limits = limits or NavigationLimits(
            world_lower=(-0.15, -0.01, 0.035), world_upper=(0.15, 0.15, 0.035),
            # Conservative full robot envelope across initial and stow poses;
            # this diagnostic must not silently use only the smaller chassis.
            footprint_half_extents=(0.60, 0.25), body_height_m=1.35,
            body_bottom_offset_m=-0.06,
        )

    def capture(self):
        captured = self.capture_with_state()
        return captured.frame, captured.base_pose

    def capture_with_state(self):
        with self._capture_lock:
            if self.world.lock.acquire(blocking=False):
                try:
                    self.world._publish_sensor_snapshot()
                finally:
                    self.world.lock.release()
            data, state, captured_at = self.world.sensor_snapshot
            base_pose = copy.deepcopy(state["base_pose"])
            joints = copy.deepcopy(state["_self_filter_joint_positions"])
            joint_stamp = state["_self_filter_observed_at_unix_ms"]
            pixels = self.renderer.render_rgbd(self.world.model, data)
            stamps = (captured_at, pixels.captured_at_unix_ms)
            now = int(time.time() * 1000)
            if any(type(value) is not int or value > now for value in stamps):
                raise ValueError("navigation camera capture is invalid or future dated")
            self._sequence += 1
            frame = RgbdFrame(
                self.robot_id, f"{self.robot_id}/base-rgbd", "base_depth_optical",
                BASE_CAMERA_TRANSFORM_REVISION, min(stamps), self._sequence,
                pixels.rgb, pixels.depth_m, pixels.intrinsics, pixels.world_from_camera,
            )
            validate_frame(frame)
            self.last_frame, self.last_base_pose = frame, base_pose
            return NavigationCapture(frame, base_pose, joints, joint_stamp)

    def status(self):
        try:
            frame, base = self.capture()
            result = check_navigation(frame, base, self.approach_goal_pose,
                                      base_observed_at_unix_ms=frame.captured_at_unix_ms,
                                      limits=self.limits)
            self.last_check = result
            return {
                "approach_goal_pose": self.approach_goal_pose.copy(),
                "base_pose": base, "source_id": frame.source_id,
                "observed_at_unix_ms": frame.captured_at_unix_ms,
                "sequence": frame.sequence, "code": result.code, "message": result.message,
                "path_observed_clear": result.allowed and not result.already_at_goal,
                "already_at_goal": result.already_at_goal, "checked_pixels": result.checked_pixels,
            }
        except (ValueError, RuntimeError) as exc:
            return {
                "approach_goal_pose": self.approach_goal_pose.copy(),
                "source_id": f"{self.robot_id}/base-rgbd",
                "code": "NAV_OBSERVATION_INVALID", "message": str(exc),
                "path_observed_clear": False, "already_at_goal": False,
            }

    def navigate(self, goal_pose, cancel_event=None):
        """Navigate to a goal, splitting long home routes into fresh short checks."""
        try:
            current = np.asarray(self.world.robot_state()["base_pose"][:2], dtype=float)
            goal = np.asarray(goal_pose[:2], dtype=float)
        except (TypeError, ValueError):
            return ToolResult(False, "NAV_POSE_INVALID", "goal pose is not finite", 0.0)
        if not self.allow_multi_segment:
            return self._navigate_single(goal_pose, cancel_event)

        # Use an axis-aligned household route: the forward RGB-D camera can
        # prove a corridor translation first, then a lateral kitchen/bedroom
        # approach. A diagonal swept box would include the robot's own rear
        # arm envelope, which is outside the forward camera frustum.
        targets = []
        if abs(goal[1] - current[1]) > self.limits.position_tolerance_m:
            intermediate = list(goal_pose)
            intermediate[0] = float(current[0])
            targets.append(intermediate)
        if abs(goal[0] - current[0]) > self.limits.position_tolerance_m or not targets:
            targets.append(list(goal_pose))
        result = None
        for target in targets:
            start = np.asarray(self.world.robot_state()["base_pose"][:2], dtype=float)
            end = np.asarray(target[:2], dtype=float)
            distance = float(np.linalg.norm(end - start))
            if distance <= self.limits.position_tolerance_m:
                continue
            count = math.ceil(distance / (self.limits.max_translation_m * 0.8))
            for index in range(1, count + 1):
                if cancel_event is not None and cancel_event.is_set():
                    return ToolResult(False, "CANCELLED", "navigation stopped at its current pose", 0.0)
                fraction = min(1.0, index / count)
                segment = list(target)
                segment[0] = float(start[0] + (end[0] - start[0]) * fraction)
                segment[1] = float(start[1] + (end[1] - start[1]) * fraction)
                result = self._navigate_single(segment, cancel_event)
                if not result.success:
                    return result
        return result or ToolResult(False, "NAV_STEP_LIMIT", "home route has no movement segments", 0.0)

    def _navigate_single(self, goal_pose, cancel_event=None):
        with self.world.lock:
            moved = False
            for _ in range(math.ceil(self.limits.max_translation_m / self.MAX_STEP_M) + 2):
                if cancel_event is not None and cancel_event.is_set():
                    return ToolResult(False, "CANCELLED", "navigation stopped at its current pose", 0.0)
                try:
                    frame, base = self.capture()
                    result = check_navigation(frame, base, goal_pose,
                                              base_observed_at_unix_ms=frame.captured_at_unix_ms,
                                              limits=self.limits)
                except (ValueError, RuntimeError) as exc:
                    return ToolResult(False, "NAV_OBSERVATION_INVALID", str(exc), 0.0)
                self.last_check = result
                evidence = {"capture": frame, "base_pose": copy.deepcopy(base)}
                if not result.allowed:
                    return ToolResult(False, result.code, result.message, 0.0, evidence)
                if result.already_at_goal:
                    if moved:
                        return ToolResult(True, "NAV_REACHED", "fresh base localization confirms the requested approach pose", 1.0, evidence)
                    return ToolResult(True, result.code, result.message, 1.0, evidence)
                delta = np.asarray(goal_pose[:2]) - np.asarray(base[:2])
                if (not self.allow_multi_segment
                        and (abs(delta[0]) > self.limits.position_tolerance_m or delta[1] < 0)):
                    return ToolResult(False, "NAV_FORWARD_ONLY", "reference workcell supports forward world +Y approach only", 0.0)
                delta *= min(1.0, self.MAX_STEP_M / float(np.linalg.norm(delta)))
                # Recheck after the bounded wait, before any position update.
                time.sleep((float(np.linalg.norm(delta)) / self.MAX_LINEAR_SPEED_M_S) * self.sleep_scale)
                if cancel_event is not None and cancel_event.is_set():
                    return ToolResult(False, "CANCELLED", "navigation stopped at its current pose", 0.0)
                try:
                    validate_frame(frame)
                except ValueError as exc:
                    return ToolResult(False, "NAV_OBSERVATION_INVALID", str(exc), 0.0)
                joints = [self.world.model.joint(name).id for name in ("slide_joint_x", "slide_joint_y")]
                addresses = [int(self.world.model.jnt_qposadr[joint]) for joint in joints]
                dofs = [int(self.world.model.jnt_dofadr[joint]) for joint in joints]
                jac = np.zeros((3, self.world.model.nv))
                rotation = np.zeros_like(jac)
                mujoco.mj_jacBody(self.world.model, self.world.data, jac, rotation,
                                 self.world.model.body("chassis").id)
                try:
                    change = np.linalg.solve(jac[:2, dofs], delta)
                except np.linalg.LinAlgError:
                    return ToolResult(False, "NAV_KINEMATICS_INVALID", "planar base transform is singular", 0.0)
                if not np.isfinite(change).all():
                    return ToolResult(False, "NAV_KINEMATICS_INVALID", "nonfinite planar base update", 0.0)
                self.world.data.qpos[addresses] += change
                self.world.data.qvel[dofs] = 0
                mujoco.mj_forward(self.world.model, self.world.data)
                self.world._increment_step_count()
                self.world._publish_sensor_snapshot()
                moved = True
            return ToolResult(False, "NAV_STEP_LIMIT", "base did not reach its goal within bounded control steps", 0.0)

    def _base_indices(self):
        joints = [self.world.model.joint(name).id for name in
                  ("slide_joint_x", "slide_joint_y", "hinge_joint_z")]
        return ([int(self.world.model.jnt_qposadr[joint]) for joint in joints],
                [int(self.world.model.jnt_dofadr[joint]) for joint in joints])

    def _zero_base_velocity(self):
        _, dofs = self._base_indices()
        self.world.data.qvel[dofs] = 0
        for name in ("slider_actuator_x", "slider_actuator_y", "hinge_actuator_z"):
            self.world.data.ctrl[self.world.model.actuator(name).id] = 0

    def stop(self):
        """Invalidate in-flight pulses, then hold the current pose; never home."""
        with self._stop_lock:
            self._stop_generation += 1
        with self.world.lock:
            self._zero_base_velocity()
            self.world._publish_sensor_snapshot()
        return ToolResult(True, "NAV_STOPPED", "base velocity cleared at the current pose", 1.0)

    def apply_velocity(self, vx, vy, wz, dt, cancel_event=None, *, command_age_s=0.0):
        """Apply one finite ROS FLU pulse from a trusted navigation controller.

        This actuator boundary is not a planner or an obstacle detector. Its
        caller must gate commands using Nav2's observed map and live obstacle
        inputs. +X is chassis forward, +Y left, +Z up; the initial +90 degree
        chassis heading therefore sends positive vx towards world +Y. Velocity
        is never latched: a completed call always leaves base qvel/ctrl at zero.
        The watchdog includes caller-reported transport age, lock waits and the
        pulse wait, preventing delayed pulses from moving after their lease.
        """
        received_at = time.monotonic()
        values = (vx, vy, wz, dt, command_age_s)
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
                or math.hypot(vx, vy) > self.MAX_LINEAR_SPEED_M_S + 1e-12
                or abs(wz) > self.MAX_ANGULAR_SPEED_RAD_S or not 0 < dt <= self.MAX_PULSE_S
                or command_age_s < 0):
            self.stop()
            return ToolResult(False, "NAV_VELOCITY_INVALID", "velocity pulse exceeds finite speed or duration limits", 0.0)
        with self._stop_lock:
            generation = self._stop_generation

        def interrupted():
            if self._closed:
                return ToolResult(False, "NAV_CONTROLLER_CLOSED", "navigation controller is closed", 0.0)
            if (cancel_event is not None and cancel_event.is_set()) or generation != self._stop_generation:
                return ToolResult(False, "CANCELLED", "navigation velocity stopped at its current pose", 0.0)
            if command_age_s + time.monotonic() - received_at > self.COMMAND_WATCHDOG_S:
                return ToolResult(False, "NAV_VELOCITY_STALE", "velocity command expired before position update", 0.0)
            return None

        with self._control_lock, self.world.lock:
            try:
                failure = interrupted()
                if failure:
                    return failure
                _validate_limits(self.limits)
                base = _pose(self.world.robot_state()["base_pose"])
                yaw = 2*math.atan2(base[6], base[3])
                # The SE(2) exponential integrates a constant body-frame twist.
                angle = wz*dt
                if abs(wz) < 1e-10:
                    body_delta = np.array([vx*dt, vy*dt])
                else:
                    sine, one_minus_cosine = math.sin(angle)/wz, (1-math.cos(angle))/wz
                    body_delta = np.array([sine*vx-one_minus_cosine*vy, one_minus_cosine*vx+sine*vy])
                rotation = np.array([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
                world_delta = rotation @ body_delta
                next_position = base[:3] + [*world_delta, 0.0]
                lower, upper = np.asarray(self.limits.world_lower), np.asarray(self.limits.world_upper)
                if (np.any(base[:3] < lower-1e-8) or np.any(base[:3] > upper+1e-8)
                        or np.any(next_position < lower-1e-8) or np.any(next_position > upper+1e-8)):
                    return ToolResult(False, "NAV_WORKSPACE_LIMIT", "velocity pulse leaves the commissioned workcell", 0.0)
                addresses, dofs = self._base_indices()
                linear, angular = np.zeros((3, self.world.model.nv)), np.zeros((3, self.world.model.nv))
                mujoco.mj_jacBody(self.world.model, self.world.data, linear, angular,
                                 self.world.model.body("chassis").id)
                jacobian = np.vstack((linear[:2, dofs], angular[2, dofs]))
                change = np.linalg.solve(jacobian, [*world_delta, angle])
                if not np.isfinite(change).all():
                    raise ValueError("nonfinite planar joint update")
                time.sleep(dt)
                failure = interrupted()
                if failure:
                    return failure
                self.world.data.qpos[addresses] += change
                self._zero_base_velocity()
                mujoco.mj_forward(self.world.model, self.world.data)
                self.world._increment_step_count()
                self.world._publish_sensor_snapshot()
                return ToolResult(True, "NAV_VELOCITY_APPLIED", "bounded base velocity pulse applied; pose requires navigation verification",
                                  1.0, {"base_pose": copy.deepcopy(self.world.robot_state()["base_pose"]), "duration_s": dt})
            except (ValueError, TypeError, np.linalg.LinAlgError) as exc:
                return ToolResult(False, "NAV_KINEMATICS_INVALID", str(exc), 0.0)
            finally:
                self._zero_base_velocity()

    def close(self):
        self._closed = True
        self.stop()
        self.renderer.close()
