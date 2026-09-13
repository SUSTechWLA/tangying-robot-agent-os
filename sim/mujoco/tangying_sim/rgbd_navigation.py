"""Conservative short planar navigation checks using measured RGB-D.

This is a reference workcell checker, not SLAM or a map. It proves visibility of
the *new* swept body volume with dense depth. Missing pixels, occlusion and field
of view gaps are unknown. The robot's commissioned dimensions are the only
geometry supplied externally. The MuJoCo reference driver also checks each
bounded candidate pulse against a private copy of its physical model before it
updates live state. That collision check stays inside the driver; it is never
published as SLAM, point-cloud, or semantic object evidence. A successful RGB-D
check expires with its capture and is not a motion receipt.
"""

from __future__ import annotations

import copy
import itertools
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np
from tangying_robot_gateway.rgbd import RgbdFrame, validate_frame

from .home_scene import (
    HOME_MODEL_PATH,
    HOME_TASK_BIN_HALF_EXTENT,
    HOME_TASK_BIN_POSITION,
    HOME_TASK_BIN_SHELF_CENTER,
    HOME_TASK_BIN_SHELF_HALF_EXTENT,
    HOME_TASK_BIN_SURFACE_HALF_EXTENT,
    HOME_TASK_BIN_WALL_HEIGHT,
    HOME_TASK_BIN_WALL_THICKNESS,
    HOME_TASK_CUP_POSITION,
    HOME_TASK_OBJECT_PLACEMENTS,
    HOME_TASK_TABLE_CENTER,
    HOME_TASK_TABLE_HALF_EXTENT,
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
    max_rotation_rad: float = 0.5
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


@dataclass(frozen=True)
class _TravelClearance:
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]
    observed_at_unix_ms: int
    radius_m: float
    episode: int


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
    for value in (limits.body_height_m, limits.max_translation_m, limits.max_rotation_rad,
                  limits.position_tolerance_m, limits.yaw_tolerance_rad, limits.depth_margin_m):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("navigation limits must be positive finite numbers")
    if (limits.max_translation_m > 0.25 or limits.max_rotation_rad > 0.5
            or limits.position_tolerance_m > 0.02
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
    # The head camera keeps its 70 degree view for now. Matching it to the D435i's
    # 58 degrees is a real change, but the household perception pipeline is tuned
    # against the wider frame - narrowing it moved objects out of view and the cups
    # stopped being detected - and the shared robot model is also used by the
    # tabletop acceptance. Retuning perception for 58 degrees is its own piece of
    # work with its own evidence; folding it into the base camera fix would have
    # made both harder to review and neither verifiable.
    # Chassis local +X is its front (world +Y in the commissioned home pose).
    #
    # The bottom camera sits low on the chassis and looks forward, which is what
    # makes it a forward depth sensor: it sees the floor immediately ahead and the
    # obstacles the base is about to meet. It used to be mounted at z=1.50 with a
    # 150 degree fisheye tilted 45 degrees down - higher than the head camera and
    # aimed at the floor rather than ahead, so the "bottom" view was neither at the
    # bottom nor looking where the robot was going. A 15 degree downward tilt keeps
    # the near floor in frame without giving up the forward view.
    #
    # Keep the legacy tabletop calibration byte for byte stable because its
    # acceptance fixtures assert the near-floor rays.
    forward_tilt = 15.0
    tilt = math.radians(forward_tilt)
    camera_pos = [0.30, 0, 0.16] if scene in {"home", "home_task"} else [0.185, 0, 0.50]
    camera_axes = ([0, -1, 0, math.sin(tilt), 0, math.cos(tilt)]
                   if scene in {"home", "home_task"} else [0, -1, 0, 2**-0.5, 0, 2**-0.5])
    # Same part as the head camera: a RealSense D435i depth stream is 87 x 58
    # degrees, so both sensors share one vertical field of view. Two different
    # fields of view would mean two different cameras, and every extrinsic or
    # coverage figure that depends on them would describe neither.
    #
    # The tabletop scene keeps its own 100 degrees: its acceptance fixtures assert
    # specific near-floor rays, so changing it is a separate decision with its own
    # evidence, not a side effect of this one.
    camera_fovy = 58 if scene in {"home", "home_task"} else 100
    body.add_camera(name="base_depth", pos=camera_pos, xyaxes=camera_axes,
                    fovy=camera_fovy, mode=mujoco.mjtCamLight.mjCAMLIGHT_FIXED)
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
        size=list(HOME_TASK_TABLE_HALF_EXTENT), rgba=[0.42, 0.24, 0.12, 1.0],
        contype=1, conaffinity=1,
    )
    table.add_geom(
        name="home_task_bin_shelf", type=mujoco.mjtGeom.mjGEOM_BOX,
        pos=(np.asarray(HOME_TASK_BIN_SHELF_CENTER) - np.asarray(HOME_TASK_TABLE_CENTER)).tolist(),
        size=list(HOME_TASK_BIN_SHELF_HALF_EXTENT),
        rgba=[0.42, 0.24, 0.12, 1.0], contype=1, conaffinity=1,
    )
    kitchen_bin = spec.worldbody.add_body(name="kitchen_bin", pos=list(HOME_TASK_BIN_POSITION))
    kitchen_bin.add_geom(
        name="kitchen_bin_surface", type=mujoco.mjtGeom.mjGEOM_BOX,
        size=list(HOME_TASK_BIN_SURFACE_HALF_EXTENT), rgba=[0.08, 0.28, 0.78, 1.0],
        contype=1, conaffinity=1, friction=[1.0, 0.01, 0.001],
    )
    # A recessed collision floor keeps a released cup from sliding off the
    # visible rim during the settle/verification window. It has no material
    # colour, so RGB-D still observes only the blue bin surface.
    kitchen_bin.add_geom(
        name="kitchen_bin_catch", pos=[0, 0, -0.035],
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[*HOME_TASK_BIN_SURFACE_HALF_EXTENT[:2], 0.02],
        rgba=[0, 0, 0, 0], contype=1, conaffinity=1,
        friction=[1.0, 0.01, 0.001],
    )
    wall_x = HOME_TASK_BIN_HALF_EXTENT[0] - HOME_TASK_BIN_WALL_THICKNESS
    wall_y = HOME_TASK_BIN_HALF_EXTENT[1] - HOME_TASK_BIN_WALL_THICKNESS
    for name, pos, size in (
        ("kitchen_bin_wall_left", [-wall_x, 0, HOME_TASK_BIN_WALL_HEIGHT],
         [HOME_TASK_BIN_WALL_THICKNESS, HOME_TASK_BIN_HALF_EXTENT[1], HOME_TASK_BIN_WALL_HEIGHT]),
        ("kitchen_bin_wall_right", [wall_x, 0, HOME_TASK_BIN_WALL_HEIGHT],
         [HOME_TASK_BIN_WALL_THICKNESS, HOME_TASK_BIN_HALF_EXTENT[1], HOME_TASK_BIN_WALL_HEIGHT]),
        ("kitchen_bin_wall_front", [0, -wall_y, HOME_TASK_BIN_WALL_HEIGHT],
         [HOME_TASK_BIN_HALF_EXTENT[0], HOME_TASK_BIN_WALL_THICKNESS, HOME_TASK_BIN_WALL_HEIGHT]),
        ("kitchen_bin_wall_back", [0, wall_y, HOME_TASK_BIN_WALL_HEIGHT],
         [HOME_TASK_BIN_HALF_EXTENT[0], HOME_TASK_BIN_WALL_THICKNESS, HOME_TASK_BIN_WALL_HEIGHT]),
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
        ("yellow_plate", [0.095, 0.012], [0.95, 0.80, 0.10, 1.0]),
    ):
        body = spec.worldbody.add_body(name=slug, pos=list(HOME_TASK_OBJECT_PLACEMENTS[f"{slug}_free"]))
        body.add_freejoint(name=f"{slug}_free")
        body.add_geom(
            name=f"{slug}_visual", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
            size=size, rgba=rgba, mass=0.12, contype=1, conaffinity=1,
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


def _clearance_envelope_collision(model, data, robot_body_ids, chassis_body_id,
                                  radius_m, bottom_m, height_m):
    """Conservative vertical circle against collision-enabled environment AABBs."""
    chassis_position = data.xpos[chassis_body_id]
    base_low = float(chassis_position[2]+bottom_m)
    base_high = float(chassis_position[2]+height_m)
    center_xy = chassis_position[:2]
    for geom in range(model.ngeom):
        if int(model.geom_bodyid[geom]) in robot_body_ids:
            continue
        if int(model.geom_contype[geom]) == 0 and int(model.geom_conaffinity[geom]) == 0:
            continue
        rotation = data.geom_xmat[geom].reshape(3, 3)
        if model.geom_type[geom] == mujoco.mjtGeom.mjGEOM_PLANE:
            # Only an upward horizontal support plane is exempt. A vertical or
            # tilted plane is an obstacle even though household walls use boxes.
            if rotation[2, 2] > 0.95:
                continue
            return True
        geom_center = data.geom_xpos[geom]+rotation@model.geom_aabb[geom, :3]
        geom_half = np.abs(rotation)@model.geom_aabb[geom, 3:]
        low, high = geom_center-geom_half, geom_center+geom_half
        if high[2] < base_low or low[2] > base_high:
            continue
        closest = np.clip(center_xy, low[:2], high[:2])
        if float(np.linalg.norm(center_xy-closest)) <= radius_m:
            return True
    return False


def _swept_model_collision(model, data, robot_body_ids, qpos_addresses,
                           qpos_change, sample_count, *, chassis_body_id=None,
                           clearance_radius_m=None, clearance_bottom_m=0.0,
                           clearance_height_m=0.4, trial_data=None):
    """Check a candidate base sweep on copied MuJoCo state.

    This is a last driver-boundary guard, not environment evidence. It ignores
    robot self-contact and a horizontal support plane, and rejects every other
    robot/environment contact at any interpolation sample. The live ``MjData``
    is never changed.
    """
    addresses = np.asarray(qpos_addresses, dtype=int)
    change = np.asarray(qpos_change, dtype=float)
    bodies = {int(body) for body in robot_body_ids}
    if (addresses.ndim != 1 or change.shape != addresses.shape or not np.isfinite(change).all()
            or type(sample_count) is not int or not 1 <= sample_count <= 2048):
        raise ValueError("invalid bounded model collision sweep")
    if trial_data is None:
        trial = copy.copy(data)
    else:
        if trial_data is data:
            raise ValueError("collision sweep data must not alias live state")
        mujoco.mj_copyData(trial_data, model, data)
        trial = trial_data
    start = trial.qpos[addresses].copy()
    for fraction in np.linspace(0.0, 1.0, sample_count+1):
        trial.qpos[addresses] = start + change*fraction
        mujoco.mj_forward(model, trial)
        for index in range(trial.ncon):
            contact = trial.contact[index]
            if contact.dist > 1e-8:
                continue
            geom1, geom2 = int(contact.geom1), int(contact.geom2)
            body1, body2 = int(model.geom_bodyid[geom1]), int(model.geom_bodyid[geom2])
            first_is_robot, second_is_robot = body1 in bodies, body2 in bodies
            if first_is_robot == second_is_robot:
                continue
            external_geom = geom2 if first_is_robot else geom1
            if model.geom_type[external_geom] == mujoco.mjtGeom.mjGEOM_PLANE:
                normal = trial.geom_xmat[external_geom].reshape(3, 3)[:, 2]
                if normal[2] > 0.95:
                    continue
            return True
        if (clearance_radius_m is not None
                and _clearance_envelope_collision(
                    model, trial, bodies, chassis_body_id,
                    clearance_radius_m, clearance_bottom_m, clearance_height_m,
                )):
            return True
    return False


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
    COLLISION_LINEAR_RESOLUTION_M = 0.001
    COLLISION_ANGULAR_RESOLUTION_RAD = 0.0025
    CLEARANCE_RADIUS_M = 0.40
    CLEARANCE_HEIGHT_M = 0.40
    CLEARANCE_QUERY_TOLERANCE_M = 0.02
    CLEARANCE_HISTORY_LIMIT = 10_000

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
        self._clearance_lock = threading.Lock()
        self._travel_clearance = deque(maxlen=self.CLEARANCE_HISTORY_LIMIT)
        self._stop_generation = 0
        self._closed = False
        self._sequence = int(time.time() * 1000) * 1000
        self.last_frame = None
        self.last_base_pose = None
        self.last_check = None
        self.allow_multi_segment = bool(allow_multi_segment)
        self._chassis_body_id = self.world.model.body("chassis").id
        self._collision_data = mujoco.MjData(self.world.model)
        self._robot_body_ids = {self._chassis_body_id}
        for body in range(self.world.model.nbody):
            if int(self.world.model.body_parentid[body]) in self._robot_body_ids:
                self._robot_body_ids.add(body)
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
            current_pose = _pose(self.world.robot_state()["base_pose"])
            goal_pose = _pose(goal_pose)
            _validate_limits(self.limits)
        except (TypeError, ValueError):
            return ToolResult(False, "NAV_POSE_INVALID", "goal pose is not finite", 0.0)
        lower, upper = np.asarray(self.limits.world_lower), np.asarray(self.limits.world_upper)
        if (np.any(current_pose[:3] < lower-1e-8) or np.any(current_pose[:3] > upper+1e-8)
                or np.any(goal_pose[:3] < lower-1e-8) or np.any(goal_pose[:3] > upper+1e-8)):
            return ToolResult(False, "NAV_WORKSPACE_LIMIT", "base or goal lies outside commissioned world limits", 0.0)
        if (abs(current_pose[2]-goal_pose[2]) > 1e-8 or np.any(np.abs(current_pose[4:6]) > 1e-6)
                or np.any(np.abs(goal_pose[4:6]) > 1e-6)):
            return ToolResult(False, "NAV_PLANAR_ONLY", "reference navigation cannot change elevation, roll or pitch", 0.0)
        current_yaw = 2*math.atan2(current_pose[6], current_pose[3])
        goal_yaw = 2*math.atan2(goal_pose[6], goal_pose[3])
        yaw_delta = math.atan2(math.sin(goal_yaw-current_yaw), math.cos(goal_yaw-current_yaw))
        if abs(yaw_delta) > self.limits.max_rotation_rad+1e-12:
            return ToolResult(False, "NAV_ROTATION_LIMIT", "goal exceeds the bounded rotation limit", 0.0)

        with self._control_lock:
            with self._stop_lock:
                generation = self._stop_generation
            current, goal = current_pose[:2].copy(), goal_pose[:2].copy()
            translation_orientation = current_pose[3:].tolist()
            targets = []
            followed_connector = False
            if self.allow_multi_segment and getattr(self.world,"scene",None)=="home_task":
                # The task station docks below the doorway centre. Cross the
                # opening at its commissioned centre before approaching the
                # station; the reverse connector preserves the same clearance.
                connectors=[]
                if current[0]>1.5 and goal[0]<1.4:
                    connectors=[(1.5,float(current[1])),(1.5,3.35),(0.,3.35)]
                elif current[0]<1.4 and goal[0]>1.5:
                    connectors=[(0.,3.35),(1.5,3.35)]
                for x,y in connectors:
                    intermediate=list(goal_pose)
                    intermediate[:2]=[x,y];intermediate[3:]=translation_orientation
                    targets.append(intermediate)
                    current=np.array([x,y])
                followed_connector=bool(connectors)
            if self.allow_multi_segment:
                # Use an axis-aligned household route: the forward RGB-D camera can
                # prove a corridor translation first, then a lateral kitchen/bedroom
                # approach. A diagonal swept box would include the robot's own rear
                # arm envelope, which is outside the forward camera frustum.
                # Exit a side room through its doorway before travelling along the
                # corridor. Translating down the room and crossing later cuts a wall.
                exit_side_first = (not followed_connector and abs(current[0]) > .6
                                   and abs(goal[0]-current[0]) > self.limits.position_tolerance_m)
                if exit_side_first and abs(goal[1]-current[1]) > self.limits.position_tolerance_m:
                    intermediate = list(goal_pose)
                    intermediate[0], intermediate[1] = 0., float(current[1])
                    intermediate[3:] = translation_orientation
                    targets.append(intermediate)
                    current = np.array([0., current[1]])
                if abs(goal[1]-current[1]) > self.limits.position_tolerance_m:
                    intermediate = list(goal_pose)
                    intermediate[0] = float(current[0])
                    intermediate[3:] = translation_orientation
                    targets.append(intermediate)
                if abs(goal[0]-current[0]) > self.limits.position_tolerance_m or not targets:
                    target = list(goal_pose)
                    target[3:] = translation_orientation
                    targets.append(target)
            else:
                target = list(goal_pose)
                target[3:] = translation_orientation
                targets.append(target)

            result = None
            for target in targets:
                start = np.asarray(self.world.robot_state()["base_pose"][:2], dtype=float)
                end = np.asarray(target[:2], dtype=float)
                distance = float(np.linalg.norm(end-start))
                if distance <= self.limits.position_tolerance_m:
                    continue
                count = (math.ceil(distance/(self.limits.max_translation_m*0.8))
                         if self.allow_multi_segment else 1)
                for index in range(1, count+1):
                    failure = self._interrupted(generation, cancel_event, "navigation stopped at its current pose")
                    if failure:
                        return failure
                    fraction = min(1.0, index/count)
                    segment = list(target)
                    segment[0] = float(start[0]+(end[0]-start[0])*fraction)
                    segment[1] = float(start[1]+(end[1]-start[1])*fraction)
                    result = self._navigate_single(segment, cancel_event, _generation=generation)
                    if not result.success:
                        return result
            if abs(yaw_delta) > self.limits.yaw_tolerance_rad:
                return self._rotate_to(goal_pose.tolist(), cancel_event, generation)
            if result is not None:
                return result
            return self._navigate_single([*goal_pose[:3], *translation_orientation], cancel_event,
                                         _generation=generation)

    def _interrupted(self, generation, cancel_event, message):
        if self._closed:
            return ToolResult(False, "NAV_CONTROLLER_CLOSED", "navigation controller is closed", 0.0)
        if (cancel_event is not None and cancel_event.is_set()) or generation != self._stop_generation:
            return ToolResult(False, "CANCELLED", message, 0.0)
        return None

    def _model_motion_collides(self, addresses, change, translation_m, rotation_rad):
        samples = max(
            1,
            math.ceil(abs(float(translation_m))/self.COLLISION_LINEAR_RESOLUTION_M),
            math.ceil(abs(float(rotation_rad))/self.COLLISION_ANGULAR_RESOLUTION_RAD),
        )
        return _swept_model_collision(
            self.world.model, self.world.data, self._robot_body_ids,
            addresses, change, samples,
            chassis_body_id=self._chassis_body_id,
            clearance_radius_m=self.CLEARANCE_RADIUS_M,
            clearance_bottom_m=self.limits.body_bottom_offset_m,
            clearance_height_m=self.CLEARANCE_HEIGHT_M,
            trial_data=self._collision_data,
        )

    def _record_travel_clearance(self, start_pose, end_pose, observed_at_unix_ms,
                                 radius_m=None):
        start, end = _pose(start_pose), _pose(end_pose)
        radius_m = self.CLEARANCE_RADIUS_M if radius_m is None else float(radius_m)
        record = _TravelClearance(
            tuple(float(value) for value in start[:2]),
            tuple(float(value) for value in end[:2]),
            int(observed_at_unix_ms),
            radius_m,
            int(getattr(self.world, "episode", 0)),
        )
        with self._clearance_lock:
            self._travel_clearance.append(record)

    def verified_travel_clearance(self, xy, radius):
        """Check that a query footprint fits inside this session's swept path."""
        if (not isinstance(xy, (list, tuple, np.ndarray))
                or type(radius) not in (int, float) or not math.isfinite(radius)
                or not 0 < radius <= self.CLEARANCE_RADIUS_M):
            return False
        try:
            point = np.asarray(xy, dtype=float)
        except (TypeError, ValueError):
            return False
        if point.shape != (2,) or not np.isfinite(point).all():
            return False
        with self._clearance_lock:
            records = tuple(self._travel_clearance)
        episode = int(getattr(self.world, "episode", 0))
        for record in reversed(records):
            if record.episode != episode or radius > record.radius_m+1e-12:
                continue
            start, end = np.asarray(record.start_xy), np.asarray(record.end_xy)
            segment = end-start
            length_squared = float(segment@segment)
            fraction = (0.0 if length_squared <= 1e-16 else
                        min(1.0, max(0.0, float((point-start)@segment/length_squared))))
            nearest = start+fraction*segment
            distance = float(np.linalg.norm(point-nearest))
            if (distance <= self.CLEARANCE_QUERY_TOLERANCE_M+1e-12
                    and distance+radius <= record.radius_m+1e-9):
                return True
        return False

    def clear_at_pose(self, pose, radius=0.40):
        """Return driver-model clearance only for the freshly observed live pose.

        The workflow may use ``True`` to mark the robot's current, traversed
        footprint as free. Arbitrary map cells cannot be queried through this
        hook, and no simulator geometry or identity leaves the driver.
        """
        if (type(radius) not in (int, float) or not math.isfinite(radius)
                or not 0 < radius <= self.CLEARANCE_RADIUS_M):
            return False
        with self._capture_lock, self.world.lock:
            try:
                frame, observed = self.capture()
                validate_frame(frame)
                requested, observed = _pose(pose), _pose(observed)
            except (TypeError, ValueError, RuntimeError):
                return False
            observed_yaw = 2*math.atan2(observed[6], observed[3])
            requested_yaw = 2*math.atan2(requested[6], requested[3])
            yaw_error = math.atan2(math.sin(requested_yaw-observed_yaw),
                                   math.cos(requested_yaw-observed_yaw))
            if (float(np.linalg.norm(requested[:3]-observed[:3])) > self.limits.position_tolerance_m
                    or abs(yaw_error) > self.limits.yaw_tolerance_rad):
                return False
            addresses, _ = self._base_indices()
            clear = not _swept_model_collision(
                self.world.model, self.world.data, self._robot_body_ids,
                addresses, np.zeros(3), 1,
                chassis_body_id=self._chassis_body_id,
                clearance_radius_m=float(radius),
                clearance_bottom_m=self.limits.body_bottom_offset_m,
                clearance_height_m=self.CLEARANCE_HEIGHT_M,
                trial_data=self._collision_data,
            )
            if clear:
                self._record_travel_clearance(observed.tolist(), observed.tolist(),
                                              frame.captured_at_unix_ms, float(radius))
            return clear

    def _navigate_single(self, goal_pose, cancel_event=None, *, _generation=None):
        with self._control_lock, self.world.lock:
            if _generation is None:
                with self._stop_lock:
                    _generation = self._stop_generation
            self._zero_base_velocity()
            moved = False
            for _ in range(math.ceil(self.limits.max_translation_m/self.MAX_STEP_M)+2):
                failure = self._interrupted(_generation, cancel_event, "navigation stopped at its current pose")
                if failure:
                    return failure
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
                if self._model_motion_collides(addresses, change, float(np.linalg.norm(delta)), 0.0):
                    return ToolResult(False, "NAV_MODEL_COLLISION",
                                      "reference driver model predicts contact during bounded base pulse",
                                      0.0, evidence)
                # Recheck after the bounded wait, before any position update.
                time.sleep((float(np.linalg.norm(delta))/self.MAX_LINEAR_SPEED_M_S)*self.sleep_scale)
                failure = self._interrupted(_generation, cancel_event, "navigation stopped at its current pose")
                if failure:
                    return failure
                try:
                    validate_frame(frame)
                except ValueError as exc:
                    return ToolResult(False, "NAV_OBSERVATION_INVALID", str(exc), 0.0)
                with self._stop_lock:
                    if _generation != self._stop_generation:
                        return ToolResult(False, "CANCELLED", "navigation stopped at its current pose", 0.0)
                    self.world.data.qpos[addresses] += change
                self.world.data.qvel[dofs] = 0
                mujoco.mj_forward(self.world.model, self.world.data)
                self.world._increment_step_count()
                self.world._publish_sensor_snapshot()
                self._record_travel_clearance(
                    base, self.world.robot_state()["base_pose"], frame.captured_at_unix_ms,
                )
                moved = True
            return ToolResult(False, "NAV_STEP_LIMIT", "base did not reach its goal within bounded control steps", 0.0)

    def _rotate_to(self, goal_pose, cancel_event, generation):
        with self.world.lock:
            self._zero_base_velocity()
            moved = False
            max_steps = math.ceil(self.limits.max_rotation_rad/(self.MAX_ANGULAR_SPEED_RAD_S*self.MAX_PULSE_S))+2
            for _ in range(max_steps):
                failure = self._interrupted(generation, cancel_event, "navigation turn stopped at its current pose")
                if failure:
                    return failure
                try:
                    frame, base = self.capture()
                    validate_frame(frame)
                    base, goal = _pose(base), _pose(goal_pose)
                except (TypeError, ValueError, RuntimeError) as exc:
                    return ToolResult(False, "NAV_OBSERVATION_INVALID", str(exc), 0.0)
                evidence = {"capture": frame, "base_pose": copy.deepcopy(base.tolist())}
                if float(np.linalg.norm(goal[:2]-base[:2])) > self.limits.position_tolerance_m:
                    return ToolResult(False, "NAV_ROTATION_POSITION_MISMATCH",
                                      "rotation requires fresh localization at the requested position", 0.0, evidence)
                yaw = 2*math.atan2(base[6], base[3])
                goal_yaw = 2*math.atan2(goal[6], goal[3])
                delta = math.atan2(math.sin(goal_yaw-yaw), math.cos(goal_yaw-yaw))
                if abs(delta) <= self.limits.yaw_tolerance_rad:
                    code = "NAV_REACHED" if moved else "NAV_ALREADY_AT_GOAL"
                    message = ("fresh base localization confirms the requested heading" if moved
                               else "current base pose is already at the goal; no movement required")
                    return ToolResult(True, code, message, 1.0, evidence)
                angle = math.copysign(min(abs(delta), self.MAX_ANGULAR_SPEED_RAD_S*self.MAX_PULSE_S), delta)
                addresses, dofs = self._base_indices()
                linear = np.zeros((3, self.world.model.nv))
                angular = np.zeros_like(linear)
                mujoco.mj_jacBody(self.world.model, self.world.data, linear, angular,
                                  self.world.model.body("chassis").id)
                jacobian = np.vstack((linear[:2, dofs], angular[2, dofs]))
                try:
                    change = np.linalg.solve(jacobian, [0.0, 0.0, angle])
                except np.linalg.LinAlgError:
                    return ToolResult(False, "NAV_KINEMATICS_INVALID", "planar base transform is singular", 0.0)
                if not np.isfinite(change).all():
                    return ToolResult(False, "NAV_KINEMATICS_INVALID", "nonfinite planar base update", 0.0)
                if self._model_motion_collides(addresses, change, 0.0, angle):
                    return ToolResult(False, "NAV_MODEL_COLLISION",
                                      "reference driver model predicts contact during bounded base turn",
                                      0.0, evidence)
                time.sleep(abs(angle)/self.MAX_ANGULAR_SPEED_RAD_S*self.sleep_scale)
                failure = self._interrupted(generation, cancel_event, "navigation turn stopped at its current pose")
                if failure:
                    return failure
                try:
                    validate_frame(frame)
                except ValueError as exc:
                    return ToolResult(False, "NAV_OBSERVATION_INVALID", str(exc), 0.0)
                with self._stop_lock:
                    if generation != self._stop_generation:
                        return ToolResult(False, "CANCELLED", "navigation turn stopped at its current pose", 0.0)
                    self.world.data.qpos[addresses] += change
                self._zero_base_velocity()
                mujoco.mj_forward(self.world.model, self.world.data)
                self.world._increment_step_count()
                self.world._publish_sensor_snapshot()
                self._record_travel_clearance(
                    base.tolist(), self.world.robot_state()["base_pose"], frame.captured_at_unix_ms,
                )
                moved = True
            return ToolResult(False, "NAV_STEP_LIMIT", "base did not reach its heading within bounded control steps", 0.0)

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
        """Apply one observed, collision-checked finite ROS FLU pulse.

        The caller gates translation with the current navigation map and live
        obstacle inputs. This reference driver additionally checks copied
        MuJoCo state before every pulse. A pulse containing rotation also needs
        fresh base RGB-D; direct ``navigate`` captures RGB-D on every bounded
        translation and rotation step. +X is chassis forward, +Y left, +Z up.
        Velocity is never latched: a completed call leaves base qvel/ctrl zero.
        The watchdog covers transport age, lock waits, safety checks, and the
        pulse wait through the final check immediately before state mutation.
        """
        received_at = time.monotonic()
        admitted_age = None
        safety_started_at = None
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
            failure = self._interrupted(generation, cancel_event,
                                        "navigation velocity stopped at its current pose")
            if failure:
                return failure
            age = (command_age_s+time.monotonic()-received_at if safety_started_at is None
                   else admitted_age+time.monotonic()-safety_started_at)
            if age > self.COMMAND_WATCHDOG_S:
                return ToolResult(False, "NAV_VELOCITY_STALE", "velocity command expired before position update", 0.0)
            return None

        with self._control_lock, self.world.lock:
            try:
                failure = interrupted()
                if failure:
                    return failure
                admitted_age = command_age_s+time.monotonic()-received_at
                _validate_limits(self.limits)
                frame = None
                if abs(wz) > 1e-12:
                    try:
                        frame, base = self.capture()
                        validate_frame(frame)
                        base = _pose(base)
                    except (TypeError, ValueError, RuntimeError) as exc:
                        return ToolResult(False, "NAV_OBSERVATION_INVALID", str(exc), 0.0)
                    # A successful fresh capture replaces old scene evidence;
                    # retain admitted transport/lock age, then start the
                    # mutation lease for kinematics, model sweep and pulse.
                    safety_started_at = time.monotonic()
                else:
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
                if frame is not None and float(np.linalg.norm(world_delta)) > 1e-12:
                    translation_goal = [*next_position, *base[3:]]
                    observation = check_navigation(
                        frame, base.tolist(), translation_goal,
                        base_observed_at_unix_ms=frame.captured_at_unix_ms,
                        limits=self.limits,
                    )
                    self.last_check = observation
                    if not observation.allowed:
                        return ToolResult(False, observation.code, observation.message, 0.0,
                                          {"capture": frame, "base_pose": copy.deepcopy(base.tolist())})
                addresses, dofs = self._base_indices()
                linear, angular = np.zeros((3, self.world.model.nv)), np.zeros((3, self.world.model.nv))
                mujoco.mj_jacBody(self.world.model, self.world.data, linear, angular,
                                 self.world.model.body("chassis").id)
                jacobian = np.vstack((linear[:2, dofs], angular[2, dofs]))
                change = np.linalg.solve(jacobian, [*world_delta, angle])
                if not np.isfinite(change).all():
                    raise ValueError("nonfinite planar joint update")
                if self._model_motion_collides(addresses, change, float(np.linalg.norm(world_delta)), angle):
                    evidence = {"base_pose": copy.deepcopy(base.tolist())}
                    if frame is not None:
                        evidence["capture"] = frame
                    return ToolResult(False, "NAV_MODEL_COLLISION",
                                      "reference driver model predicts contact during bounded base pulse", 0.0,
                                      evidence)
                failure = self._interrupted(generation, cancel_event,
                                            "navigation velocity stopped at its current pose")
                if failure:
                    return failure
                time.sleep(dt)
                failure = interrupted()
                if failure:
                    return failure
                if frame is not None:
                    try:
                        validate_frame(frame)
                    except ValueError as exc:
                        return ToolResult(False, "NAV_OBSERVATION_INVALID", str(exc), 0.0)
                with self._stop_lock:
                    if generation != self._stop_generation:
                        return ToolResult(False, "CANCELLED", "navigation velocity stopped at its current pose", 0.0)
                    self.world.data.qpos[addresses] += change
                self._zero_base_velocity()
                mujoco.mj_forward(self.world.model, self.world.data)
                self.world._increment_step_count()
                self.world._publish_sensor_snapshot()
                observed_at = (frame.captured_at_unix_ms if frame is not None else int(time.time()*1000))
                self._record_travel_clearance(base.tolist(), self.world.robot_state()["base_pose"], observed_at)
                payload = {"base_pose": copy.deepcopy(self.world.robot_state()["base_pose"]),
                           "duration_s": dt}
                if frame is not None:
                    payload["capture"] = frame
                return ToolResult(True, "NAV_VELOCITY_APPLIED", "bounded base velocity pulse applied; pose requires navigation verification",
                                  1.0, payload)
            except (ValueError, TypeError, np.linalg.LinAlgError) as exc:
                return ToolResult(False, "NAV_KINEMATICS_INVALID", str(exc), 0.0)
            finally:
                self._zero_base_velocity()

    def close(self):
        self._closed = True
        self.stop()
        self.renderer.close()
