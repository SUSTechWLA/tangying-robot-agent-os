"""RTAB-Map/Nav2 command adapter for the native simulation runtime.

Only Nav2's fresh, fenced velocity requests reach the bounded base controller.
This client neither constructs a map from simulator state nor retries movement
after an ambiguous transport outcome. ROS consumes the same raw RGB-D contract
that a hardware adapter can implement.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from threading import Event, Thread

from .tools import ToolResult


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("navigation bridge redirects are forbidden")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _pose(value):
    if not isinstance(value, list) or len(value) != 7 or not all(map(_finite, value)):
        raise ValueError("navigation pose must contain seven finite numbers")
    if abs(sum(v*v for v in value[3:]) - 1) > .001:
        raise ValueError("navigation pose quaternion must be normalized")
    return value


def _fresh(stamp, max_age_ms):
    age = int(time.time()*1000) - stamp if type(stamp) is int else math.inf
    return 0 <= age <= max_age_ms


def _localized(state):
    try:
        _pose(state.get("mapPose"))
    except ValueError:
        return False
    return (state.get("poseSource") == "rtabmap_tf"
            and isinstance(state.get("mapRevision"), str) and bool(state["mapRevision"])
            and _fresh(state.get("poseObservedAtUnixMs"), 1000))


class RTABMapClient:
    def __init__(self, endpoint: str, token: str, *, robot_id: str | None = None):
        parsed = urllib.parse.urlsplit(endpoint)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("navigation URL must be a configured HTTP(S) origin")
        if not token or any(char.isspace() for char in token):
            raise ValueError("navigation bridge requires a nonempty bearer token")
        self.endpoint, self._token = endpoint.rstrip("/"), token
        self.robot_id = robot_id
        self._http = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _request(self, method, path, payload=None, *, timeout=None):
        request = urllib.request.Request(
            self.endpoint + path,
            data=None if payload is None else json.dumps(payload, allow_nan=False).encode(),
            headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"},
            method=method,
        )
        # The controller runs a finite pulse then stops; a slow HTTP response
        # cannot leave a stale motor command latched.
        if timeout is None:
            timeout = 2.0 if method == "POST" else .5
        with self._http.open(request, timeout=timeout) as reply:
            raw = reply.read(65_537)
            if len(raw) > 65_536:
                raise ValueError("navigation bridge response exceeds size limit")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TypeError("navigation bridge must return a JSON object")
            return result

    def status(self, *, timeout=.5):
        """Read once; distinguish transient readiness from a foreign/invalid bridge."""
        state = {}
        try:
            state = self._request("GET", "/v1/navigation/map", timeout=timeout)
            if not isinstance(state, dict):
                raise TypeError("navigation status must be an object")
            if self.robot_id is not None and state.get("robotId") != self.robot_id:
                code, retryable = "NAV_STATUS_ROBOT_MISMATCH", False
            elif state.get("actuationMode") != "native_http":
                code, retryable = "NAV_STATUS_ACTUATION_MISMATCH", False
            elif type(state.get("ready")) is not bool:
                code, retryable = "NAV_STATUS_INVALID", False
            elif not state["ready"]:
                code, retryable = "NAV_MAP_NOT_READY", True
            elif not _localized(state):
                code, retryable = "NAV_LOCALIZATION_UNAVAILABLE", True
            else:
                code, retryable = "NAV_READY", False
        except urllib.error.HTTPError as exc:
            state = {"httpStatus": exc.code}
            code, retryable = "NAV_STATUS_HTTP_ERROR", exc.code in (408, 429, 500, 502, 503, 504)
            exc.close()
        except OSError as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            code = "NAV_STATUS_TIMEOUT" if isinstance(reason, TimeoutError) else "NAV_STATUS_UNAVAILABLE"
            retryable = True
        except (TypeError, ValueError):
            state = {}
            code, retryable = "NAV_STATUS_INVALID", False
        # Transport errors may include URL/userinfo/headers. Never copy their
        # exception strings into history or an externally visible status.
        return {**state, "backend": "rtabmap_nav2", "ready": code == "NAV_READY",
                "statusCode": code, "statusRetryable": retryable,
                "clientCheckedAtUnixMs": int(time.time()*1000),
                "message": "navigation readiness: " + code}

    def _readiness_metadata(self, state):
        """Small, credential-free snapshot; no image/grid or arbitrary server fields."""
        metadata = {}
        for key in ("backend", "statusCode", "robotId", "actuationMode", "mode", "frameId",
                    "mapRevision", "poseSource", "localizationState"):
            value = state.get(key)
            if isinstance(value, str):
                metadata[key] = value.replace(self._token, "[redacted]")[:256]
        for key in ("ready", "statusRetryable"):
            if type(state.get(key)) is bool:
                metadata[key] = state[key]
        for key in ("checkedAtUnixMs", "poseObservedAtUnixMs", "odomObservedAtUnixMs",
                    "clientCheckedAtUnixMs", "knownCells", "httpStatus"):
            if type(state.get(key)) is int:
                metadata[key] = state[key]
        for key, allowed in (
            ("inputAgeMs", ("rtabmap", "mapPose", "odometry", "base", "head")),
            ("sensorObservedAtUnixMs", ("base", "head")),
            ("visualQuality", ("currentFrameWords", "dictionaryWords", "ready")),
        ):
            values = state.get(key)
            if isinstance(values, dict):
                metadata[key] = {name: values[name] for name in allowed
                                 if type(values.get(name)) is bool or _finite(values.get(name))}
        blockers = state.get("readinessBlockers")
        if isinstance(blockers, list):
            metadata["readinessBlockers"] = [value.replace(self._token, "[redacted]")[:128]
                                              for value in blockers[:32] if isinstance(value, str)]
        return metadata

    def _await_ready(self, deadline_unix_ms, cancel_event):
        started = time.monotonic()
        end = started + 3.0
        details = {"attempts": 0, "last_status": {"ready": False, "statusCode": "NAV_STATUS_TIMEOUT"}}

        def finish(code):
            details["waited_ms"] = round((time.monotonic()-started)*1000)
            return ToolResult(code == "NAV_READY", code, "navigation readiness: " + code,
                              1 if code == "NAV_READY" else 0, {"readiness": details})

        def interruption():
            if cancel_event.is_set():
                return "CANCELLED"
            if int(time.time()*1000) >= deadline_unix_ms:
                return "NAV_DEADLINE_EXCEEDED"
            if time.monotonic() >= end:
                last_code = details["last_status"]["statusCode"]
                return "NAV_STATUS_TIMEOUT" if last_code == "NAV_READY" else last_code
            return None

        while True:
            if code := interruption():
                return finish(code)
            remaining = min(end-time.monotonic(), (deadline_unix_ms-int(time.time()*1000))/1000)
            done, replies = Event(), []
            # Only this read-only GET runs off-thread. Cancellation/deadline
            # need not wait for a slow server/body. Late responses have no
            # authority to dispatch a goal; at most one read is in flight.
            request_timeout = min(.5, remaining)
            def read_status(done=done, replies=replies, timeout=request_timeout):
                replies.append(self.status(timeout=timeout))
                done.set()

            details["attempts"] += 1
            Thread(target=read_status, daemon=True, name="navigation-readiness").start()
            while not done.is_set():
                if code := interruption():
                    return finish(code)
                cancel_event.wait(min(.01, max(0, end-time.monotonic()),
                                      max(0, (deadline_unix_ms-int(time.time()*1000))/1000)))
            state = replies[0]
            details["last_status"] = self._readiness_metadata(state)
            if not state["ready"] and "actuationMode" in state:
                details["last_not_ready"] = details["last_status"]
            if code := interruption():
                return finish(code)
            if state["ready"] or not state["statusRetryable"]:
                return finish(state["statusCode"])
            cancel_event.wait(min(.1, max(0, end-time.monotonic()),
                                  max(0, (deadline_unix_ms-int(time.time()*1000))/1000)))

    def navigate(self, command_id, goal_pose, deadline_unix_ms, cancel_event,
                 apply_velocity, stop):
        readiness = {}
        result = self._navigate(command_id, goal_pose, deadline_unix_ms, cancel_event,
                                apply_velocity, stop, readiness)
        if readiness:
            return ToolResult(result.success, result.code, result.message, result.confidence,
                              {**result.payload, "readiness": readiness})
        return result

    def _navigate(self, command_id, goal_pose, deadline_unix_ms, cancel_event,
                  apply_velocity, stop, readiness):
        goal_id = None
        completed = False
        stale_since = None
        result = ToolResult(False, "NAV_BRIDGE_UNAVAILABLE", confidence=0)
        try:
            _pose(goal_pose)
            if not isinstance(command_id, str) or not command_id:
                raise ValueError("navigation requires a command identity")
            if type(deadline_unix_ms) is not int:
                raise ValueError("navigation deadline must be Unix milliseconds")
            if cancel_event.is_set():
                return ToolResult(False, "CANCELLED", "navigation cancelled before dispatch", 0)
            if int(time.time()*1000) >= deadline_unix_ms:
                return ToolResult(False, "NAV_DEADLINE_EXCEEDED", "navigation deadline elapsed before dispatch", 0)
            stop()
            ready = self._await_ready(deadline_unix_ms, cancel_event)
            readiness.update(ready.payload["readiness"])
            if not ready.success:
                return ready
            if cancel_event.is_set():
                return ToolResult(False, "CANCELLED", "navigation cancelled before goal dispatch", 0)
            if int(time.time()*1000) >= deadline_unix_ms:
                return ToolResult(False, "NAV_DEADLINE_EXCEEDED", "navigation deadline elapsed during readiness check", 0)
            start = self._request("POST", "/v1/navigation/goals", {
                "commandId": command_id, "goalPose": goal_pose, "frameId": "odom",
            })
            proposed_id = start.get("goalId")
            if not isinstance(proposed_id, str) or not proposed_id or len(proposed_id) > 128:
                raise ValueError("bridge did not return a valid navigation goal ID")
            # Adopt only a receipt explicitly belonging to this command. A
            # mismatched POST reply is not authority to cancel another goal.
            self._validate_identity(start, command_id, proposed_id)
            goal_id = proposed_id
            path = "/v1/navigation/goals/" + urllib.parse.quote(goal_id, safe="")
            while True:
                if cancel_event.is_set():
                    return ToolResult(False, "CANCELLED", "navigation cancelled; stopped in place", 0)
                if int(time.time()*1000) >= deadline_unix_ms:
                    return ToolResult(False, "NAV_DEADLINE_EXCEEDED", "navigation deadline elapsed", 0)
                state = self._request("GET", path)
                self._validate_identity(state, command_id, goal_id)
                # Network time belongs to the same command lease; a response
                # arriving after cancellation/deadline grants no new pulse or
                # successful terminal result.
                if cancel_event.is_set():
                    return ToolResult(False, "CANCELLED", "navigation cancelled while awaiting receipt", 0)
                if int(time.time()*1000) >= deadline_unix_ms:
                    return ToolResult(False, "NAV_DEADLINE_EXCEEDED", "navigation receipt arrived after deadline", 0)
                phase = state.get("state")
                if phase == "SUCCEEDED":
                    result = self._verify_goal(state)
                    completed = result.success
                    return result
                if phase in ("FAILED", "CANCELLED"):
                    completed = True
                    failure = state.get("failureObservation")
                    # The bridge freezes this at the stop decision. Live map
                    # readiness may have recovered before this terminal poll;
                    # it is not evidence of what failed and must not backfill it.
                    failure = self._readiness_metadata(failure) if isinstance(failure, dict) else {}
                    return ToolResult(False, "NAV_" + phase, str(state.get("message", "")), 0,
                                      {"goal_id": goal_id, "map_revision": state.get("mapRevision", ""),
                                       "failure_observation": failure})
                if phase == "PENDING":
                    stop()
                    cancel_event.wait(.05)
                    continue
                if phase != "RUNNING":
                    raise ValueError("unknown navigation lifecycle state")
                if state.get("mapReady") is not True or not _localized(state):
                    return ToolResult(False, "NAV_LOCALIZATION_UNAVAILABLE", "live map localization no longer authorizes movement", 0)
                velocity = state.get("latestCmdVel")
                if velocity is None:
                    # Nav2 may still be computing its first path. No command is
                    # permission to move; retain zero until a fresh one arrives.
                    stop()
                    cancel_event.wait(.05)
                    continue
                if state.get("velocityValid") is not True or not isinstance(velocity, dict):
                    return ToolResult(False, "NAV_COMMAND_STALE", "Nav2 rejected velocity authorization", 0)
                if not _fresh(velocity.get("stampUnixMs"), 200):
                    # A delayed GET grants no motion. Stop and read the next
                    # command, without replaying this expired velocity or POST.
                    # This bounded wait preserves the 250 ms motor lease.
                    stop()
                    stale_since = time.monotonic() if stale_since is None else stale_since
                    if time.monotonic() - stale_since >= .5:
                        return ToolResult(False, "NAV_COMMAND_STALE", "Nav2 velocity lease expired", 0)
                    cancel_event.wait(.01)
                    continue
                values = [velocity.get(key) for key in ("linearX", "linearY", "angularZ")]
                if not all(map(_finite, values)) or math.hypot(*values[:2]) > .05 + 1e-9 or abs(values[2]) > .2 + 1e-9:
                    raise ValueError("Nav2 velocity exceeds commissioned controller limits")
                if cancel_event.is_set():
                    return ToolResult(False, "CANCELLED", "navigation cancelled before motor pulse", 0)
                age = (int(time.time()*1000) - velocity["stampUnixMs"]) / 1000
                if int(time.time()*1000) + 50 >= deadline_unix_ms:
                    return ToolResult(False, "NAV_DEADLINE_EXCEEDED", "remaining lease cannot cover a motor pulse", 0)
                pulse = apply_velocity(*values, .05, cancel_event=cancel_event, command_age_s=age)
                if not pulse.success:
                    if pulse.code == "NAV_VELOCITY_STALE":
                        # This controller result guarantees no position update
                        # was applied. Reacquire a fresh command while stopped.
                        stop()
                        stale_since = time.monotonic() if stale_since is None else stale_since
                        if time.monotonic() - stale_since < .5:
                            continue
                    return pulse
                stale_since = None
                # The real controller consumes this interval. Test controllers
                # may return immediately; avoid an unbounded HTTP polling loop.
                cancel_event.wait(.005)
        except (OSError, TypeError, ValueError) as exc:
            code = "NAV_BRIDGE_UNAVAILABLE" if isinstance(exc, OSError) else "NAV_RECEIPT_INVALID"
            return ToolResult(False, code, str(exc), 0)
        finally:
            stop()
            if goal_id and not completed:
                try:
                    self._request("POST", "/v1/navigation/goals/" + urllib.parse.quote(goal_id, safe="") + "/cancel", {})
                except (OSError, TypeError, ValueError):
                    pass  # Motor output is already zero; never re-dispatch.

    def _validate_identity(self, state, command_id, goal_id):
        if state.get("goalId") != goal_id or state.get("commandId") != command_id:
            raise ValueError("navigation receipt belongs to a different command")
        if state.get("actuationMode") != "native_http":
            raise ValueError("navigation bridge is assigned to a different motor execution channel")
        if self.robot_id is not None and state.get("robotId") != self.robot_id:
            raise ValueError("navigation receipt belongs to a different robot")

    @staticmethod
    def _verify_goal(state):
        source = state.get("completionSource")
        if source not in {"nav2_action", "pose_confirmation"}:
            raise ValueError("navigation success lacks a recognized completion source")
        completion_stamp = state.get("completionPoseObservedAtUnixMs")
        if not _fresh(completion_stamp, 1000):
            raise ValueError("navigation completion lacks a fresh original localization timestamp")
        actual, goal = _pose(state.get("mapPose")), _pose(state.get("goalPoseMap"))
        if state.get("mapReady") is not True or not _localized(state):
            raise ValueError("navigation success lacks a fresh RTAB-Map localization receipt")
        distance = math.hypot(actual[0]-goal[0], actual[1]-goal[1])
        def yaw(p):
            w, x, y, z = p[3:]
            return math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))
        delta = yaw(actual)-yaw(goal)
        angle = abs(math.atan2(math.sin(delta), math.cos(delta)))
        details = {"goal_id": state["goalId"], "map_revision": state["mapRevision"],
                   "map_pose": actual, "goal_pose_map": goal, "position_error_m": distance,
                   "yaw_error_rad": angle, "pose_source": "rtabmap_tf",
                   "pose_observed_at_unix_ms": state["poseObservedAtUnixMs"],
                   "completion_source": source,
                   "completion_pose_observed_at_unix_ms": completion_stamp,
                   "checked_at_unix_ms": int(time.time()*1000)}
        if distance > .015 or angle > .04:
            return ToolResult(False, "NAV_GOAL_NOT_REACHED", "localized pose is outside goal tolerance", 0, details)
        message = ("fresh localized pose already matches the goal; no motion requested"
                   if source == "pose_confirmation"
                   else "Nav2 completed and localized pose matches the goal")
        return ToolResult(True, "NAV_GOAL_REACHED", message, 1, details)
