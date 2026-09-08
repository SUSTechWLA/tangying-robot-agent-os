"""Authenticated local navigation boundary. It never publishes motion itself."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contracts import ContractError, validate_goal

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


class ApiError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


class GoalRegistry:
    def __init__(self, driver, database):
        self.driver = driver
        self.lock = threading.RLock()
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS goals (id TEXT PRIMARY KEY, command TEXT UNIQUE NOT NULL, request TEXT NOT NULL, state TEXT NOT NULL, message TEXT NOT NULL)"
        )
        self.db.execute(
            "UPDATE goals SET state='FAILED', message='NAVIGATION_SERVICE_RESTARTED' WHERE state NOT IN ('SUCCEEDED','FAILED','CANCELLED')"
        )
        self.db.commit()
        self.active_id = None
        self.last_poll = 0.0

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
                "INSERT INTO goals VALUES (?,?,?,?,?)",
                (goal_id, goal["commandId"], encoded, "PENDING", ""),
            )
            self.db.commit()
            self.active_id = goal_id
            self.last_poll = time.monotonic()
            try:
                self.driver.start(goal_id, goal)
            except Exception:  # noqa: BLE001 — adapter errors must fail closed without leaking transport details.
                self.update(goal_id, "FAILED", "NAV2_GOAL_SUBMISSION_FAILED")
            return self.status(goal_id)

    def update(self, goal_id, state, message=""):
        if state not in TERMINAL | {"RUNNING", "PENDING"}:
            raise ValueError("invalid goal state")
        with self.lock:
            row = self.db.execute("SELECT state FROM goals WHERE id=?", (goal_id,)).fetchone()
            if not row or row[0] in TERMINAL or row[0] == state:
                return
            self.db.execute(
                "UPDATE goals SET state=?,message=? WHERE id=?", (state, message, goal_id)
            )
            self.db.commit()
            if state in TERMINAL and self.active_id == goal_id:
                self.active_id = None

    def status(self, goal_id):
        with self.lock:
            row = self.db.execute(
                "SELECT command,state,message FROM goals WHERE id=?", (goal_id,)
            ).fetchone()
            if not row:
                raise ApiError(404, "NAVIGATION_GOAL_NOT_FOUND")
            if self.active_id == goal_id:
                self.last_poll = time.monotonic()
            world = self.driver.map_status()
            if row[1] in {"PENDING", "RUNNING"} and not world["ready"]:
                self.update(goal_id, "FAILED", "NAVIGATION_OBSERVATION_LOST")
                self.driver.cancel(goal_id)
                row = (row[0], "FAILED", "NAVIGATION_OBSERVATION_LOST")
            velocity = (
                self.driver.velocity()
                if row[1] == "RUNNING"
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
                and world.get("actuationMode", "native_http") == "native_http"
                and not 0 <= int(time.time() * 1000) - velocity["stampUnixMs"] <= 250
            ):
                self.update(goal_id, "FAILED", "NAV2_VELOCITY_STALE")
                self.driver.cancel(goal_id)
                row = (row[0], "FAILED", "NAV2_VELOCITY_STALE")
                velocity = {**velocity, "linearX": 0.0, "linearY": 0.0, "angularZ": 0.0}
            return {
                "goalId": goal_id,
                "commandId": row[0],
                "state": row[1],
                "message": row[2],
                "latestCmdVel": velocity,
                "velocityValid": world.get("actuationMode", "native_http") == "native_http"
                and row[1] == "RUNNING"
                and 0 <= int(time.time() * 1000) - velocity["stampUnixMs"] <= 250,
                "stopReason": ""
                if row[1] == "RUNNING"
                and 0 <= int(time.time() * 1000) - velocity["stampUnixMs"] <= 250
                else row[2]
                or ("NAV2_VELOCITY_STALE" if row[1] == "RUNNING" else "WAITING_FOR_NAV2_VELOCITY"),
                "actuationMode": world.get("actuationMode", "native_http"),
                "mapPose": world.get("mapPose"),
                "mapRevision": world.get("mapRevision"),
                "localizationState": world.get("localizationState"),
                "mapReady": world["ready"],
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
            elif self.active_id and not self.driver.map_status()["ready"]:
                self.cancel(self.active_id, failure="NAVIGATION_OBSERVATION_LOST")
            elif self.active_id:
                row = self.db.execute(
                    "SELECT state FROM goals WHERE id=?", (self.active_id,)
                ).fetchone()
                if (
                    row
                    and row[0] == "RUNNING"
                    and self.driver.map_status().get("actuationMode", "native_http")
                    == "native_http"
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
