"""Authenticated local navigation boundary. It never publishes motion itself."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contracts import ContractError, validate_goal

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def failure_observation(world):
    """Freeze bounded diagnostics from the actual stop decision, never a later poll."""
    result = {}
    if type(world.get("ready")) is bool:
        result["ready"] = world["ready"]
    for key in ("checkedAtUnixMs", "poseObservedAtUnixMs", "odomObservedAtUnixMs"):
        if type(world.get(key)) is int:
            result[key] = world[key]
    for key in ("mapRevision", "poseSource", "robotId", "mode", "localizationState"):
        if isinstance(world.get(key), str):
            result[key] = world[key][:256]
    for key, allowed in (
        ("inputAgeMs", ("rtabmap", "mapPose", "odometry", "base", "head")),
        ("sensorObservedAtUnixMs", ("base", "head")),
        ("visualQuality", ("currentFrameWords", "dictionaryWords", "ready")),
    ):
        values = world.get(key)
        if isinstance(values, dict):
            result[key] = {
                name: values[name]
                for name in allowed
                if type(values.get(name)) is bool
                or (type(values.get(name)) in (int, float) and math.isfinite(values[name]))
            }
    if isinstance(world.get("readinessBlockers"), list):
        result["readinessBlockers"] = [
            value[:128] for value in world["readinessBlockers"][:32] if isinstance(value, str)
        ]
    return result


class ApiError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


class GoalRegistry:
    def __init__(self, driver, database, *, observation_hold_seconds=2.0):
        if (type(observation_hold_seconds) not in (int, float)
                or not math.isfinite(observation_hold_seconds)
                or not 0 < observation_hold_seconds <= 15):
            raise ValueError("observation hold must be finite and in (0, 15] seconds")
        self.observation_hold_seconds = float(observation_hold_seconds)
        self.driver = driver
        self.lock = threading.RLock()
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, command TEXT UNIQUE NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL)"
        )
        if "failure_observation" not in {
            row[1] for row in self.db.execute("PRAGMA table_info(goals)")
        }:
            self.db.execute(
                "ALTER TABLE goals ADD COLUMN failure_observation TEXT NOT NULL DEFAULT '{}'"
            )
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(goals)")}
        for name, definition in (
            ("completion_source", "TEXT NOT NULL DEFAULT ''"),
            ("completion_pose_stamp", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                self.db.execute(f"ALTER TABLE goals ADD COLUMN {name} {definition}")
        self.db.execute(
            "UPDATE goals SET state='FAILED', message='NAVIGATION_SERVICE_RESTARTED' WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED')"
        )
        self.db.commit()
        self.active_id = None
        self.last_poll = 0.0
        self.observation_loss_since = None
        self.holds = {}

    def observation_hold_expired(self):
        return (self.observation_loss_since is not None
                and time.monotonic()-self.observation_loss_since >= self.observation_hold_seconds)

    def observation_hold(self, goal_id, world):
        """A driver may opt in only when it gates the real velocity output."""
        setter = getattr(self.driver, "set_observation_hold", None)
        # A delayed check must not resume an expired goal merely because the
        # newest sample is fresh. Keep the real publisher held until cancellation.
        if self.observation_hold_expired():
            return False
        if world["ready"]:
            if self.observation_loss_since is not None:
                self.holds[goal_id][-1]["resumedAtUnixMs"] = int(time.time()*1000)
                self.observation_loss_since = None
                setter(False)
            return False
        blockers = set(world.get("readinessBlockers", []))
        transient = {"BASE_RGBD_STALE", "HEAD_RGBD_STALE", "RTABMAP_PROCESSING_STALE",
                     "MAP_POSE_STALE", "ODOMETRY_STALE", "LOCALIZATION_UNAVAILABLE"}
        if (not getattr(self.driver, "supports_observation_hold", False)
                or not callable(setter) or not blockers or not blockers <= transient
                or blockers == {"LOCALIZATION_UNAVAILABLE"}):
            return False
        setter(True)  # Immediate zero at the real publisher, never a fake ready state.
        if self.observation_loss_since is None:
            self.observation_loss_since = time.monotonic()
            records = self.holds.setdefault(goal_id, [])
            records.append({"stoppedAtUnixMs": int(time.time()*1000),
                            "maxDurationSeconds": self.observation_hold_seconds,
                            "observation": failure_observation(world)})
            del records[:-32]
        return not self.observation_hold_expired()

    def submit(self, body):
        try:
            goal = validate_goal(body)
        except ContractError as error:
            raise ApiError(400, "INVALID_NAVIGATION_GOAL") from error
        encoded = json.dumps(goal, sort_keys=True, separators=(",", ":"))
        with self.lock:
            row = self.db.execute(
                "SELECT id,request FROM goals WHERE command=?", (goal["commandId"],)
            ).fetchone()
            if row:
                if row[1] != encoded:
                    raise ApiError(409, "COMMAND_ID_CONFLICT")
                return self.status(row[0])
            if self.active_id:
                raise ApiError(409, "NAVIGATION_BUSY")
            if not self.driver.map_status()["ready"]:
                raise ApiError(503, "NAVIGATION_NOT_READY")
            goal_id = hashlib.sha256(goal["commandId"].encode()).hexdigest()
            self.db.execute(
                "INSERT INTO goals (id,command,request,state,message) VALUES (?,?,?,?,?)",
                (goal_id, goal["commandId"], encoded, "PENDING", ""),
            )
            self.db.commit()
            self.active_id = goal_id
            self.observation_loss_since = None
            self.last_poll = time.monotonic()
            try:
                self.driver.start(goal_id, goal)
            except Exception:  # noqa: BLE001 — adapter errors must fail closed without leaking transport details.
                self.update(goal_id, "FAILED", "NAV2_GOAL_SUBMISSION_FAILED")
            return self.status(goal_id)

    def update(self, goal_id, state, message="", *, observation=None,
               completion_source="", completion_pose_stamp=0):
        if state not in TERMINAL | {"RUNNING", "PENDING"}:
            raise ValueError("invalid goal state")
        with self.lock:
            row = self.db.execute("SELECT state FROM goals WHERE id=?", (goal_id,)).fetchone()
            if not row or row[0] in TERMINAL or row[0] == state:
                return
            if state == "SUCCEEDED" and (
                completion_source not in {"nav2_action", "pose_confirmation"}
                or type(completion_pose_stamp) is not int or completion_pose_stamp <= 0
            ):
                raise ValueError("navigation success requires a known source and pose timestamp")
            if state == "FAILED" and observation is None:
                # Freeze at this new failure transition, not a later status
                # read. The terminal-state guard above forbids backfilling old
                # failures or rewriting them after a delayed action callback.
                try:
                    observation = self.driver.map_status()
                except Exception:  # noqa: BLE001 — diagnostic failure must never prevent stopping.
                    observation = {}
            self.db.execute(
                "UPDATE goals SET state=?,message=?,failure_observation=?,completion_source=?,completion_pose_stamp=? WHERE id=?",
                (
                    state,
                    message,
                    json.dumps(failure_observation(observation or {}), allow_nan=False),
                    completion_source if state == "SUCCEEDED" else "",
                    completion_pose_stamp if state == "SUCCEEDED" else 0,
                    goal_id,
                ),
            )
            self.db.commit()
            if state in TERMINAL and self.active_id == goal_id:
                self.active_id = None
                self.observation_loss_since = None

    def status(self, goal_id):
        with self.lock:
            row = self.db.execute(
                "SELECT command,state,message,failure_observation,completion_source,completion_pose_stamp FROM goals WHERE id=?", (goal_id,)
            ).fetchone()
            if not row:
                raise ApiError(404, "NAVIGATION_GOAL_NOT_FOUND")
            if self.active_id == goal_id:
                self.last_poll = time.monotonic()
            world = self.driver.map_status()
            holding = (self.observation_hold(goal_id, world)
                       if row[1] in {"PENDING", "RUNNING"} else False)
            if (row[1] in {"PENDING", "RUNNING"} and not holding
                    and (not world["ready"] or self.observation_hold_expired())):
                self.update(goal_id, "FAILED", "NAVIGATION_OBSERVATION_LOST", observation=world)
                self.driver.cancel(goal_id)
                row = (
                    row[0],
                    "FAILED",
                    "NAVIGATION_OBSERVATION_LOST",
                    json.dumps(failure_observation(world), allow_nan=False),
                    "",
                    0,
                )
            velocity = (
                self.driver.velocity()
                if row[1] == "RUNNING"
                and not holding
                and world.get("actuationMode", "native_http") == "native_http"
                else {
                    "linearX": 0.0,
                    "linearY": 0.0,
                    "angularZ": 0.0,
                    "stampUnixMs": int(time.time() * 1000),
                }
            )
            if (
                row[1] == "RUNNING"
                and not holding
                and world.get("actuationMode", "native_http") == "native_http"
                and not 0 <= int(time.time() * 1000) - velocity["stampUnixMs"] <= 250
            ):
                self.update(goal_id, "FAILED", "NAV2_VELOCITY_STALE", observation=world)
                self.driver.cancel(goal_id)
                row = (row[0], "FAILED", "NAV2_VELOCITY_STALE",
                       json.dumps(failure_observation(world), allow_nan=False), "", 0)
                velocity = {**velocity, "linearX": 0.0, "linearY": 0.0, "angularZ": 0.0}
            return {
                "goalId": goal_id,
                "commandId": row[0],
                "state": row[1],
                "message": row[2],
                "failureObservation": json.loads(row[3]),
                "completionSource": row[4],
                "completionPoseObservedAtUnixMs": row[5],
                "latestCmdVel": velocity,
                "velocityValid": world.get("actuationMode", "native_http") == "native_http"
                and row[1] == "RUNNING"
                and not holding
                and 0 <= int(time.time() * 1000) - velocity["stampUnixMs"] <= 250,
                "stopReason": "OBSERVATION_HOLD" if holding else ""
                if row[1] == "RUNNING"
                and 0 <= int(time.time() * 1000) - velocity["stampUnixMs"] <= 250
                else row[2]
                or ("NAV2_VELOCITY_STALE" if row[1] == "RUNNING" else "WAITING_FOR_NAV2_VELOCITY"),
                "actuationMode": world.get("actuationMode", "native_http"),
                "mapPose": world.get("mapPose"),
                "mapRevision": world.get("mapRevision"),
                "localizationState": world.get("localizationState"),
                "mapReady": world["ready"],
                "observationHold": holding,
                "observationHolds": list(self.holds.get(goal_id, [])),
                "robotId": world.get("robotId"),
                "poseSource": world.get("poseSource"),
                "poseObservedAtUnixMs": world.get("poseObservedAtUnixMs"),
                "goalPoseMap": self.driver.goal_pose_map(goal_id),
            }

    def cancel(self, goal_id, *, failure=None):
        with self.lock:
            current = self.status(goal_id)
            if current["state"] not in TERMINAL:
                self.update(
                    goal_id, "FAILED" if failure else "CANCELLED", failure or "CANCEL_REQUESTED"
                )
                self.driver.cancel(goal_id)
            return self.status(goal_id)

    def watchdog(self):
        with self.lock:
            if self.active_id and time.monotonic() - self.last_poll > 2:
                self.cancel(self.active_id, failure="CLIENT_LEASE_EXPIRED")
            elif self.active_id:
                world = self.driver.map_status()
                holding = self.observation_hold(self.active_id, world)
                if not holding and (not world["ready"] or self.observation_hold_expired()):
                    goal_id = self.active_id
                    self.update(goal_id, "FAILED", "NAVIGATION_OBSERVATION_LOST", observation=world)
                    self.driver.cancel(goal_id)
                    return
                if holding:
                    return
                row = self.db.execute(
                    "SELECT state FROM goals WHERE id=?", (self.active_id,)
                ).fetchone()
                if (
                    row
                    and row[0] == "RUNNING"
                    and world.get("actuationMode", "native_http") == "native_http"
                ):
                    stamp = self.driver.velocity()["stampUnixMs"]
                    if not 0 <= int(time.time() * 1000) - stamp <= 250:
                        self.cancel(self.active_id, failure="NAV2_VELOCITY_STALE")

    def close(self):
        with self.lock:
            if self.active_id:
                self.cancel(self.active_id, failure="NAVIGATION_SERVICE_STOPPED")
            self.db.close()


def create_http_server(host, port, token, registry):
    if not isinstance(token, str) or len(token) < 24 or any(c.isspace() for c in token):
        raise ValueError("navigation token must contain at least 24 non-whitespace characters")

    class Handler(BaseHTTPRequestHandler):
        server_version = "TangyingNavigation"

        def log_message(self, *_):
            pass  # Request paths and credentials never enter HTTP logs.

        def reply(self, status, body):
            data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def dispatch(self, post=False):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.reply(401, {"code": "UNAUTHORIZED"})
                return
            try:
                if not post and self.path in {
                    "/v1/navigation/map",
                    "/v1/navigation/map?includeGrid=1",
                }:
                    self.reply(
                        200,
                        registry.driver.map_status(include_grid=True)
                        if "?" in self.path
                        else registry.driver.map_status(),
                    )
                    return
                if post and self.path == "/v1/navigation/goals":
                    if (
                        self.headers.get("Transfer-Encoding")
                        or self.headers.get_content_type() != "application/json"
                    ):
                        raise ApiError(400, "INVALID_REQUEST_BODY")
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        raise ApiError(400, "INVALID_REQUEST_BODY")
                    if not 0 < size <= 8192:
                        raise ApiError(400, "INVALID_REQUEST_BODY")
                    try:
                        data = json.loads(
                            self.rfile.read(size),
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                        )
                    except (ValueError, UnicodeError):
                        raise ApiError(400, "INVALID_REQUEST_BODY")
                    self.reply(202, registry.submit(data))
                    return
                match = re.fullmatch(r"/v1/navigation/goals/([a-f0-9]{64})(/cancel)?", self.path)
                if match and post == bool(match[2]):
                    self.reply(
                        200, registry.cancel(match[1]) if post else registry.status(match[1])
                    )
                    return
                raise ApiError(404, "NOT_FOUND")
            except ApiError as error:
                self.reply(error.status, {"code": error.code})
            except Exception:  # noqa: BLE001 — adapter errors must fail closed without leaking transport details.
                self.reply(503, {"code": "NAVIGATION_UNAVAILABLE"})

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch(True)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
