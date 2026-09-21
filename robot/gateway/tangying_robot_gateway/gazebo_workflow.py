"""Driving the production mapping workflow against a Gazebo house.

The tool layer and the survey loop are simulator-agnostic by construction - they
reach the robot through ``RobotWorkflow``'s injected callables - so making Gazebo
a backend rather than a separate universe is a matter of answering those callables
from Gazebo's own endpoints:

* **capture** comes from the Gazebo runtime, which is the same ``robot.profile.v1``
  observation a MuJoCo or physical robot serves. The conversion to the wire message
  lives in :func:`~tangying_robot_gateway.gazebo_runtime.observation_message`, so
  the message ``DenseSLAM`` receives is built by one function for both backends.
* **move** comes from the ``tangying_navigation`` goal API, which is the real
  production path a physical unit takes: a commissioned goal adjudicated by nav2,
  not a bespoke simulator nudge.

What this module deliberately does **not** do is re-implement any part of the
mapping stack. There is no second survey loop, no second occupancy grid and no
second notion of a calibrated camera here - if a number differs between MuJoCo and
Gazebo, it has to differ because the *robot* differs, never because the adapter
does.

This module is pure: HTTP in, decisions out, no ROS and no gRPC. That is what lets
the goal-lease and refusal rules be tested without a simulator, and those are the
rules that decide whether a survey keeps going or stalls.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
from tangying_robot_proto.robot.v1 import robot_pb2

from .calibration import MOTOR_IDS, calibration_revision, validate_calibration
from .gazebo_runtime import GazeboRuntimeError, observation_message
from .robot_workflow import RobotWorkflow

__all__ = [
    "GAZEBO_CAMERA_MOUNTS",
    "GAZEBO_FRAMEBUFFER",
    "GAZEBO_HORIZONTAL_FOV_RAD",
    "GAZEBO_NAV2_FOOTPRINT",
    "GazeboNavigationClient",
    "GazeboNavigationError",
    "GazeboTravelClearance",
    "GazeboWorkflowBindings",
    "bounded_step_command",
    "chassis_points",
    "gazebo_calibration_document",
    "optical_rpy",
    "swept_step_is_clear",
]

#: The footprint nav2 adjudicates every goal against, in metres, copied from
#: ``config/nav2.yaml`` (``local_costmap.footprint`` and ``global_costmap.footprint``).
#:
#: It is here because it is the *proof surface*: what can honestly be certified as
#: clear is what a collision checker actually checked, and nothing else. See
#: :class:`GazeboTravelClearance`.
GAZEBO_NAV2_FOOTPRINT: tuple[tuple[float, float], ...] = (
    (-0.24, -0.23), (0.22, -0.23), (0.22, 0.21), (-0.24, 0.21),
)

#: The radius of the smallest disc containing that footprint.
#:
#: The circumscribed radius and not the inscribed one: the checker proved the whole
#: polygon clear, so a disc that fits inside the polygon would throw away proof,
#: while a disc that escaped the polygon would claim clearance nobody checked.
GAZEBO_FOOTPRINT_RADIUS_M = max(math.hypot(x, y) for x, y in GAZEBO_NAV2_FOOTPRINT)

#: How close a query must be to the driven path to inherit its proof, matching the
#: tolerance the MuJoCo driver uses. Larger would certify floor the robot never
#: drove over.
CLEARANCE_QUERY_TOLERANCE_M = 0.02

#: The chassis envelope a bounded step is guarded against, in metres, in the robot
#: base frame: half-length, half-width, and the height band that counts.
#:
#: Taken from the robot's own ``<collision>`` in the world - 0.65 x 0.55 x 0.32 -
#: plus a margin, and deliberately **wider** than the footprint nav2 adjudicates
#: goals against (0.46 x 0.44). The two are not the same question: nav2 decides
#: whether a *planned path* is admissible under its costmap, while this guard
#: decides whether the body can physically be where it is about to be. Using
#: nav2's smaller rectangle here would let the survey drive the chassis into
#: furniture that the costmap's footprint never covered.
GAZEBO_CHASSIS_HALF_LENGTH_M = 0.325 + 0.07
GAZEBO_CHASSIS_HALF_WIDTH_M = 0.275 + 0.07
GAZEBO_CHASSIS_HEIGHT_BAND_M = (-0.35, 0.40)

#: Bounded-step limits: how fast, how finely, and how long before giving up.
#:
#: 0.05 m/s is what nav2 is configured to command, and the survey's step is 0.5 m,
#: so a step is about ten seconds of travel. ``STEP_TIMEOUT_S`` has to clear that
#: with room for the turn, or a healthy step would be reported as a refusal.
GAZEBO_STEP_LINEAR_MPS = 0.05
GAZEBO_STEP_ANGULAR_RPS = 0.20
GAZEBO_STEP_TIMEOUT_S = 45.0
#: Distance remaining below which a step is done. The survey's own arrival radius
#: is 0.7 m, so this only has to distinguish "arrived" from "stopped short".
GAZEBO_STEP_TOLERANCE_M = 0.04
GAZEBO_STEP_HEADING_TOLERANCE_RAD = 0.10


def chassis_points(points_base, *, height_band=GAZEBO_CHASSIS_HEIGHT_BAND_M):
    """The points that are physically in the robot's way, in the base frame.

    Height is what separates a wall from the rug it stands on and from the shelf
    above it: a base that refuses to move because it can see the ceiling is a base
    that never moves.
    """
    value = np.asarray(points_base, dtype=float)
    if value.ndim != 2 or value.shape[1] < 3:
        return np.zeros((0, 3))
    value = value[np.isfinite(value).all(axis=1)]
    low, high = height_band
    return value[(value[:, 2] >= low) & (value[:, 2] <= high)]


def swept_step_is_clear(points_base, *, forward_m: float, turn_rad: float,
                        half_length: float = GAZEBO_CHASSIS_HALF_LENGTH_M,
                        half_width: float = GAZEBO_CHASSIS_HALF_WIDTH_M,
                        height_band=GAZEBO_CHASSIS_HEIGHT_BAND_M) -> bool:
    """Would the chassis stay clear if it took this step?

    The swept region is the union of the chassis rectangle over the step, which is
    a rectangle for a translation and an annulus sector for a rotation; both are
    approximated here by the rectangle *plus* the arc the swept corners cover.

    The check is one-sided on purpose. It answers "is the body clear", not "is the
    path comfortable": refusing a step that is geometrically fine costs the survey
    one approach, while accepting one that is not costs the robot a collision, and
    those are not symmetric.
    """
    relevant = chassis_points(points_base, height_band=height_band)
    if not len(relevant):
        return True
    x, y = relevant[:, 0], relevant[:, 1]
    if abs(forward_m) > 1e-9:
        # The chassis occupies [-half_length, +half_length] before the step and is
        # translated by forward_m, so the union spans the interval between them.
        near = -half_length + min(0.0, forward_m)
        far = half_length + max(0.0, forward_m)
        inside = (x >= near) & (x <= far) & (np.abs(y) <= half_width)
        if bool(inside.any()):
            return False
    if abs(turn_rad) > 1e-9:
        # A rotation sweeps the chassis corners through radii up to the half
        # diagonal, so everything inside that disc has to be clear.
        radius = math.hypot(half_length, half_width)
        if bool((np.hypot(x, y) <= radius).any()):
            return False
    return True


def yaw_from_quaternion(pose) -> float:
    """The heading of a planar ``[x, y, z, qw, qx, qy, qz]`` pose."""
    w, x, y, z = (float(value) for value in pose[3:7])
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def bounded_step_command(pose, goal, *, tolerance_m=GAZEBO_STEP_TOLERANCE_M,
                         heading_tolerance_rad=GAZEBO_STEP_HEADING_TOLERANCE_RAD,
                         linear_mps=GAZEBO_STEP_LINEAR_MPS,
                         angular_rps=GAZEBO_STEP_ANGULAR_RPS):
    """One velocity command toward ``goal``, or ``None`` when it is reached.

    ``pose`` is ``[x, y, yaw]`` and ``goal`` is the 7-element pose the survey
    sends. **Both** the position and the heading are part of the goal, and getting
    that wrong is not a rounding error: the survey turns in place before every
    step, and those turns arrive as goals whose ``x`` and ``y`` are exactly the
    robot's current position. A driver that checked only position reported every
    one of those turns as already complete, so the robot never rotated, the survey
    saw twelve steps that moved nothing, and the leg ended on ``no_progress`` with
    an unmapped house. Measured: 26 s per leg, 0.00 m travelled.

    Turn first, then drive: a differential base that translates while misaligned
    traces an arc the survey did not plan and the guard did not check.
    """
    x, y, yaw = float(pose[0]), float(pose[1]), float(pose[2])
    dx, dy = float(goal[0]) - x, float(goal[1]) - y
    distance = math.hypot(dx, dy)
    if distance <= tolerance_m:
        # Standing where it was asked to stand; the remaining question is which
        # way it is facing.
        error = math.atan2(math.sin(yaw_from_quaternion(goal) - yaw),
                           math.cos(yaw_from_quaternion(goal) - yaw))
        if abs(error) <= heading_tolerance_rad:
            return None
        return 0.0, max(-angular_rps, min(angular_rps, error))
    heading = math.atan2(dy, dx)
    error = math.atan2(math.sin(heading - yaw), math.cos(heading - yaw))
    if abs(error) > heading_tolerance_rad:
        return 0.0, max(-angular_rps, min(angular_rps, error))
    # Full speed until the arrival radius, then stop.
    #
    # There is nothing to slow down for at this scale: the driver commands for one
    # control tick and re-reads the pose, and at the speed nav2 is configured for
    # (0.05 m/s) a tick advances 2.5 mm against a 40 mm arrival radius. A ramp is
    # what a fast robot needs to avoid overshoot; here it only made short steps
    # crawl. Measured with a ramp that bit below 0.5 m and floored at 0.01 m/s: a
    # 5 cm step took five seconds, a whole leg covered 0.43 m in eighty, never
    # accumulated three registrable keyframes, and the map publisher refused the
    # result - which reads as "the survey failed" and is really "the driver was
    # five times slower than it had to be".
    return linear_mps, 0.0

#: Where each camera is bolted, in the robot base frame, as metres.
#:
#: The same numbers as ``CAMERA_MOUNTS`` in the runtime node, and ultimately the
#: same numbers as the ``<pose>`` of each sensor in ``worlds/tangying_home.sdf``.
#: They are repeated rather than imported because that module imports ROS and this
#: one must not; the test that pins them together is the one that matters.
#: Camera -> (x, y, z, tilt down in degrees), relative to ``base_link``.
#:
#: The same numbers as ``GAZEBO_CAMERA_MOUNTS`` in the runtime node and the
#: ``camera_mounts`` table in ``launch/gazebo_house.launch.py``, and ultimately the
#: same numbers as the ``<pose>`` of each sensor in ``worlds/tangying_home.sdf``.
#: They are repeated rather than imported because that module imports ROS and this
#: one must not; the test that pins them together is the one that matters.
#:
#: The base camera is the *mapping* camera: low and aimed 15 degrees down, so it
#: sees the floor. That is what the reference robot does, and a calibration that
#: described a level camera would put every depth return 15 degrees above where it
#: belongs while looking perfectly well formed.
GAZEBO_CAMERA_MOUNTS: dict[str, tuple[float, float, float, float]] = {
    "base-rgbd": (0.36, 0.0, 0.16, 15.0),
    "head-rgbd": (0.05, 0.0, 1.05, 0.0),
}


def optical_rpy(tilt_degrees: float) -> tuple[float, float, float]:
    """The optical frame's roll/pitch/yaw for a camera aimed down by ``tilt``.

    REP-103 makes a level camera's optical frame ``(-90, 0, -90)`` - z forward,
    x right, y down - so aiming further down adds to the roll. Computed rather than
    typed for the same reason the calibration is derived: two hand-written copies
    of one angle eventually disagree, and the map is what pays.
    """
    return (-math.pi / 2 - math.radians(tilt_degrees), 0.0, -math.pi / 2)

#: The world's sensor: 320x240 at a 1.25 rad horizontal field of view.
GAZEBO_FRAMEBUFFER = (320, 240)
GAZEBO_HORIZONTAL_FOV_RAD = 1.25


class GazeboNavigationError(Exception):
    """A refusal from the navigation sidecar, with the code it reported."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _rpy_to_matrix(rpy: Sequence[float]) -> np.ndarray:
    roll, pitch, yaw = (float(value) for value in rpy)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])


def gazebo_calibration_document(*, robot_id: str, framebuffer=GAZEBO_FRAMEBUFFER,
                                horizontal_fov_rad: float = GAZEBO_HORIZONTAL_FOV_RAD,
                                updated_at_unix_ms: int | None = None) -> dict[str, Any]:
    """The ``robot.calibration.v1`` document that describes this Gazebo robot.

    Derived from the world and the launch file, not typed in: the lens from the
    sensor's declared field of view at its declared framebuffer, and each mount
    from the transform the ROS graph already publishes. A calibration that
    disagreed with the renderer would place every depth return at the wrong angle,
    and the resulting map would look plausible while being wrong everywhere.

    The revision is a content hash over these numbers, so two runs against the same
    world share a map identity and a changed world does not silently inherit one.
    """
    width, height = framebuffer
    # fx = fy for a sensor whose aspect handling is square; Gazebo derives both
    # from the horizontal field of view, and the runtime inverts it the same way.
    focal = (width / 2.0) / math.tan(horizontal_fov_rad / 2.0)
    cameras: dict[str, Any] = {}
    for name, (x, y, z, tilt) in GAZEBO_CAMERA_MOUNTS.items():
        cameras[name] = {
            "sourceId": f"{robot_id}/{name}",
            "width": int(width),
            "height": int(height),
            "intrinsics": {"fx": float(focal), "fy": float(focal),
                           "cx": (width - 1) * 0.5, "cy": (height - 1) * 0.5},
            "distortion": {"model": "none", "coefficients": []},
            "extrinsics": {"parentLink": "base_link",
                           "xyz": [float(x), float(y), float(z)],
                           "rpy": [float(value) for value in optical_rpy(tilt)]},
        }
    return validate_calibration({
        "schemaVersion": "robot.calibration.v1",
        "robotId": robot_id,
        "adapterId": "gazebo",
        "source": "simulation",
        "updatedAtUnixMs": int(updated_at_unix_ms
                               if updated_at_unix_ms is not None else time.time() * 1000),
        # Gazebo has no STS3215 registers either, so the simulated unit publishes
        # the numbers a freshly programmed one carries. A real unit replaces exactly
        # these fields, in the same document.
        "motors": {name: {"id": servo_id, "drive_mode": 0, "homing_offset": 0,
                          "range_min": 0, "range_max": 4095}
                   for name, servo_id in MOTOR_IDS.items()},
        "cameras": cameras,
        "geometry": {"gripper": {"openM": 0.081, "closedM": 0.0}},
        # The speeds nav2 is actually configured to command, from `nav2.yaml`. A
        # calibration that claimed a faster robot than the controller will drive
        # would make every travel-time estimate optimistic, and a survey budget is
        # a travel-time estimate.
        "safety": {"maxRelativeTargetDeg": 45.0, "maxActionChunkLength": 64,
                   "maxLinearSpeedMPerS": 0.05, "maxAngularSpeedRadPerS": 0.2},
    })


class GazeboNavigationClient:
    """The ``tangying_navigation`` goal API, as a client.

    The important rule is not the HTTP. It is the **lease**: the goal registry
    cancels any active goal whose status has not been read for two seconds
    (``http_api.py`` — ``watchdog``), because a client that stopped polling may be
    gone and a robot driving on behalf of a vanished client is the failure that
    rule exists to prevent. So polling is not an optimisation here; it is the thing
    that keeps the robot moving, and the interval is bounded below the lease rather
    than chosen for latency.
    """

    def __init__(self, base_url: str, token: str, *, timeout_s: float = 10.0,
                 poll_interval_s: float = 0.25, clock: Callable[[], float] = time.monotonic):
        if not base_url or not token:
            raise GazeboNavigationError(
                "NAVIGATION_CONFIG_REQUIRED",
                "the navigation sidecar needs both a base URL and its bearer token")
        if not 0 < poll_interval_s < 2.0:
            # Below the registry's two-second lease, with margin: a poll that lands
            # exactly on the boundary is a poll that sometimes loses the goal.
            raise GazeboNavigationError(
                "POLL_INTERVAL_UNSAFE",
                "the goal lease expires after 2 s without a status read; poll faster")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.poll_interval_s = poll_interval_s
        self._clock = clock

    # -- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(dict(body)).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Authorization", "Bearer " + self.token)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            try:
                code = str(json.loads(detail).get("code") or "NAVIGATION_HTTP_ERROR")
            except (ValueError, AttributeError):
                code = "NAVIGATION_HTTP_ERROR"
            raise GazeboNavigationError(code, f"navigation answered HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise GazeboNavigationError(
                "NAVIGATION_UNAVAILABLE",
                f"the navigation sidecar could not be reached: {error}") from error
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except ValueError as error:
            raise GazeboNavigationError(
                "NAVIGATION_INVALID_RESPONSE", "navigation did not return JSON") from error

    # -- API ----------------------------------------------------------------

    def map_status(self, *, include_grid: bool = False) -> dict[str, Any]:
        path = "/v1/navigation/map" + ("?includeGrid=1" if include_grid else "")
        return self._request("GET", path)

    def goal_status(self, goal_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/navigation/goals/{goal_id}")

    def cancel_goal(self, goal_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/navigation/goals/{goal_id}/cancel")

    def navigate(self, pose: Sequence[float], *, command_id: str | None = None,
                 frame_id: str = "odom", cancel: threading.Event | None = None,
                 deadline_s: float = 300.0) -> dict[str, Any]:
        """Drive to one commissioned pose and report how it ended.

        ``frame_id`` defaults to ``odom``, and that default is load-bearing: the
        survey plans on the pose stream it measures, which is odometry, so a goal
        expressed in ``map`` would be a goal in a frame the planner never saw. The
        sidecar converts between the two, so asking in ``odom`` is both legal and
        the only choice that keeps planner and driver talking about one frame.

        Returns the same receipt shape every other driver binding returns, so the
        survey's refusal handling cannot tell which backend it is driving.
        """
        pose = [float(value) for value in pose]
        if len(pose) != 7 or not all(math.isfinite(value) for value in pose):
            raise GazeboNavigationError("INVALID_GOAL", "a navigation goal is seven finite numbers")
        command = command_id or "mapping-" + uuid.uuid4().hex
        started = self._clock()
        try:
            accepted = self._request("POST", "/v1/navigation/goals",
                                     {"commandId": command, "goalPose": pose, "frameId": frame_id})
        except GazeboNavigationError as error:
            # BUSY and NOT_READY are the sidecar's own vocabulary and they are news
            # for the loop, not transport failures: the survey retires the target
            # and picks another one instead of ending the leg.
            return {"ok": False, "code": error.code, "message": error.message}
        goal_id = str(accepted.get("goalId") or "")
        if not goal_id:
            return {"ok": False, "code": "NAVIGATION_NO_GOAL_ID",
                    "message": "navigation accepted a goal without naming it"}
        cancelled = False
        while True:
            if cancel is not None and cancel.is_set() and not cancelled:
                cancelled = True
                try:
                    self.cancel_goal(goal_id)
                except GazeboNavigationError:
                    pass  # the poll below reports the terminal state either way
            try:
                status = self.goal_status(goal_id)
            except GazeboNavigationError as error:
                return {"ok": False, "code": error.code, "message": error.message}
            state = str(status.get("state") or "")
            if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            if self._clock() - started > deadline_s:
                try:
                    self.cancel_goal(goal_id)
                except GazeboNavigationError:
                    pass
                return {"ok": False, "code": "NAVIGATION_TIMEOUT",
                        "message": f"导航未在 {deadline_s:.0f} 秒内结束。"}
            time.sleep(self.poll_interval_s)
        if cancelled:
            return {"ok": False, "code": "CANCELLED", "message": "扫描移动已停止。"}
        if state == "SUCCEEDED":
            return {"ok": True, "code": "NAVIGATION_SUCCEEDED", "message": ""}
        message = str(status.get("message") or state or "NAVIGATION_FAILED")
        # A nav2 refusal is a *local* refusal in exactly the sense the survey's
        # refusal rule is written for: the sidecar stopped at an obstacle, or could
        # not plan through space the gateway's grid believes is free. Reporting the
        # code it gave keeps the two layers' vocabularies connected.
        return {"ok": False, "code": message or "NAVIGATION_FAILED", "message": message}


class GazeboTravelClearance:
    """The swept-path proof, built from the poses the robot actually reported.

    ``RobotWorkflow._observed_travel`` certifies the space *between* sparse sensor
    keyframes, and it returns early - certifying nothing at all - when no
    ``clearance_validator`` is supplied. That is not a small omission: a forward
    camera never sees the floor it is standing on, so without the dense trail the
    start footprint stays unknown, the published map refuses the first task
    dispatched from it, and the survey's own driven corridor comes back as
    no-go space. Measured on the MuJoCo house, dropping this hook is what turns a
    finished map into one the planner cannot leave.

    The rule is the same one the reference driver applies, and it is deliberately
    strict in the same direction: a query is certified only when the robot *drove*
    a segment within ``CLEARANCE_QUERY_TOLERANCE_M`` of it, at a footprint radius
    at least as large as the one being asked about. A wider query is refused rather
    than answered from a narrower check, because the caller asking for more is
    asking for a guarantee this evidence cannot give.
    """

    def __init__(self, radius_m: float = GAZEBO_FOOTPRINT_RADIUS_M):
        self.radius_m = float(radius_m)
        self._segments: list[tuple[float, float, float, float]] = []
        self._lock = threading.Lock()

    def reset(self) -> None:
        """Start a new session's proof. The certificate is per drive, not per process."""
        with self._lock:
            self._segments.clear()

    def record(self, start_xy, end_xy) -> None:
        start = (float(start_xy[0]), float(start_xy[1]))
        end = (float(end_xy[0]), float(end_xy[1]))
        if not all(math.isfinite(value) for value in (*start, *end)):
            return
        with self._lock:
            # A segment that did not move proves nothing new and grows the list
            # without bound while the robot sits still sampling.
            if math.dist(start, end) > 1e-6:
                self._segments.append((*start, *end))

    def __call__(self, xy, radius) -> bool:
        """``clearance_validator(xy, radius)`` — the hook the workflow queries."""
        if (not isinstance(radius, (int, float)) or isinstance(radius, bool)
                or not math.isfinite(radius) or not 0 < radius <= self.radius_m):
            return False
        try:
            point = np.asarray(xy, dtype=float)
        except (TypeError, ValueError):
            return False
        if point.shape != (2,) or not np.isfinite(point).all():
            return False
        with self._lock:
            segments = tuple(self._segments)
        for ax, ay, bx, by in reversed(segments):
            sx, sy = bx - ax, by - ay
            length_squared = sx * sx + sy * sy
            fraction = (0.0 if length_squared <= 1e-16
                        else min(1.0, max(0.0, ((point[0] - ax) * sx + (point[1] - ay) * sy)
                                          / length_squared)))
            distance = math.hypot(point[0] - (ax + fraction * sx), point[1] - (ay + fraction * sy))
            if (distance <= CLEARANCE_QUERY_TOLERANCE_M + 1e-12
                    and distance + radius <= self.radius_m + 1e-9):
                return True
        return False


class GazeboWorkflowBindings:
    """Answers ``RobotWorkflow``'s injected callables from the Gazebo stack.

    The shape mirrors ``sim/mujoco/tangying_sim/workflow_services.py`` on purpose:
    same constructor inputs, same receipt shapes, same refusal codes. Anything that
    has to be different between the two backends is a number (a camera mount, a
    waypoint), never a rule.
    """

    def __init__(self, *, runtime, navigation: GazeboNavigationClient, robot_id: str,
                 root: str, camera: str = "base-rgbd",
                 calibration: Mapping[str, Any] | None = None,
                 survey_goals: Sequence[Sequence[float]] = (),
                 clearance: GazeboTravelClearance | None = None,
                 world_revision: str = "",
                 bounded_driver: Callable[..., dict[str, Any]] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.runtime = runtime
        self.navigation = navigation
        self.robot_id = robot_id
        self.root = str(root)
        self.camera = camera
        self.clock = clock
        self.calibration = dict(calibration
                                if calibration is not None
                                else gazebo_calibration_document(robot_id=robot_id))
        self.calibration["revision"] = calibration_revision(self.calibration)
        self._goals = [list(goal) for goal in survey_goals]
        self.session = {"status": "idle",
                        "message": "选择机器人服务标定，或录入自己的标定结果。"}
        self._reservation: str | None = None
        self._reservation_lock = threading.RLock()
        self._observation_sequence = 0
        #: The driven path, recorded from the poses the robot reports. This is the
        #: evidence `_observed_travel` certifies the corridor with, so it is fed by
        #: `capture` - the one call the workflow makes continuously while moving.
        self.clearance = clearance if clearance is not None else GazeboTravelClearance()
        self._last_pose_xy: tuple[float, float] | None = None
        #: The guarded bounded-step driver, injected by the ROS node.
        self.bounded_driver = bounded_driver
        #: Hashed into every saved map's identity, so a map is bound to the world it
        #: was surveyed in. Without it a redrawn house would silently inherit maps
        #: surveyed through different walls.
        self.world_revision = str(world_revision or calibration_revision(self.calibration))
        self.workflow: RobotWorkflow | None = None

    # -- session ------------------------------------------------------------

    def reserve(self):
        from .service_registry import ServiceError

        with self._reservation_lock:
            if self._reservation is not None:
                raise ServiceError("ROBOT_BUSY", "机器人正在执行任务或扫描，请先完成或停止当前操作。")
            token = uuid.uuid4().hex
            self._reservation = token
            return token

    def release(self, token):
        with self._reservation_lock:
            if token and self._reservation == token:
                self._reservation = None

    # -- calibration --------------------------------------------------------

    def calibration_get(self):
        return {"available": True, "document": json.loads(json.dumps(self.calibration)),
                "revision": self.calibration["revision"],
                "session": dict(self.session), "methods": ["service", "manual"]}

    def calibration_run(self):
        """Gazebo's calibration is derived, so running it re-derives and re-applies.

        There is no servo programmer and no checkerboard in a simulator; the honest
        implementation is to rebuild the document from the world and report it, so a
        client that asks for a calibration gets the numbers that are actually in
        force rather than an error it would have to special-case.
        """
        self.calibration = gazebo_calibration_document(robot_id=self.robot_id)
        self.calibration["revision"] = calibration_revision(self.calibration)
        self.session = {"status": "completed",
                        "message": "机器人标定服务已完成，参数已经应用。"}
        return self.calibration_get()

    def calibration_save(self, document, expected_revision, algorithm):
        from .service_registry import ServiceError

        if set(document.get("motors", {})) != set(self.calibration["document"]["motors"]):
            raise ServiceError("MOTOR_LAYOUT_MISMATCH", "标定电机必须与当前机器人注册的电机布局一致。")
        if set(document.get("cameras", {})) != set(self.calibration["document"]["cameras"]):
            raise ServiceError("CAMERA_LAYOUT_MISMATCH", "标定相机必须与当前机器人注册的相机布局一致。")
        for name, camera in document["cameras"].items():
            parent = camera.get("extrinsics", {}).get("parentLink")
            if parent != self.calibration["document"]["cameras"][name]["extrinsics"]["parentLink"]:
                raise ServiceError("CAMERA_PARENT_MISMATCH", "相机父坐标系必须与注册的机器人结构一致。")
        if expected_revision != self.calibration["revision"]:
            raise ServiceError("REVISION_CONFLICT", "标定已在别处发生变更，请重新读取后再保存。")
        saved = dict(document)
        saved["revision"] = calibration_revision(saved)
        self.calibration = saved
        self.session = {"status": "completed", "algorithm": algorithm,
                        "message": "自行标定结果已验证、保存并应用。"}
        return self.calibration_get()

    # -- capture and motion -------------------------------------------------

    def capture(self):
        """One metric RGB-D frame plus the measured base pose.

        The pose is the odometry pose the survey plans on. It is **not** the
        RTAB-Map localized pose: a survey that planned on a map-frame pose while
        the next capture arrived in odometry would be reasoning about two frames
        with one set of numbers, which is the defect this whole layout exists to
        avoid.
        """
        self._observation_sequence += 1
        try:
            payload = self.runtime.observation(
                self.camera, observation_id=f"gz-{self._observation_sequence}",
                streams=["robot_state", "rgbd_raw"], include_raw=True)
        except GazeboRuntimeError as error:
            from .service_registry import ServiceError

            # NO_CAPTURE before the first frame is a "not ready yet", not a fault;
            # reporting it as a hardware failure would teach an operator to ignore
            # the one code that means the camera really has stopped.
            raise ServiceError(error.code, error.message) from error
        message = observation_message(payload)
        self._record_travel(message.robot_state["base_pose"])
        return message

    def _record_travel(self, base_pose) -> None:
        """Extend the swept-path proof with the pose that was just measured.

        Recorded from ``capture`` rather than from ``move`` because capture is the
        call that runs *while the robot is moving* - ``_move_and_sample`` samples
        every 200 ms on its own thread - so the segments it sees are the path
        actually driven, not the path that was requested. A proof built from
        requested waypoints would certify floor the robot skipped over.
        """
        pose = list(base_pose or [])
        if len(pose) != 7:
            return
        here = (float(pose[0]), float(pose[1]))
        if self._last_pose_xy is not None:
            self.clearance.record(self._last_pose_xy, here)
        self._last_pose_xy = here

    def move(self, goal, cancel, bounded=False):
        """One commissioned step or survey goal, expressed in the planning frame.

        ``bounded`` decides which of two genuinely different questions is being
        asked, and the split is the same one the MuJoCo binding makes:

        * a **bounded step** is the survey's own motion primitive - "move half a
          metre that way". The survey has already planned the route on the map it
          built; all it wants from the driver is that the body goes where it was
          told and stops if it cannot. Routing this through nav2 asked a *second*
          global planner to re-decide a route the explorer had already chosen, on a
          different map, with a different footprint and a 0.4 m inflation policy -
          and every sub-metre goal came back ``NAV2_ACTION_ENDED``. The survey read
          that as a refusal (correctly), spent its refusal budget, and stopped
          reporting ``no_reachable_frontier`` with the house unmapped.
        * a **commissioned goal** is point-to-point navigation, which is what nav2
          is for and where its costmap, footprint and controller do belong.
        """
        if bounded and self.bounded_driver is not None:
            return self.bounded_driver(goal, cancel)
        return self.navigation.navigate(goal, cancel=cancel, frame_id="odom")

    # -- commissioned routes ------------------------------------------------

    def survey_goals(self):
        return [list(goal) for goal in self._goals]

    def semantic_workspaces(self, anchor):
        # No commissioned semantic layer exists for the Gazebo house, and inventing
        # room names from a world file the robot cannot read would put labels in the
        # map that nothing verified.
        return []

    def observe_entities(self):
        return robot_pb2.Observation()

    # -- assembly -----------------------------------------------------------

    def build_workflow(self) -> RobotWorkflow:
        """The production workflow, wired to this backend.

        One ``RobotWorkflow`` per process, and the same class MuJoCo instantiates:
        if a survey behaves differently here, it is because the robot did.
        """
        self.workflow = RobotWorkflow(
            robot_id=self.robot_id, root=self.root,
            calibration_get=self.calibration_get, calibration_run=self.calibration_run,
            calibration_save=self.calibration_save, capture=self.capture, move=self.move,
            reserve=self.reserve, release=self.release, survey_goals=self.survey_goals,
            semantic_workspaces=self.semantic_workspaces, footprint_radius=0.32,
            entity_source=None,
            # Without this the workflow certifies nothing between keyframes and the
            # corridor it just drove comes back as no-go space on its own map.
            clearance_validator=self.clearance,
            # The world frame is pinned by what the world *is*, not by when the
            # process started: a map surveyed before a restart has to stay usable
            # after it, and a random epoch would silently orphan every map.
            world_frame_revision=self.world_revision)
        return self.workflow
