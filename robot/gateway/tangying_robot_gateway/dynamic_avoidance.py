"""Deterministic local velocity selection from timestamped measured geometry.

This is a replaceable short-horizon policy, not a controller or safety certificate.
The caller must continue measuring contacts, clearance and actual stopping motion.
All coordinates and timestamps must already share one registered frame and clock.
"""
import math
from dataclasses import dataclass, replace
from itertools import pairwise


@dataclass(frozen=True)
class TrajectoryPoint:
    """An advisory position at an absolute time on the measurement clock."""

    at: float
    position: tuple[float, ...]


@dataclass(frozen=True)
class MotionObservation:
    """Measured enclosing disc (2D) or sphere (3D), not a desired peer pose.

    ``obstacles`` may use this type for local range hits with radius zero and
    velocity zero. They must cover the swept body, not just a forward ray. The
    policy cannot infer unseen obstacles or verify sensor coverage.
    """

    identity: str
    position: tuple[float, ...]
    velocity: tuple[float, ...]
    radius: float
    observed_at: float
    world_id: str
    frame_id: str
    transform_revision: str
    trajectory: tuple[TrajectoryPoint, ...] = ()


@dataclass(frozen=True)
class AvoidanceConfig:
    dimensions: int = 2
    max_speed: float = 0.5
    horizon: float = 2.0
    safety_margin: float = 0.06
    reaction_time: float = 0.12
    braking_deceleration: float = 0.8
    max_observation_age: float = 0.3
    future_tolerance: float = 0.02
    # Inflation for velocity/registration error accumulated since capture.
    observation_uncertainty_speed: float = 0.1
    angular_samples: int = 32
    max_objects: int = 256

    def __post_init__(self):
        if type(self.dimensions) is not int or self.dimensions not in (2, 3):
            raise ValueError("dimensions must be 2 or 3")
        for key in ("max_speed", "horizon", "braking_deceleration", "max_observation_age"):
            if not _number(getattr(self, key)) or getattr(self, key) <= 0:
                raise ValueError(f"{key} must be finite and positive")
        for key in ("safety_margin", "reaction_time", "future_tolerance",
                    "observation_uncertainty_speed"):
            if not _number(getattr(self, key)) or getattr(self, key) < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        if (type(self.angular_samples) is not int or not 8 <= self.angular_samples <= 128
                or self.angular_samples % 4):
            raise ValueError("angular_samples must be divisible by 4 in 8..128")
        if type(self.max_objects) is not int or not 1 <= self.max_objects <= 4096:
            raise ValueError("max_objects must be 1..4096")


@dataclass(frozen=True)
class VelocityDecision:
    velocity: tuple[float, ...]
    stop_required: bool
    reason: str
    diagnostics: dict


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _scale(a, amount):
    return tuple(x * amount for x in a)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _norm(a):
    return math.sqrt(_dot(a, a))


def continuous_closest_distance(relative_position, relative_velocity, duration):
    """Exact minimum centre distance for one pair of constant-velocity segments."""
    if (not _number(duration) or duration < 0
            or type(relative_position) not in (tuple, list)
            or type(relative_velocity) not in (tuple, list)
            or len(relative_position) not in (2, 3)
            or len(relative_position) != len(relative_velocity)
            or any(not _number(value) for value in (*relative_position, *relative_velocity))):
        raise ValueError("finite same-dimension vectors and nonnegative duration required")
    speed_squared = _dot(relative_velocity, relative_velocity)
    at = (max(0., min(duration, -_dot(relative_position, relative_velocity) / speed_squared))
          if speed_squared > 1e-20 else 0.)
    return _norm(_add(relative_position, _scale(relative_velocity, at))), at


class VelocityAvoider:
    """Sample bounded velocities; reject swept collisions and rank clear candidates.

    Right-handed coordinates are required: in XY, right is (vy, -vx). A common
    right-hand passing preference resolves symmetric head-on ties. At crossing
    conflicts the lexicographically smallest robot identity has preference;
    lower priority robots prefer a clear wait or lateral/backward motion. This
    is local coordination, not mutual exclusion or a narrow-passage route planner.
    """

    def __init__(self, config=None):
        self.config = AvoidanceConfig() if config is None else config
        if not isinstance(self.config, AvoidanceConfig):
            raise TypeError("AvoidanceConfig required")

    def _observation(self, observation):
        d = self.config.dimensions
        if not isinstance(observation, MotionObservation):
            raise TypeError("MotionObservation required")
        if any(type(getattr(observation, key)) is not str or not getattr(observation, key)
               for key in ("identity", "world_id", "frame_id", "transform_revision")):
            raise ValueError("observation identities must be nonempty strings")
        for vector in (observation.position, observation.velocity):
            if (type(vector) not in (tuple, list) or len(vector) != d
                    or any(not _number(value) for value in vector)):
                raise ValueError("observation vectors must match configured dimension")
        if (not _number(observation.radius) or observation.radius < 0
                or not _number(observation.observed_at) or observation.observed_at < 0
                or type(observation.trajectory) not in (tuple, list)
                or len(observation.trajectory) > 128):
            raise ValueError("invalid radius, time or trajectory budget")
        last = observation.observed_at
        points = []
        for point in observation.trajectory:
            if (not isinstance(point, TrajectoryPoint) or not _number(point.at)
                    or point.at <= last or type(point.position) not in (tuple, list)
                    or len(point.position) != d
                    or any(not _number(value) for value in point.position)):
                raise ValueError("trajectory times must increase strictly after capture")
            points.append(TrajectoryPoint(point.at, tuple(point.position)))
            last = point.at
        return replace(observation, position=tuple(observation.position),
                       velocity=tuple(observation.velocity), trajectory=tuple(points))

    @staticmethod
    def _basis(observation):
        return observation.world_id, observation.frame_id, observation.transform_revision

    def _stop(self, reason, diagnostics, **extra):
        diagnostics.update(extra)
        diagnostics["predictionClear"] = False
        return VelocityDecision((0.,) * self.config.dimensions, True, reason, diagnostics)

    def _paths(self, observation, now):
        """Keep measured constant velocity even when a peer supplies a prediction."""
        age = max(0., now - observation.observed_at)
        start = _add(observation.position, _scale(observation.velocity, age))
        paths = [[(0., start), (self.config.horizon, _add(
            start, _scale(observation.velocity, self.config.horizon)))]]
        if observation.trajectory:
            knots = [(observation.observed_at, observation.position)] + [
                (point.at, point.position) for point in observation.trajectory]

            def at(time):
                for (t0, p0), (t1, p1) in pairwise(knots):
                    if time <= t1:
                        return _add(p0, _scale(_sub(p1, p0), max(0., time - t0) / (t1 - t0)))
                return knots[-1][1]

            times = [now] + [t for t, _ in knots if now < t < now + self.config.horizon]
            times.append(now + self.config.horizon)
            paths.append([(t - now, at(t)) for t in times])
        return paths

    def _clearance(self, state, velocity, objects, now):
        cfg = self.config
        own_age = max(0., now - state.observed_at)
        own_position = _add(state.position, _scale(state.velocity, own_age))
        closest, closest_id, closest_time, max_buffer = math.inf, None, None, 0.
        for observation in objects:
            age = max(0., now - observation.observed_at)
            paths = self._paths(observation, now)
            relative = _sub(paths[0][0][1], own_position)
            distance = _norm(relative)
            direction = _scale(relative, 1 / distance) if distance > 1e-12 else (0.,) * cfg.dimensions
            # Reserve stopping travel only in the direction toward this object.
            # Retreat must not be prohibited merely because a forward margin overlaps.
            closing = max(0., _dot(velocity, direction), _dot(state.velocity, direction))
            buffer = (cfg.safety_margin + closing * cfg.reaction_time
                      + closing * closing / (2 * cfg.braking_deceleration)
                      + (age + own_age) * cfg.observation_uncertainty_speed)
            max_buffer = max(max_buffer, buffer)
            radius = state.radius + observation.radius + buffer
            for path in paths:
                for (t0, p0), (t1, p1) in pairwise(path):
                    peer_velocity = _scale(_sub(p1, p0), 1 / (t1 - t0))
                    rel_position = _sub(p0, _add(own_position, _scale(velocity, t0)))
                    rel_velocity = _sub(peer_velocity, velocity)
                    separation, at = continuous_closest_distance(rel_position, rel_velocity, t1 - t0)
                    clearance = separation - radius
                    if clearance < closest:
                        closest, closest_id, closest_time = clearance, observation.identity, t0 + at
        return closest, closest_id, closest_time, max_buffer

    def _candidates(self, desired):
        cfg = self.config
        speed = _norm(desired)
        # Never invent motion when the local goal controller requests a stop.
        if speed <= 1e-12:
            return [(0.,) * cfg.dimensions]
        heading = math.atan2(desired[1], desired[0])
        candidates = [desired, (0.,) * cfg.dimensions]
        for factor in (1., .75, .5, .25):
            for index in range(cfg.angular_samples):
                angle = heading + 2 * math.pi * index / cfg.angular_samples
                xy = (speed * factor * math.cos(angle), speed * factor * math.sin(angle))
                if cfg.dimensions == 2:
                    candidates.append(xy)
                else:
                    for elevation in (-math.pi / 4, 0., math.pi / 4):
                        candidates.append((xy[0] * math.cos(elevation), xy[1] * math.cos(elevation),
                                           speed * factor * math.sin(elevation)))
        if cfg.dimensions == 3:
            candidates.extend([(0., 0., speed), (0., 0., -speed)])
        return list(dict.fromkeys(tuple(round(x, 12) for x in item) for item in candidates))

    def select(self, state, desired_velocity, *, peers=(), obstacles=(), now):
        """Select a world-frame velocity target; never dispatch or acknowledge it.

        Invalid, stale or differently registered inputs fail closed. A zero target
        requests braking; only subsequent measured motion can confirm a stop.
        """
        cfg = self.config
        diagnostics = {"schemaVersion": "robot.dynamic_avoidance.v1",
                       "algorithm": "continuous_velocity_sampling",
                       "controlModel": "holonomic_world_velocity",
                       "dimensions": cfg.dimensions, "horizonSeconds": cfg.horizon,
                       "physicalSafetyVerified": False, "physicalStopVerified": False,
                       "grantsExecution": False, "predictionClear": False,
                       "checkedCandidates": 0, "clearCandidates": 0}
        if not _number(now) or now < 0:
            return self._stop("invalid_clock", diagnostics)
        try:
            state = self._observation(state)
            if (type(peers) not in (tuple, list) or type(obstacles) not in (tuple, list)
                    or len(peers) + len(obstacles) > cfg.max_objects):
                raise ValueError("bounded observation lists required")
            peers = tuple(sorted((self._observation(item) for item in peers), key=lambda o: o.identity))
            obstacles = tuple(sorted((self._observation(item) for item in obstacles), key=lambda o: o.identity))
            ids = [item.identity for item in (*peers, *obstacles)]
            if len(ids) != len(set(ids)) or state.identity in ids:
                raise ValueError("unique peer and obstacle identities required")
            if (type(desired_velocity) not in (tuple, list) or len(desired_velocity) != cfg.dimensions
                    or any(not _number(value) for value in desired_velocity)):
                raise ValueError("finite desired velocity with configured dimension required")
        except (ValueError, TypeError) as error:
            return self._stop("invalid_observation", diagnostics, error=str(error))
        objects = (*peers, *obstacles)
        diagnostics.update(robotId=state.identity, measuredAt=state.observed_at, evaluatedAt=now,
                           basis={"worldId": state.world_id, "frameId": state.frame_id,
                                  "transformRevision": state.transform_revision},
                           peerIds=[item.identity for item in peers],
                           observationAges={item.identity: now - item.observed_at
                                            for item in (state, *objects)},
                           advisoryTrajectoryIds=[item.identity for item in objects if item.trajectory])
        for item in (state, *objects):
            if self._basis(item) != self._basis(state):
                return self._stop("basis_mismatch", diagnostics, invalidIdentity=item.identity)
            age = now - item.observed_at
            if age < -cfg.future_tolerance:
                return self._stop("future_observation", diagnostics, invalidIdentity=item.identity)
            if age > cfg.max_observation_age:
                kind = "self" if item is state else "peer" if item in peers else "obstacle"
                return self._stop(f"stale_{kind}_observation", diagnostics, invalidIdentity=item.identity)
        desired = tuple(desired_velocity)
        desired_speed = _norm(desired)
        if desired_speed > cfg.max_speed:
            desired = _scale(desired, cfg.max_speed / desired_speed)
        desired_speed = _norm(desired)
        forward = _scale(desired, 1 / desired_speed) if desired_speed > 1e-12 else (0.,) * cfg.dimensions
        right = (forward[1], -forward[0]) + ((0.,) if cfg.dimensions == 3 else ())
        desired_clearance = self._clearance(state, desired, objects, now)
        conflicting, yielding = [], []
        for peer in peers:
            if self._clearance(state, desired, (peer,), now)[0] < 0:
                conflicting.append(peer.identity)
                heading_alignment = _dot(forward, peer.velocity) / max(1e-12, _norm(peer.velocity))
                if peer.identity < state.identity and heading_alignment > -.7:
                    yielding.append(peer.identity)
        diagnostics.update(conflictingPeerIds=conflicting, yieldingTo=yielding,
                           rightOfWayOrder=sorted([state.identity, *[item.identity for item in peers]]))
        # Without a conflict the cost is squared desired-velocity mismatch.
        # A clear desired candidate has the global minimum (zero); expanding
        # the whole 3D lattice would only hold up the controller's lease loop.
        if desired_clearance[0] >= 0 and not conflicting:
            candidate = tuple(round(value, 12) for value in desired)
            clearance, obstacle_id, at, buffer = self._clearance(state, candidate, objects, now)
            if clearance >= -1e-9:
                diagnostics.update(checkedCandidates=1, clearCandidates=1, predictionClear=True,
                                   minimumPredictedClearance=clearance if objects else None,
                                   closestIdentity=obstacle_id, closestTimeSeconds=at, maximumBuffer=buffer,
                                   speedLimited=_norm(desired_velocity) > cfg.max_speed,
                                   desiredMinimumClearance=desired_clearance[0] if objects else None,
                                   desiredCandidateFastPath=True)
                return VelocityDecision(candidate, _norm(candidate) <= 1e-12,
                                        "goal_stop" if desired_speed <= 1e-12 else "desired_velocity", diagnostics)
        best = None
        candidates = self._candidates(desired)
        for index, candidate in enumerate(candidates):
            clearance, obstacle_id, at, buffer = self._clearance(state, candidate, objects, now)
            diagnostics["checkedCandidates"] += 1
            if clearance < -1e-9:
                continue
            diagnostics["clearCandidates"] += 1
            mismatch = _norm(_sub(candidate, desired)) ** 2
            # Stable right-hand passing breaks exact left/right ties. A crossing
            # loser prefers waiting to cutting across the winner's measured path.
            cost = mismatch - (0.3 * cfg.max_speed * _dot(candidate, right) if conflicting else 0.)
            if yielding:
                cost += 2.5 * cfg.max_speed * max(0., _dot(candidate, forward))
                cost += .25 * _norm(candidate) ** 2
            rank = (round(cost, 12), index)
            if best is None or rank < best[0]:
                best = (rank, candidate, clearance, obstacle_id, at, buffer)
        if best is None:
            return self._stop("no_collision_free_candidate", diagnostics,
                              desiredMinimumClearance=desired_clearance[0] if objects else None)
        _, candidate, clearance, obstacle_id, at, buffer = best
        stopped = _norm(candidate) <= 1e-12
        changed = _norm(_sub(candidate, desired)) > 1e-8
        reason = ("goal_stop" if desired_speed <= 1e-12 else "yielding_right_of_way" if yielding
                  else "avoiding_predicted_collision" if changed else "desired_velocity")
        diagnostics.update(predictionClear=True, minimumPredictedClearance=clearance if objects else None,
                           closestIdentity=obstacle_id, closestTimeSeconds=at, maximumBuffer=buffer,
                           speedLimited=_norm(desired_velocity) > cfg.max_speed,
                           desiredMinimumClearance=desired_clearance[0] if objects else None)
        return VelocityDecision(candidate, stopped, reason, diagnostics)
