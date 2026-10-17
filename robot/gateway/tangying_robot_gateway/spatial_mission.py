"""Versioned exploration evidence and conservative, resumable task leases.

This layer coordinates *objectives*, not flight control, gait or joint collision
avoidance. A registered world transform and identical voxel geometry are required
before observations may be shared. Expired execution authority never proves that
a robot stopped: uncertain executions retain their fence until stop is confirmed.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .spatial_navigation import _identifier, _integer, _pose, validate_volume

MAX_EVENTS = 100_000
MAX_JOURNAL_BYTES = 100_000_000
MAX_TASKS = 10_000
MAX_BATCH_CELLS = 250_000


class MissionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def geometry_id(volume):
    """A grid address space, independent of robot and map snapshot revisions."""
    normalized = validate_volume(volume)
    return _digest({key: normalized[key] for key in ("frameId", "worldFrameRevision", "origin",
                                                   "shape", "resolution")})


def _time(value, label="now_ms"):
    return _integer(value, label, 1, 9_007_199_254_740_991)


def _orientation_error(first, second):
    """Shortest quaternion angle, normalizing permitted numeric norm drift."""
    norm = math.sqrt(sum(value * value for value in first) * sum(value * value for value in second))
    cosine = min(1., abs(sum(a * b for a, b in zip(first, second, strict=True))) / norm)
    return 2 * math.acos(cosine)


class EventJournal:
    """Append-only hash chain; existing files are verified, never overwritten.

    Hashes detect accidental corruption, not malicious editing. A single owner
    must hold the journal; external transport authentication is a separate port.
    """
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self._events = []
        self._lock = threading.RLock()
        self._stream = None
        self._failed = False
        self._bytes = 0
        if self.path is not None:
            # Edge deployment is POSIX (ROS 2 Linux; macOS development). Never
            # permit two processes to issue authority from independent caches.
            import fcntl
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
            self._stream = os.fdopen(descriptor, "a+", encoding="utf-8")
            try:
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                self.close()
                raise MissionError("JOURNAL_OWNED", "another writer owns this mission journal") from error
        if self.path is not None and self.path.exists():
            self._bytes = self.path.stat().st_size
            if self._bytes > MAX_JOURNAL_BYTES:
                raise MissionError("JOURNAL_CAPACITY", "journal exceeds replay byte budget")
            for line in self.path.read_text().splitlines():
                event = json.loads(line)
                body = {key: value for key, value in event.items() if key != "hash"}
                expected_previous = self._events[-1]["hash"] if self._events else "0" * 64
                if (body.get("schemaVersion") != "exploration.event.v1"
                        or body.get("sequence") != len(self._events) + 1
                        or body.get("previousHash") != expected_previous
                        or event.get("hash") != _digest(body)):
                    raise MissionError("JOURNAL_CORRUPT", "event sequence or hash does not match")
                if len(self._events) >= MAX_EVENTS:
                    raise MissionError("JOURNAL_CAPACITY", "event budget exhausted")
                self._events.append(event)

    def close(self):
        if getattr(self, "_stream", None) is not None:
            self._stream.close()
            self._stream = None

    def __del__(self):
        self.close()

    @property
    def events(self):
        with self._lock:
            return copy.deepcopy(self._events)

    @property
    def last_hash(self):
        with self._lock:
            return self._events[-1]["hash"] if self._events else "0" * 64

    def append(self, kind, payload):
        with self._lock:
            if self._failed or self.path is not None and self._stream is None:
                raise MissionError("JOURNAL_UNAVAILABLE", "journal failed or its writer is closed")
            if len(self._events) >= MAX_EVENTS:
                raise MissionError("JOURNAL_CAPACITY", "event budget exhausted")
            body = {"schemaVersion": "exploration.event.v1", "sequence": len(self._events) + 1,
                    "previousHash": self._events[-1]["hash"] if self._events else "0" * 64,
                    "kind": kind, "payload": copy.deepcopy(payload)}
            event = {**body, "hash": _digest(body)}
            line = json.dumps(event, sort_keys=True, allow_nan=False) + "\n"
            byte_count = len(line.encode("utf-8"))
            if self._bytes + byte_count > MAX_JOURNAL_BYTES:
                raise MissionError("JOURNAL_CAPACITY", "event would exceed the replay byte budget")
            if self.path is not None:
                try:
                    self._stream.write(line)
                    self._stream.flush()
                    os.fsync(self._stream.fileno())
                except OSError:
                    self._failed = True
                    raise
            self._events.append(event)
            self._bytes += byte_count
            return copy.deepcopy(event)


class CoverageLedger:
    """Deduplicated *classified voxel* evidence, not surface reconstruction quality.

    ``target_ids`` is a declared task scope, never an inferred ground-truth set.
    Conflicting classifications remain unresolved and are excluded from coverage.
    Change world/geometry by starting a new mission; do not silently reuse IDs.
    """
    def __init__(self, mission_id, world_revision, target_ids=None, *, geometry_id=None,
                 journal_path=None, max_age_ms=5000):
        self.mission_id = _identifier(mission_id, "mission_id")
        self.world_revision = _identifier(world_revision, "world_revision")
        self.geometry_id = _identifier(geometry_id, "geometry_id") if geometry_id else None
        self.max_age_ms = _integer(max_age_ms, "max_age_ms", 1, 60_000)
        self.targets = None if target_ids is None else frozenset(self._ids(target_ids))
        if self.targets is not None and not self.targets:
            raise ValueError("declared target scope must not be empty")
        self._lock = threading.RLock()
        self._cells, self._sources, self._evidence = {}, {}, set()
        self._conflicts = set()
        self._total = self._repeated = 0
        self.journal = EventJournal(journal_path)
        context = {"missionId": self.mission_id, "worldFrameRevision": self.world_revision,
                   "geometryId": self.geometry_id, "maxAgeMs": self.max_age_ms,
                   "targetIds": sorted(self.targets) if self.targets is not None else None}
        events = self.journal.events
        if events:
            if events[0]["kind"] != "coverage_created" or events[0]["payload"] != context:
                raise MissionError("MISSION_CONTEXT_MISMATCH", "coverage journal belongs to another scope")
            for event in events[1:]:
                if event["kind"] != "observation":
                    raise MissionError("JOURNAL_CORRUPT", "unexpected coverage event")
                self._apply(event["payload"])
        else:
            self.journal.append("coverage_created", context)

    @staticmethod
    def _ids(values):
        if len(values) > MAX_BATCH_CELLS:
            raise ValueError("voxel ID budget exceeded")
        return [_integer(value, "voxel ID", 0, MAX_BATCH_CELLS - 1) for value in values]

    @property
    def observed_ids(self):
        with self._lock:
            return set(self._cells) - self._conflicts

    @property
    def conflicting_ids(self):
        with self._lock:
            return set(self._conflicts)

    def _apply(self, payload):
        source = (payload["robotId"], payload["sourceId"])
        self._sources[source] = (payload["sequence"], payload["fingerprint"],
                                 payload["observedAtMs"])
        self._evidence.add(payload["evidenceId"])
        for voxel, value in payload["classified"]:
            self._total += 1
            if voxel in self._cells:
                self._repeated += 1
                if self._cells[voxel] != value:
                    self._conflicts.add(voxel)
            else:
                self._cells[voxel] = value

    def observe(self, robot_id, source_id, sequence, observed_at_ms, now_ms,
                world_revision, classified, evidence_id, *, geometry_id=None):
        with self._lock:
            robot_id, source_id = (_identifier(robot_id, "robot_id"),
                                   _identifier(source_id, "source_id"))
            _time(now_ms)
            _time(observed_at_ms, "observed_at_ms")
            _integer(sequence, "sequence", 1, 9_007_199_254_740_991)
            _identifier(evidence_id, "evidence_id")
            if world_revision != self.world_revision or geometry_id != self.geometry_id:
                raise MissionError("WORLD_MISMATCH", "explicit world registration and grid geometry required")
            if observed_at_ms > now_ms + 1000 or now_ms - observed_at_ms > self.max_age_ms:
                raise MissionError("STALE_OBSERVATION", "observation age is outside the admitted window")
            if type(classified) is not dict:
                raise ValueError("classified must map integer voxel IDs to 0 or 100")
            self._ids(classified)
            if any(type(value) is not int or value not in (0, 100) for value in classified.values()):
                raise ValueError("unknown or non-integer classifications are not measurements")
            payload = {"robotId": robot_id, "sourceId": source_id, "sequence": sequence,
                       "observedAtMs": observed_at_ms, "evidenceId": evidence_id,
                       "classified": sorted(map(list, classified.items()))}
            fingerprint = _digest(payload)
            payload["fingerprint"] = fingerprint
            previous = self._sources.get((robot_id, source_id))
            if previous and sequence <= previous[0]:
                if sequence == previous[0] and fingerprint == previous[1]:
                    return {"accepted": False, "duplicate": True, "newlyObserved": 0,
                            "repeated": 0, "conflicts": 0}
                raise MissionError("OBSERVATION_REPLAY", "source sequence is stale or contradicts its receipt")
            if previous and observed_at_ms < previous[2]:
                raise MissionError("OBSERVATION_REPLAY", "source capture time moved backwards")
            if evidence_id in self._evidence:
                raise MissionError("EVIDENCE_REPLAY", "an evidence ID cannot acquire a new sequence")
            before, repeated, conflicts = len(self.observed_ids), self._repeated, len(self._conflicts)
            new_ids = set(classified) - self._cells.keys()
            self.journal.append("observation", payload)
            self._apply(payload)
            return {"accepted": True, "duplicate": False,
                    "newlyObserved": len(new_ids), "coverageDelta": len(self.observed_ids) - before,
                    "repeated": self._repeated - repeated,
                    "conflicts": len(self._conflicts) - conflicts}

    def summary(self):
        with self._lock:
            covered = self.observed_ids
            count = len(covered if self.targets is None else covered & self.targets)
            denominator = None if self.targets is None else len(self.targets)
            return {"schemaVersion": "exploration.coverage.v1", "missionId": self.mission_id,
                    "worldFrameRevision": self.world_revision, "geometryId": self.geometry_id,
                    "metric": "classified_voxel_coverage", "scope": "declared_targets",
                    "observedVoxels": count, "targetVoxels": denominator,
                    "coverage": count / denominator if denominator else None,
                    "conflictingVoxels": len(self._conflicts),
                    "repeatedObservations": self._repeated, "totalObservations": self._total,
                    "repeatRatio": self._repeated / self._total if self._total else None,
                    "lastEventHash": self.journal.last_hash}


def _candidate_targets(candidate):
    """Keep predicted known-map coverage separate from unknown-map gain."""
    if candidate.get("objectiveKind") == "explicit_scope_coverage":
        if candidate.get("visibleUnknownVoxelIds") != []:
            raise ValueError("known-map coverage predictions must not relabel measured voxels as unknown")
        return CoverageLedger._ids(candidate["expectedObservedVoxelIds"])
    if "expectedObservedVoxelIds" in candidate:
        raise ValueError("predicted coverage requires an explicit objective kind")
    return CoverageLedger._ids(candidate["visibleUnknownVoxelIds"])


class TaskCoordinator:
    """Capability-filtered proposal leases; one execution in flight by default.

    Coordination ablation disables target overlap scoring only. The execution
    fence, world admission and uncertain-outcome handling are never ablated.
    There is no implication of multi-robot geometric collision avoidance here.
    """
    def __init__(self, mission_id, world_revision, *, use_coordination=True, journal_path=None):
        self.mission_id = _identifier(mission_id, "mission_id")
        self.world_revision = _identifier(world_revision, "world_revision")
        if type(use_coordination) is not bool:
            raise ValueError("use_coordination must be boolean")
        self.use_coordination = use_coordination
        self._lock = threading.RLock()
        self._tasks = {}
        self.journal = EventJournal(journal_path)
        context = {"missionId": mission_id, "worldFrameRevision": world_revision,
                   "useCoordination": use_coordination}
        events = self.journal.events
        if events:
            if events[0]["kind"] != "coordinator_created" or events[0]["payload"] != context:
                raise MissionError("MISSION_CONTEXT_MISMATCH", "task journal belongs to another mission")
            for event in events[1:]:
                if event["kind"] == "runner_created":
                    continue
                if event["kind"] != "task":
                    raise MissionError("JOURNAL_CORRUPT", "unexpected coordinator event")
                task = event["payload"]
                self._tasks[task["taskId"]] = task
        else:
            self.journal.append("coordinator_created", context)

    @property
    def tasks(self):
        with self._lock:
            return copy.deepcopy(list(self._tasks.values()))

    def _save(self, task):
        self.journal.append("task", task)
        self._tasks[task["taskId"]] = copy.deepcopy(task)
        return copy.deepcopy(task)

    def expire(self, now_ms):
        with self._lock:
            _time(now_ms)
            expired = []
            for task in list(self._tasks.values()):
                if task["state"] in {"assigned", "running"} and now_ms >= task["deadlineMs"]:
                    updated = {**task, "state": "uncertain" if task["state"] == "running" else "expired",
                               "updatedAtMs": now_ms, "reason": "lease_expired"}
                    expired.append(self._save(updated))
            return expired

    def assign(self, robot_id, candidates, now_ms, lease_ms=5000):
        with self._lock:
            _identifier(robot_id, "robot_id")
            _time(now_ms)
            _integer(lease_ms, "lease_ms", 100, 60_000)
            self.expire(now_ms)
            active = [task for task in self._tasks.values()
                      if task["state"] in {"assigned", "running", "uncertain"}]
            if any(task["robotId"] == robot_id for task in active):
                return None
            if len(self._tasks) >= MAX_TASKS:
                raise MissionError("TASK_CAPACITY", "task receipt budget exhausted")
            if type(candidates) is not list or len(candidates) > 1024:
                raise ValueError("candidates must be a bounded list")
            used = {voxel for task in active for voxel in _candidate_targets(task["candidate"])}
            ranked = []
            for candidate in candidates:
                path = candidate["path"]
                if (path.get("schemaVersion") != "navigation.path.v1"
                        or path.get("robotId") != robot_id
                        or path.get("worldFrameRevision") != self.world_revision):
                    raise MissionError("WORLD_MISMATCH", "proposal must be planned for this robot and world")
                _identifier(candidate["targetId"], "targetId")
                _pose(candidate["pose"])
                waypoints = path.get("waypoints")
                if type(waypoints) is not list or not 2 <= len(waypoints) <= 4096:
                    raise ValueError("candidate requires a bounded navigation path")
                poses = [_pose(pose) for pose in waypoints]
                if poses[-1] != candidate["pose"]:
                    raise ValueError("candidate pose must equal the final path waypoint")
                length = sum(math.dist(a[:3], b[:3]) for a, b in itertools.pairwise(poses))
                declared = candidate["pathLengthM"]
                if (type(declared) not in (int, float) or not math.isfinite(declared)
                        or abs(declared - length) > 1e-6):
                    raise ValueError("candidate path length does not match its waypoints")
                ids = _candidate_targets(candidate)
                score = candidate["score"]
                if type(score) not in (float, int) or not math.isfinite(score):
                    raise ValueError("candidate score must be finite")
                if self.use_coordination and any(task["candidate"]["targetId"] == candidate["targetId"]
                                                 for task in active):
                    continue
                overlap = len(set(ids) & used) / max(1, len(set(ids))) if self.use_coordination else 0.
                ranked.append((float(score) - overlap * (abs(score) + 1), candidate["targetId"], candidate))
            if not ranked:
                return None
            chosen = min(ranked, key=lambda item: (-item[0], item[1]))[2]
            task_id = _digest([self.mission_id, robot_id, len(self._tasks) + 1, chosen["targetId"]])
            return self._save({"schemaVersion": "exploration.task.v1", "missionId": self.mission_id,
                               "worldFrameRevision": self.world_revision, "taskId": task_id,
                               "robotId": robot_id, "candidate": copy.deepcopy(chosen),
                               "pathDigest": _digest(chosen["path"]), "state": "assigned",
                               "deadlineMs": now_ms + lease_ms, "updatedAtMs": now_ms})

    def _get(self, task_id, robot_id):
        task = self._tasks.get(task_id)
        if task is None or task["robotId"] != robot_id:
            raise MissionError("TASK_OWNER_MISMATCH", "task belongs to another robot or is unknown")
        return copy.deepcopy(task)

    def start(self, task_id, robot_id, now_ms):
        with self._lock:
            self.expire(now_ms)
            task = self._get(task_id, robot_id)
            if task["state"] != "assigned":
                raise MissionError("TASK_NOT_ASSIGNED", "task cannot be dispatched again")
            if any(other["taskId"] != task_id and other["state"] in {"running", "uncertain"}
                   for other in self._tasks.values()):
                raise MissionError("EXECUTION_FENCED", "another execution is running or unconfirmed")
            if now_ms < task["updatedAtMs"]:
                raise MissionError("CLOCK_REGRESSION", "task transition time moved backwards")
            return self._save({**task, "state": "running", "updatedAtMs": now_ms,
                               "startedAtMs": now_ms})

    def finish(self, task_id, robot_id, outcome, now_ms):
        with self._lock:
            _time(now_ms)
            task = self._get(task_id, robot_id)
            if now_ms < task["updatedAtMs"]:
                raise MissionError("CLOCK_REGRESSION", "task transition time moved backwards")
            if outcome not in {"succeeded", "rejected", "unknown"}:
                raise ValueError("outcome must be succeeded, rejected or unknown")
            if task["state"] != "running":
                raise MissionError("TASK_NOT_RUNNING", "only a running task accepts an execution result")
            # Late success cannot retrospectively authorize movement past its lease.
            state = "uncertain" if outcome == "unknown" or now_ms >= task["deadlineMs"] else outcome
            return self._save({**task, "state": state, "updatedAtMs": now_ms})

    def acknowledge_stop(self, task_id, robot_id, now_ms):
        with self._lock:
            _time(now_ms)
            task = self._get(task_id, robot_id)
            if now_ms < task["updatedAtMs"]:
                raise MissionError("CLOCK_REGRESSION", "task transition time moved backwards")
            if task["state"] not in {"running", "uncertain"}:
                raise MissionError("STOP_NOT_REQUIRED", "no uncertain execution to resolve")
            return self._save({**task, "state": "stopped", "updatedAtMs": now_ms,
                               "reason": "controller_stop_acknowledged"})

    def release_assignment(self, task_id, robot_id, now_ms, reason="snapshot_changed"):
        with self._lock:
            _time(now_ms)
            task = self._get(task_id, robot_id)
            if task["state"] != "assigned" or now_ms < task["updatedAtMs"]:
                raise MissionError("TASK_NOT_ASSIGNED", "cannot release a dispatched task as unexecuted")
            return self._save({**task, "state": "cancelled", "updatedAtMs": now_ms, "reason": reason})


@dataclass(frozen=True)
class SpatialSnapshot:
    """Detached measured map/pose in an explicitly registered world basis.

    Localizers and map providers own covariance/TF/calibration admission. The
    sequence and capture time refer to the localization observation, not a poll.
    """
    volume: dict
    pose: list
    sequence: int
    observed_at_ms: int
    source_id: str
    localized: bool = True


@dataclass(frozen=True)
class DriverResult:
    """Controller receipt; success additionally needs a new measured pose."""
    outcome: str
    task_id: str
    path_digest: str


class SpatialMapPort(Protocol):
    def snapshot(self, robot_id: str) -> SpatialSnapshot:
        """Read registered navigation.volume.v1 and measured SE3 localization."""

    def capture(self, robot_id: str) -> dict:
        """Update the local map from a measurement and return a ledger packet.

        Fields: sourceId, sequence, observedAtMs, worldFrameRevision, geometryId,
        classified (integer voxel -> 0|100), evidenceId. No predicted occupancy.
        """

    def check_cancel(self) -> None:
        """Raise before any new physical dispatch when cancelled."""


class SpatialDriverPort(Protocol):
    def execute(self, robot_id: str, path: dict, task_id: str, deadline_ms: int) -> DriverResult:
        """Use existing approved, commissioned controller/lease/path handshake.

        Revalidate dynamics, current obstacles and other robots. Respect the
        deadline independently; this synchronous port is not a watchdog itself.
        A path digest is a receipt binding, not an authorization token.
        """

    def stop(self, robot_id: str, task_id: str) -> bool:
        """True only with the registered controller's positive stop receipt."""


class SpatialReportPort(Protocol):
    def publish(self, event: dict) -> None:
        """Publish or persist progress; must not authorize movement."""


class SpatialMissionRunner:
    """Bounded closed loop over replaceable map, exploration and controller ports.

    ``explorer_factory(snapshot)`` returns an object with ``propose``. It may
    replace the geometric backend without changing evidence/task contracts.
    Existing 2D mapping.build is deliberately not silently redirected here.
    """
    def __init__(self, explorer_factory, map_port, driver_port, report_port, ledger, coordinator,
                 *, clock_ms, max_steps=100, max_travel_m=100., max_duration_ms=600_000,
                 max_snapshot_age_ms=1000, lease_ms=5000):
        if ledger.mission_id != coordinator.mission_id or ledger.world_revision != coordinator.world_revision:
            raise MissionError("MISSION_CONTEXT_MISMATCH", "ports must share the same mission and world")
        self.explorer_factory, self.map_port = explorer_factory, map_port
        self.driver, self.report = driver_port, report_port
        self.ledger, self.coordinator, self.clock_ms = ledger, coordinator, clock_ms
        self.max_steps = _integer(max_steps, "max_steps", 1, MAX_TASKS)
        if type(max_travel_m) not in (int, float) or not math.isfinite(max_travel_m) or max_travel_m <= 0:
            raise ValueError("max_travel_m must be positive and finite")
        self.max_travel_m = max_travel_m
        self.max_duration_ms = _integer(max_duration_ms, "max_duration_ms", 100, 86_400_000)
        self.max_snapshot_age_ms = _integer(max_snapshot_age_ms, "max_snapshot_age_ms", 1, 60_000)
        self.lease_ms = _integer(lease_ms, "lease_ms", 100, 60_000)
        created = [event["payload"] for event in coordinator.journal.events
                   if event["kind"] == "runner_created"]
        context = {"maxSteps": self.max_steps, "maxTravelM": self.max_travel_m,
                   "maxDurationMs": self.max_duration_ms, "geometryId": ledger.geometry_id}
        if created:
            if len(created) != 1 or any(created[0][key] != value for key, value in context.items()):
                raise MissionError("MISSION_CONTEXT_MISMATCH", "resuming cannot reset mission budgets")
            self.started_at_ms = created[0]["startedAtMs"]
        else:
            self.started_at_ms = _time(clock_ms())
            coordinator.journal.append("runner_created", {**context, "startedAtMs": self.started_at_ms})
        self._steps, self._travel = 0, 0.
        self._excluded, self._sequences = {}, {}
        self._lock = threading.RLock()

    def _snapshot(self, robot_id):
        if self.ledger.conflicting_ids:
            raise MissionError("MAP_CONFLICT", "conflicting measurements require a newly registered mission basis")
        sample = self.map_port.snapshot(robot_id)
        if not isinstance(sample, SpatialSnapshot):
            raise TypeError("map port must return SpatialSnapshot")
        volume, pose = validate_volume(sample.volume), _pose(sample.pose)
        now = _time(self.clock_ms())
        _identifier(sample.source_id, "localization source")
        _integer(sample.sequence, "localization sequence", 1, 9_007_199_254_740_991)
        _time(sample.observed_at_ms, "localization capture time")
        if (sample.localized is not True or sample.observed_at_ms > now + 1000
                or now - sample.observed_at_ms > self.max_snapshot_age_ms):
            raise MissionError("LOCALIZATION_STALE", "localized, fresh pose evidence is required")
        if (volume["robotId"] != robot_id or volume["worldFrameRevision"] != self.ledger.world_revision
                or self.ledger.geometry_id != geometry_id(volume)):
            raise MissionError("WORLD_MISMATCH", "map snapshot is not registered in this mission grid")
        previous = self._sequences.get(robot_id)
        if previous and (sample.source_id != previous[0] or sample.sequence < previous[1]
                         or sample.observed_at_ms < previous[2]):
            raise MissionError("LOCALIZATION_REPLAY", "localization source or sequence changed backwards")
        self._sequences[robot_id] = (sample.source_id, sample.sequence, sample.observed_at_ms)
        return SpatialSnapshot(volume, pose, sample.sequence, sample.observed_at_ms,
                               sample.source_id, True)

    def _capture(self, robot_id):
        sample = self._snapshot(robot_id)
        packet = self.map_port.capture(robot_id)
        classified = packet["classified"]
        if type(classified) is not dict or any(type(voxel) is not int
                or not 0 <= voxel < math.prod(sample.volume["shape"]) for voxel in classified):
            raise MissionError("OBSERVATION_BOUNDS", "observed voxel IDs exceed the registered grid")
        return self.ledger.observe(robot_id, packet["sourceId"], packet["sequence"],
                                   packet["observedAtMs"], self.clock_ms(),
                                   packet["worldFrameRevision"], packet["classified"],
                                   packet["evidenceId"], geometry_id=packet["geometryId"])

    def _emit(self, robot_id, reason, **details):
        event = {"schemaVersion": "exploration.progress.v1", "missionId": self.ledger.mission_id,
                 "robotId": robot_id, "reason": reason, "steps": self._steps,
                 "commandedPathM": self._travel, "coverage": self.ledger.summary(), **details}
        self.report.publish(copy.deepcopy(event))
        return event

    def _stop_uncertain(self, task, robot_id):
        try:
            acknowledged = self.driver.stop(robot_id, task["taskId"]) is True
        except Exception:  # noqa: BLE001 - transport faults do not acknowledge a stop.
            acknowledged = False
        if acknowledged:
            try:
                self.coordinator.acknowledge_stop(task["taskId"], robot_id, self.clock_ms())
            except (OSError, MissionError):
                # A physical stop without a durable receipt retains the fence.
                acknowledged = False
        return acknowledged

    def step(self, robot_id):
        with self._lock:
            _identifier(robot_id, "robot_id")
            self.map_port.check_cancel()
            now = _time(self.clock_ms())
            self.coordinator.expire(now)
            tasks = self.coordinator.tasks
            started = [task for task in tasks if "startedAtMs" in task]
            self._steps = len(started)
            self._travel = sum(task["candidate"]["pathLengthM"] for task in started)
            for task in tasks:
                if task["state"] in {"succeeded", "rejected"}:
                    self._excluded.setdefault(task["robotId"], set()).add(task["candidate"]["targetId"])
            if now < self.started_at_ms:
                raise MissionError("CLOCK_REGRESSION", "mission clock moved backwards")
            if any(task["state"] in {"running", "uncertain"} for task in self.coordinator.tasks):
                return self._emit(robot_id, "execution_fenced", termination="await_stop_evidence")
            if self.ledger.targets is not None and self.ledger.targets <= self.ledger.observed_ids:
                return self._emit(robot_id, "coverage_complete", termination="completed")
            if (self._steps >= self.max_steps or self._travel >= self.max_travel_m
                    or now - self.started_at_ms >= self.max_duration_ms):
                return self._emit(robot_id, "budget_exhausted", termination="partial")
            self._capture(robot_id)
            if self.ledger.targets is not None and self.ledger.targets <= self.ledger.observed_ids:
                return self._emit(robot_id, "coverage_complete", termination="completed")
            sample = self._snapshot(robot_id)
            explorer = self.explorer_factory(sample)
            excluded = self._excluded.setdefault(robot_id, set())
            proposal = explorer.propose(sample.pose, excluded_targets=excluded,
                                        observed_targets=self.ledger.observed_ids
                                        if self.coordinator.use_coordination else ())
            scope = proposal.get("scope")
            coverage_candidates = [candidate for candidate in proposal.get("candidates", [])
                                   if candidate.get("objectiveKind") == "explicit_scope_coverage"]
            if (scope is not None or coverage_candidates) and (
                        type(scope) is not dict or scope.get("schemaVersion") != "coverage.scope.v1"
                        or scope.get("geometryId") != self.ledger.geometry_id
                        or set(CoverageLedger._ids(scope.get("targetVoxelIds", []))) != self.ledger.targets
                        or any(not set(_candidate_targets(candidate)) <= self.ledger.targets
                               for candidate in coverage_candidates)):
                raise MissionError("COVERAGE_SCOPE_MISMATCH", "proposal and evidence ledger must share the target scope")
            if not proposal.get("candidates"):
                return self._emit(robot_id, "no_candidate", termination="partial",
                                  diagnostics=proposal.get("diagnostics", {}))
            for candidate in proposal["candidates"]:
                path = candidate["path"]
                if any(path.get(key) != sample.volume[key] for key in (
                        "robotId", "mapId", "mapRevision", "frameId",
                        "worldFrameRevision", "calibrationRevision")):
                    raise MissionError("PROPOSAL_SNAPSHOT_MISMATCH", "proposal is bound to another snapshot")
                waypoints = path.get("waypoints")
                if type(waypoints) is not list or not waypoints:
                    raise MissionError("PROPOSAL_START_MISMATCH", "proposal has no measured start pose")
                first = _pose(waypoints[0])
                if (math.dist(first[:3], sample.pose[:3]) > .02
                        or _orientation_error(first[3:], sample.pose[3:]) > .02):
                    raise MissionError("PROPOSAL_START_MISMATCH", "proposal must start at the measured pose")
            now = self.clock_ms()
            remaining_ms = self.max_duration_ms - (now - self.started_at_ms)
            if remaining_ms < 100:
                return self._emit(robot_id, "budget_exhausted", termination="partial")
            candidates = [candidate for candidate in proposal["candidates"]
                          if candidate["pathLengthM"] <= self.max_travel_m - self._travel]
            if not candidates:
                return self._emit(robot_id, "travel_budget", termination="partial")
            task = self.coordinator.assign(robot_id, candidates, now, min(self.lease_ms, remaining_ms))
            if task is None:
                return self._emit(robot_id, "no_assignment", termination="partial")
            try:
                self.map_port.check_cancel()
                fresh = self._snapshot(robot_id)
                # A new map or a shifted robot requires a newly certified path.
                if (fresh.volume != sample.volume or math.dist(fresh.pose[:3], sample.pose[:3]) > .02
                        or _orientation_error(fresh.pose[3:], sample.pose[3:]) > .02):
                    raise MissionError("SNAPSHOT_CHANGED", "replan against the new registered snapshot")
                if self.clock_ms() - self.started_at_ms >= self.max_duration_ms:
                    raise MissionError("MISSION_DEADLINE", "planning exhausted the mission time budget")
                self.coordinator.start(task["taskId"], robot_id, self.clock_ms())
            except Exception:
                current = next(item for item in self.coordinator.tasks if item["taskId"] == task["taskId"])
                if current["state"] == "assigned":
                    self.coordinator.release_assignment(task["taskId"], robot_id, self.clock_ms())
                raise
            self._steps += 1
            self._travel += task["candidate"]["pathLengthM"]
            dispatched_at_ms = self.clock_ms()
            try:
                result = self.driver.execute(robot_id, copy.deepcopy(task["candidate"]["path"]),
                                             task["taskId"], task["deadlineMs"])
                if (not isinstance(result, DriverResult) or result.task_id != task["taskId"]
                        or result.path_digest != task["pathDigest"]
                        or result.outcome not in {"succeeded", "rejected", "unknown"}):
                    raise MissionError("RECEIPT_MISMATCH", "controller receipt does not bind this dispatch")
                if result.outcome == "succeeded":
                    arrived = self._snapshot(robot_id)
                    goal = task["candidate"]["pose"]
                    if (arrived.sequence <= sample.sequence or arrived.observed_at_ms <= sample.observed_at_ms
                            or arrived.observed_at_ms < dispatched_at_ms
                            or arrived.volume["calibrationRevision"] != sample.volume["calibrationRevision"]
                            or math.dist(arrived.pose[:3], goal[:3]) > .05
                            or _orientation_error(arrived.pose[3:], goal[3:]) > .10):
                        raise MissionError("ARRIVAL_NOT_PROVEN", "new post-command goal pose is required")
                    self._capture(robot_id)
                outcome = result.outcome
            except Exception as error:  # noqa: BLE001 - retain unknown physical outcomes.
                outcome = "unknown"
                failure = getattr(error, "code", type(error).__name__)
            else:
                failure = None
            try:
                finished = self.coordinator.finish(task["taskId"], robot_id, outcome, self.clock_ms())
            except (OSError, MissionError) as error:
                # Logging failure after dispatch must not suppress the separate
                # controller stop path (notably ENOSPC and journal capacity).
                stopped = self._stop_uncertain(task, robot_id)
                return self._emit(robot_id, "execution_journal_failed", taskId=task["taskId"],
                                  stopAcknowledged=stopped, failure=getattr(error, "code", "JOURNAL_IO"),
                                  termination="partial")
            if finished["state"] == "uncertain":
                stopped = self._stop_uncertain(task, robot_id)
                return self._emit(robot_id, "execution_uncertain", taskId=task["taskId"],
                                  stopAcknowledged=stopped, failure=failure, termination="partial")
            excluded.add(task["candidate"]["targetId"])
            if (finished["state"] == "succeeded" and self.ledger.targets is not None
                    and self.ledger.targets <= self.ledger.observed_ids):
                return self._emit(robot_id, "coverage_complete", taskId=task["taskId"], termination="completed")
            return self._emit(robot_id, finished["state"], taskId=task["taskId"],
                              diagnostics=proposal.get("diagnostics", {}))
