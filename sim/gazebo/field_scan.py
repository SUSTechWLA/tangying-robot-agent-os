"""Measured Gazebo lidar returns for the field experiment's local avoidance.

Only polar range measurements are consumed. ``worldPose`` and scene CAD are
deliberately ignored. The configured sensor-to-base transform below is the
fixture's simulated extrinsic calibration, not an estimated physical calibration.
The caller supplies measured base poses and must keep their clock provenance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from tangying_robot_gateway.dynamic_avoidance import MotionObservation


@dataclass(frozen=True)
class SensorExtrinsics:
    translation: tuple[float, float, float] = (.12, .04, .20)
    roll_pitch_yaw: tuple[float, float, float] = (.02, -.08, .12)
    revision: str = "gazebo-field-configured-extrinsic-v1"
    provenance: str = "configured_simulation_fixture"


@dataclass(frozen=True)
class ScanObstacleResult:
    obstacles: tuple[MotionObservation, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)
    usable: bool = False


CONFIGURED_EXTRINSICS = SensorExtrinsics()


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("finite numeric measurement required")
    return float(value)


def _vector(value: Any, size: int) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)) or len(value) != size:
        raise ValueError("measurement vector has wrong dimension")
    return tuple(_number(v) for v in value)


def _rpy_matrix(rpy):
    r, p, y = rpy
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return ((cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
            (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
            (-sp, cp * sr, cp * cr))


def _quaternion_matrix(value):
    w, x, y, z = _vector(value, 4)
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if abs(norm - 1) > .01:
        raise ValueError("base quaternion must be normalized")
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    return ((1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)),
            (2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)),
            (2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)))


def _rotate(matrix, vector):
    return tuple(sum(a * b for a, b in zip(row, vector)) for row in matrix)


def scan_stamp(scan: dict[str, Any]) -> float:
    """Read the acquisition clock; protobuf omits zero-valued sec/nsec fields."""
    header = scan["header"]
    if not isinstance(header, dict) or not isinstance(header.get("stamp"), dict):
        raise TypeError("measurement header/stamp must be objects")
    stamp = header["stamp"]
    sec, nsec = stamp.get("sec", 0), stamp.get("nsec", 0)
    # Protobuf JSON renders int64 seconds as strings.
    if isinstance(sec, str) and sec.isdecimal():
        sec = int(sec)
    if type(sec) is not int or type(nsec) is not int or sec < 0 or not 0 <= nsec < 1_000_000_000:
        raise ValueError("invalid simulation stamp")
    return sec + nsec * 1e-9


def polar_points(scan: dict[str, Any]) -> tuple[list[tuple[int, tuple[float, ...]]], dict]:
    """Decode vertical-major Gazebo LaserScan ranges into sensor-local XYZ.

    Positive infinity denotes no return and is omitted. NaN, negative infinity,
    malformed arrays and inconsistent angular metadata reject the entire packet.
    """
    columns, rows = scan["count"], scan["verticalCount"]
    if type(columns) is not int or type(rows) is not int or not 2 <= columns <= 2048 or not 2 <= rows <= 128:
        raise ValueError("bounded three-dimensional scan geometry required")
    ranges = scan["ranges"]
    if not isinstance(ranges, (tuple, list)) or len(ranges) != columns * rows:
        raise ValueError("range array does not match scan dimensions")
    amin = _number(scan.get("angleMin", 0))
    astep = _number(scan.get("angleStep", 0))
    amax = _number(scan.get("angleMax", 0))
    emin = _number(scan.get("verticalAngleMin", 0))
    estep = _number(scan.get("verticalAngleStep", 0))
    emax = _number(scan.get("verticalAngleMax", 0))
    low, high = _number(scan["rangeMin"]), _number(scan["rangeMax"])
    if (astep <= 0 or estep <= 0 or low < 0 or high <= low or high > 100
            or abs(amin + (columns-1) * astep - amax) > 1e-6
            or abs(emin + (rows-1) * estep - emax) > 1e-6
            or not -math.pi / 2 <= emin < emax <= math.pi / 2):
        raise ValueError("inconsistent scan geometry")
    points, misses, out_of_range = [], 0, 0
    for index, raw in enumerate(ranges):
        if raw == "Infinity" or (type(raw) is float and raw == math.inf):
            misses += 1
            continue
        distance = _number(raw)
        if distance < low or distance > high:
            out_of_range += 1
            continue
        azimuth = amin + (index % columns) * astep
        elevation = emin + (index // columns) * estep
        points.append((index, (distance * math.cos(elevation) * math.cos(azimuth),
                               distance * math.cos(elevation) * math.sin(azimuth),
                               distance * math.sin(elevation))))
    return points, {"rayCount": columns * rows, "finiteReturns": len(points),
                    "noReturns": misses, "outOfRange": out_of_range}


def extract_obstacles(
    robot_id: str,
    scan: dict[str, Any],
    state: dict[str, Any],
    *,
    peer_states: dict[str, dict[str, Any]] | None = None,
    extrinsics: SensorExtrinsics = CONFIGURED_EXTRINSICS,
    now: float,
    dimensions: int = 2,
    max_points: int = 128,
    max_distance: float = 2.2,
    max_age: float = .35,
    max_pose_scan_delta: float = .15,
    transform_revision: str = "gazebo-ground-truth-v1",
) -> ScanObstacleResult:
    """Project fresh measured returns and retain bounded nearby obstacle points.

    Ground points are filtered to the body's obstacle-height band (base z ±.13/.20)
    so the support floor is not an obstacle. Fresh measured peer envelopes remove
    duplicate lidar constraints; unknown/stale peers do not remove any points.
    Conservative point radii include angular sampling and pose/scan time skew.
    ``usable=False`` requires the caller to request braking, not an empty free map.
    """
    diagnostics: dict[str, Any] = {
        "schemaVersion": "gazebo.field_scan_obstacles.v1", "robotId": robot_id,
        "worldPoseUsed": False, "cadUsed": False, "physicalSafetyVerified": False,
        "configuredExtrinsics": True,
    }
    try:
        if not isinstance(scan, dict) or not isinstance(state, dict):
            raise TypeError("scan and state must be objects")
        if peer_states is not None and not isinstance(peer_states, dict):
            raise ValueError("peer states must be an identity mapping")
        if type(transform_revision) is not str or not transform_revision:
            raise ValueError("explicit common transform revision required")
        now = _number(now)
        if now < 0 or dimensions not in (2, 3) or type(max_points) is not int or not 1 <= max_points <= 128:
            raise ValueError("invalid obstacle extraction budget")
        if not 0 < _number(max_distance) <= 10 or not 0 < _number(max_age) <= 1:
            raise ValueError("invalid extraction distance/age")
        if not 0 < _number(max_pose_scan_delta) <= max_age:
            raise ValueError("invalid pose/scan synchronization allowance")
        if state["robotId"] != robot_id or state["clockDomain"] != "simulation":
            raise ValueError("robot identity or clock mismatch")
        if state["frameId"] != "world" or state["worldId"] != "field-validation":
            raise ValueError("base state frame mismatch")
        if scan["frame"] != f"{robot_id}::base_link::lidar":
            raise ValueError("scan sensor frame mismatch")
        stamp, pose_time = scan_stamp(scan), _number(state["simulationTime"])
        diagnostics.update(observedAt=stamp, evaluatedAt=now, poseAt=pose_time)
        if min(now - stamp, now - pose_time) < -.02:
            raise ValueError("future scan or base state")
        if max(now - stamp, now - pose_time) > max_age:
            raise ValueError("stale scan or base state")
        dt = abs(pose_time - stamp)
        if dt > max_pose_scan_delta:
            raise ValueError("scan and base pose are not synchronized")
        base = _vector(state["position"], 3)
        body_rotation = _quaternion_matrix(state["quaternionWXYZ"])
        velocity, angular_velocity = _vector(state["linearVelocity"], 3), _vector(state["angularVelocity"], 3)
        radius = _number(state["radius"])
        if not .01 <= radius <= 2:
            raise ValueError("invalid robot envelope")
        if (not isinstance(extrinsics, SensorExtrinsics)
                or type(extrinsics.revision) is not str or not extrinsics.revision):
            raise ValueError("explicit configured extrinsics required")
        offset = _vector(extrinsics.translation, 3)
        sensor_rotation = _rpy_matrix(_vector(extrinsics.roll_pitch_yaw, 3))
        if math.dist(offset, (0, 0, 0)) > 2:
            raise ValueError("sensor offset is outside fixture envelope")
        diagnostics.update(extrinsicRevision=extrinsics.revision, extrinsicProvenance=extrinsics.provenance)
        points, decoded = polar_points(scan)
        diagnostics.update(decoded)
        peers = []
        for identity, peer in (peer_states or {}).items():
            if identity == robot_id:
                continue
            try:
                if not isinstance(peer, dict):
                    continue
                if (peer["robotId"] != identity or peer["clockDomain"] != "simulation"
                        or peer["worldId"] != state["worldId"] or peer["frameId"] != "world"):
                    continue
                at = _number(peer["simulationTime"])
                if not -.02 <= now - at <= max_age:
                    continue
                p, v = _vector(peer["position"], 3), _vector(peer["linearVelocity"], 3)
                r = _number(peer["radius"])
                if not .01 <= r <= 2:
                    continue
                # Measured constant-velocity interpolation, not desired/goal poses.
                peers.append((tuple(p[i] + v[i] * (stamp-at) for i in range(3)), r + .03))
            except (KeyError, ValueError, TypeError):
                continue
        counts = {"heightFiltered": 0, "selfFiltered": 0, "peerFiltered": 0, "distantFiltered": 0}
        speed = math.sqrt(sum(x*x for x in velocity))
        turn_speed = math.sqrt(sum(x*x for x in angular_velocity))
        candidates = []
        for index, point in points:
            local = tuple(x + y for x, y in zip(_rotate(sensor_rotation, point), offset))
            world = tuple(x + y for x, y in zip(_rotate(body_rotation, local), base))
            if dimensions == 2 and not base[2] - .13 <= world[2] <= base[2] + .20:
                counts["heightFiltered"] += 1
                continue
            distance = math.dist(world[:dimensions], base[:dimensions])
            # Only the commissioned robot body, not its larger avoidance envelope,
            # is self-filtered. An external hit inside the safety envelope remains.
            body_half_size = (.23, .23, .07) if robot_id == "uav-a" else (.25, .20, .15)
            if all(abs(local[i]) <= body_half_size[i] + .015 for i in range(3)):
                counts["selfFiltered"] += 1
                continue
            if any(math.dist(world, p) <= r for p, r in peers):
                counts["peerFiltered"] += 1
                continue
            if distance > max_distance:
                counts["distantFiltered"] += 1
                continue
            # Half of a spatial downsampling cell plus angular and skew inflation.
            uncertainty = (.06 + dt * (speed + turn_speed * distance)
                           + distance * abs(scan["angleStep"]) * .5)
            if dimensions == 3:
                uncertainty += distance * abs(scan["verticalAngleStep"]) * .5
            candidates.append((distance, index, world, uncertainty))
        candidates.sort(key=lambda item: (item[0], item[1]))
        unique, occupied = [], set()
        for item in candidates:
            cell = tuple(math.floor(x / .08) for x in item[2][:dimensions])
            if cell not in occupied:
                occupied.add(cell)
                unique.append(item)
        selected = unique[:max_points]
        obstacles = tuple(MotionObservation(
            identity=f"{robot_id}/lidar/{index}", position=tuple(world[:dimensions]),
            velocity=(0.,) * dimensions, radius=uncertainty, observed_at=stamp,
            world_id=state["worldId"], frame_id="world", transform_revision=transform_revision,
        ) for _, index, world, uncertainty in selected)
        diagnostics.update(counts, selectedCount=len(selected), candidateCount=len(candidates),
                           uniqueCount=len(unique), truncated=len(unique) > max_points,
                           poseScanDelta=dt, reason="measured_scan", coverageGuarantee=False)
        return ScanObstacleResult(obstacles, diagnostics, True)
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        diagnostics.update(reason="invalid_scan", error=str(exc))
        return ScanObstacleResult((), diagnostics, False)
