"""Profiled ROS 2 driver backed exclusively by measured Gazebo hub state.

Ground-truth link localization is declared as a simulation-state source. This
adapter never assigns a pose, integrates a pose in Python, or synthesizes lidar.
Hub-side command leases remain authoritative if this ROS process disappears.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tangying_robot_gateway.contracts import RobotProfile, validate_pose, validate_tool_parameters
from tangying_robot_gateway.ros2_profile import navigation_state, profile_revision


def finite_vector(value, length):
    if (type(value) not in (list, tuple) or len(value) != length
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)):
        raise ValueError(f"expected {length} finite measured coordinates")
    return list(value)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("simulation hub redirects are forbidden")


class HubClient:
    def __init__(self, url, timeout=.2):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or not parsed.port or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("simulation hub must be an explicit loopback HTTP port")
        if type(timeout) not in (float, int) or not 0 < timeout <= .5:
            raise ValueError("bounded hub timeout required")
        self.url, self.timeout = url.rstrip("/"), timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def request(self, route, payload=None):
        data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
        request = urllib.request.Request(self.url + route, data=data,
                                         headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            body = response.read(8 * 1024 * 1024 + 1)
        if len(body) > 8 * 1024 * 1024:
            raise ValueError("hub response exceeds byte budget")
        result = json.loads(body)
        if type(result) is not dict:
            raise ValueError("hub response must be an object")
        if payload is not None and result.get("ok", result.get("accepted")) is not True:
            raise ValueError("hub did not acknowledge command")
        return result


class MeasuredSnapshot:
    """Reject replay/regression without restamping unchanged simulation samples."""

    def __init__(self, robot_id):
        self.robot_id = robot_id
        self.sample = None

    def accept(self, snapshot, *, now_ms, max_age_ms):
        if type(snapshot) is not dict or type(snapshot.get("states")) is not dict:
            raise ValueError("snapshot has no measured states")
        state = snapshot["states"].get(self.robot_id)
        if type(state) is not dict:
            raise ValueError("snapshot lacks requested robot")
        sequence, captured = snapshot.get("sequence"), snapshot.get("capturedAtUnixMs")
        sim_time = state.get("simulationTime", snapshot.get("simulationTime"))
        if (type(sequence) is not int or not 0 < sequence <= 9_007_199_254_740_991
                or type(captured) is not int or captured <= 0
                or not -250 <= now_ms - captured <= max_age_ms
                or type(sim_time) not in (float, int) or not math.isfinite(sim_time) or sim_time < 0):
            raise ValueError("snapshot clock/sequence is invalid or stale")
        position = finite_vector(state.get("position"), 3)
        quaternion = state.get("orientationWxyz", state.get("quaternionWXYZ"))
        if quaternion is None:
            yaw = state.get("yaw")
            if type(yaw) not in (float, int) or not math.isfinite(yaw):
                raise ValueError("measured yaw or quaternion required")
            quaternion = [math.cos(yaw / 2), 0., 0., math.sin(yaw / 2)]
        pose = validate_pose(position + finite_vector(quaternion, 4))
        velocity = finite_vector(state.get("linearVelocity"), 3)
        sample = {"sequence": sequence, "capturedAtUnixMs": captured,
                  "simulationTime": sim_time, "pose": pose, "velocity": velocity}
        if self.sample:
            old = self.sample
            if (sequence < old["sequence"] or sim_time < old["simulationTime"]
                    or captured < old["capturedAtUnixMs"]):
                raise ValueError("snapshot regressed")
            if sequence == old["sequence"]:
                if sample != old:
                    raise ValueError("same snapshot identity changed its measurement")
                return old, False
            if sim_time <= old["simulationTime"]:
                raise ValueError("new source sequence requires advancing simulation time")
        self.sample = sample
        return sample, True


def load_goals(document, profile):
    if type(document) is not dict or type(document.get("goals")) is not dict or not document["goals"]:
        raise ValueError("named measured-world goals required")
    if len(document["goals"]) > 128:
        raise ValueError("too many named goals")
    for name, pose in document["goals"].items():
        if type(name) is not str or not name:
            raise ValueError("goal name required")
        validate_tool_parameters("navigation.navigate", {"goalPose": pose}, profile)
    if any(type(document.get(key)) is not str or not document[key] for key in ("mapId", "mapRevision")):
        raise ValueError("named goals must identify their map")
    tolerance = document.get("arrivalToleranceM", .12)
    if type(tolerance) not in (float, int) or not .01 <= tolerance <= .25:
        raise ValueError("arrival tolerance must be 0.01..0.25 metres")
    aliases = document.get("aliases", {})
    edges = document.get("routeEdges", {name: [] for name in document["goals"]})
    if type(aliases) is not dict or type(edges) is not dict or set(edges) != set(document["goals"]):
        raise ValueError("aliases must be an object and route nodes must match all named goals")
    for alias, target in aliases.items():
        if type(alias) is not str or not alias or target not in document["goals"]:
            raise ValueError("alias references an unknown goal")
    for source, destinations in edges.items():
        if (source not in document["goals"] or type(destinations) is not list
                or any(target not in document["goals"] for target in destinations)):
            raise ValueError("route references an unknown goal")
    result = json.loads(json.dumps({**document, "aliases": aliases, "routeEdges": edges}, allow_nan=False))
    # Exercise the same admission contract as the consuming ROS gateway.
    navigation_state_for_sample(profile, result, {"pose": next(iter(result["goals"].values()))})
    return result


def navigation_state_for_sample(profile, goals, sample):
    active = {"mapId": goals["mapId"], "mapRevision": goals["mapRevision"],
              "calibrationRevision": profile.sensors[0].transform_revision}
    value = {"base_pose": sample["pose"], "base_pose_frame": "world", "active_map": active,
             "semantic_navigation": {"schemaVersion": "semantic.navigation.v1", "frameId": "world",
                 "robotId": profile.robot_id, **active, "goals": goals["goals"],
                 "aliases": goals["aliases"], "routeEdges": goals["routeEdges"]}}
    return navigation_state(json.dumps(value, allow_nan=False), profile)


def controller_class():
    # Keep the protocol/measurement helpers importable for non-ROS unit tests.
    from rclpy.action import ActionServer, CancelResponse, GoalResponse
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.node import Node
    from tangying_robot_msgs.action import ProfiledExecute
    from tangying_robot_msgs.msg import CommandLease, ProfiledDriverState, ProfiledObservation
    from tangying_robot_msgs.srv import EnterSafeStop

    class GazeboController(Node):
        def __init__(self, profile, commissioning_id, *, robot_id, hub_url, goals,
                     namespace="", journal_path=None):
            super().__init__("gazebo_field_profiled_controller", namespace=namespace)
            self.profile = RobotProfile.model_validate(profile)
            if (len(self.profile.sensors) != 1 or self.profile.sensors[0].source_type != "sensor_fusion"
                    or not any(marker in self.profile.sensors[0].source_id for marker in ("sim-state", "gazebo-state"))):
                raise ValueError("declare exactly one sim-state sensor_fusion localization source")
            self.revision = profile_revision(self.profile)
            self.commissioning_id, self.robot_id = commissioning_id, robot_id
            self.hub, self.goals = HubClient(hub_url), load_goals(goals, self.profile)
            self.journal_path = journal_path
            if journal_path:
                Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
            self._lock = threading.RLock()
            self._sample_lock = threading.Lock()
            self._measured = MeasuredSnapshot(robot_id)
            self._stopped, self._active, self._dispatched = False, None, False
            self._leases, self._executed, self._accepted_ids = {}, set(), set()
            self._lease_ack_expires, self._lease_fault = 0., ""
            self._last_capture_attempt = -math.inf
            self._state_seq, self._observation = 0, None
            self._fault = "awaiting_measured_gazebo_state"
            group = ReentrantCallbackGroup()
            self._state = self.create_publisher(ProfiledDriverState, "profiled_driver_state", 1)
            self._obs = self.create_publisher(ProfiledObservation, "profiled_observation", 64)
            self.create_subscription(CommandLease, "profiled_command_lease", self._lease, 10, callback_group=group)
            self.create_service(EnterSafeStop, "enter_safe_stop", self._stop, callback_group=group)
            self._server = ActionServer(self, ProfiledExecute, "profiled_execute", self._execute,
                goal_callback=self._goal, cancel_callback=lambda goal: CancelResponse.ACCEPT, callback_group=group)
            self.create_timer(.1, self._publish, callback_group=group)
            self.record("DRIVER_STARTED", robotId=self.profile.robot_id, hubRobotId=robot_id,
                        profileRevision=self.revision, source="gazebo_ground_truth_link_localization",
                        poseAssignmentAllowed=False, syntheticSensorCaptures=False)

        def record(self, kind, **data):
            wire = json.dumps({"type": kind, "atUnixMs": int(time.time() * 1000), **data}, sort_keys=True,
                              allow_nan=False)
            with self._lock:
                print(wire, flush=True)
                if self.journal_path:
                    with Path(self.journal_path).open("a") as stream:
                        stream.write(wire + "\n")

        def _fresh(self):
            sample = self._measured.sample
            return sample is not None and 0 <= int(time.time() * 1000) - sample["capturedAtUnixMs"] <= self.profile.sensors[0].max_age_ms

        def _goal(self, goal):
            try:
                with self._lock:
                    if (self._stopped or self._active is not None or not self._fresh()
                            or goal.schema_version != "ros2.execution.v1"
                            or goal.robot_id != self.profile.robot_id or goal.adapter_id != self.profile.adapter_id
                            or goal.profile_revision != self.revision or goal.commissioning_id != self.commissioning_id
                            or not goal.command_id or not goal.idempotency_key or not goal.catalog_revision
                            or not goal.task_id or goal.command_id in self._accepted_ids
                            or goal.idempotency_key in self._executed
                            or goal.deadline_unix_ms <= int(time.time() * 1000) or goal.lease_ms <= 0
                            or goal.capability not in {"navigation.navigate", "verify_arrival"}
                            or (goal.capability == "navigation.navigate" and not goal.approval_id)
                            or goal.navigation_plan_json):
                        return GoalResponse.REJECT
                    validate_tool_parameters(goal.capability, json.loads(goal.parameters_json), self.profile,
                                             target_ref=goal.target_ref)
                    self._active, self._dispatched = goal, False
                    self._lease_ack_expires, self._lease_fault = 0., ""
                    self._accepted_ids.add(goal.command_id)
                    return GoalResponse.ACCEPT
            except (ValueError, KeyError, TypeError):
                return GoalResponse.REJECT

        def _hub_lease(self, goal):
            with self._lock:
                lease = self._leases.get(goal.command_id)
                if lease is None:
                    raise ValueError("no command-bound DDS lease")
                maximum = min(goal.lease_ms, self.profile.mobility.safe_stop.command_loss_timeout_ms, 500)
                remaining = maximum - (int(time.time() * 1000) - lease[1])
                if remaining <= 0:
                    raise ValueError("DDS lease expired before forwarding")
            return {"robotId": self.robot_id, "commandId": goal.command_id,
                    "leaseMs": remaining, "observedAtUnixMs": lease[1],
                    "deadlineUnixMs": goal.deadline_unix_ms}

        def _lease(self, message):
            now = int(time.time() * 1000)
            if (message.robot_id != self.profile.robot_id or message.profile_revision != self.revision
                    or not 0 <= now - message.observed_at_unix_ms <= message.lease_ms
                    or message.deadline_unix_ms <= now or message.lease_ms <= 0):
                return
            with self._lock:
                goal = self._active
                maximum_ms = min(message.lease_ms, self.profile.mobility.safe_stop.command_loss_timeout_ms, 500)
                if (goal is None or message.command_id != goal.command_id
                        or message.deadline_unix_ms != goal.deadline_unix_ms or message.lease_ms != goal.lease_ms
                        or self._stopped or not self._lease_channel_healthy()
                        or now - message.observed_at_unix_ms >= maximum_ms
                        or message.observed_at_unix_ms <= self._leases.get(message.command_id, (0, -1))[1]):
                    return
                self._leases[message.command_id] = (time.monotonic(), message.observed_at_unix_ms)
                dispatched = self._dispatched
            if dispatched:
                try:
                    payload = self._hub_lease(goal)
                    sent_at = time.monotonic()
                    response = self.hub.request("/lease", payload)
                    if response.get("commandId") != goal.command_id:
                        raise ValueError("hub lease command identity mismatch")
                    self._acknowledge_lease(goal, payload, sent_at)
                except (urllib.error.HTTPError, ValueError) as error:
                    with self._lock:
                        if self._active is goal:
                            self._lease_fault = self._lease_fault or "hub_lease_rejected"
                    self.record("LEASE_FORWARD_REJECTED", commandId=goal.command_id, error=str(error))
                except OSError as error:
                    # A renewal is command-bound and may have reached the hub.
                    # Preserve only the previously confirmed window; a later
                    # NEW DDS lease may renew it, never replay the motion goal.
                    self.record("LEASE_FORWARD_RETRY", commandId=goal.command_id, error=str(error),
                                confirmedRemainingMs=max(0., (self._lease_ack_expires-time.monotonic())*1000),
                                leaseExtended=False)
            self.record("LEASE", commandId=message.command_id, observedAtUnixMs=message.observed_at_unix_ms)

        def _lease_valid(self, goal):
            with self._lock:
                lease = self._leases.get(goal.command_id)
                maximum_ms = min(goal.lease_ms, self.profile.mobility.safe_stop.command_loss_timeout_ms, 500)
                return (not self._stopped and lease is not None
                        and self._lease_channel_healthy()
                        and (time.monotonic() - lease[0]) * 1000 < maximum_ms
                        and 0 <= int(time.time() * 1000) - lease[1] < maximum_ms
                        and int(time.time() * 1000) < goal.deadline_unix_ms)

        def _lease_channel_healthy(self):
            with self._lock:
                if self._dispatched and time.monotonic() >= self._lease_ack_expires:
                    self._lease_fault = self._lease_fault or "hub_confirmed_lease_expired"
                return not self._lease_fault

        def _acknowledge_lease(self, goal, payload, sent_at):
            with self._lock:
                if self._active is not goal:
                    return
                # Check the OLD confirmed window before applying a renewal.
                # Otherwise an ACK racing the timer could revive an expired
                # command simply because the timer has not run yet.
                if not self._lease_channel_healthy():
                    raise ValueError("a failed confirmed lease cannot be revived")
                expiry = sent_at + payload["leaseMs"] / 1000
                if expiry <= time.monotonic():
                    self._lease_fault = "hub_confirmed_lease_expired"
                    raise ValueError("lease acknowledgement arrived after its bounded window")
                self._lease_ack_expires = max(self._lease_ack_expires, expiry)

        def _navigation_state(self, sample):
            return navigation_state_for_sample(self.profile, self.goals, sample)

        def _capture(self):
            # Serialize HTTP requests with source admission so callback order
            # cannot turn two concurrent replies into a timestamp regression.
            with self._sample_lock:
                if (time.monotonic() - self._last_capture_attempt < .05
                        and self._fresh() and self._observation is not None):
                    return self._observation, self._measured.sample
                self._last_capture_attempt = time.monotonic()
                try:
                    snapshot = self.hub.request("/snapshot")
                except urllib.error.HTTPError:
                    # A negative hub response is not a transient transport gap.
                    raise
                except OSError as error:
                    if not self._fresh() or self._observation is None:
                        raise
                    sample = self._measured.sample
                    self.record("MEASUREMENT_READ_RETRY", error=str(error),
                                observationId=self._observation, sourceSequence=sample["sequence"],
                                capturedAtUnixMs=sample["capturedAtUnixMs"],
                                ageMs=int(time.time() * 1000) - sample["capturedAtUnixMs"],
                                reusedExistingCapture=True, freshnessExtended=False)
                    # Reuse only the original bounded measurement. Do not
                    # publish a new capture or advance its identity/timestamp.
                    return self._observation, sample
                sample, is_new = self._measured.accept(snapshot, now_ms=int(time.time() * 1000),
                                                      max_age_ms=self.profile.sensors[0].max_age_ms)
                if not is_new:
                    return self._observation, sample
                sensor = self.profile.sensors[0]
                observation_id = f"gazebo-{self.robot_id}-{sample['sequence']}"
                observed = {"schemaVersion": "scene.reconstruction.v1", "robotId": self.profile.robot_id,
                    "observationId": observation_id, "sourceId": sensor.source_id,
                    "sourceType": sensor.source_type, "sourceFrameId": sensor.frame_id,
                    "frameId": "world", "transformRevision": sensor.transform_revision,
                    "observedAtUnixMs": sample["capturedAtUnixMs"], "sequence": sample["sequence"], "units": "m",
                    "entities": [{"entityId": "robot-base", "category": "robot", "pose": sample["pose"],
                        "confidence": 1., "attributes": {"source": "gazebo_ground_truth_link_state",
                            "simulationTime": str(sample["simulationTime"]), "physicalHardware": "false"}}],
                    "points": []}
                state = self._navigation_state(sample)
                self._obs.publish(ProfiledObservation(robot_id=self.profile.robot_id, profile_revision=self.revision,
                    reconstruction_json=json.dumps(observed), robot_state_json=json.dumps(state)))
                with self._lock:
                    self._observation = observation_id
                    self._fault = ""
                self.record("MEASURED_OBSERVATION", observation=observed, robotState=state,
                            simulationTime=sample["simulationTime"], linearVelocity=sample["velocity"],
                            localizationSource="gazebo_ground_truth_link_state_proxy",
                            snapshotSha256=hashlib.sha256(json.dumps(snapshot, sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode()).hexdigest())
                return observation_id, sample

        def _publish(self):
            try:
                self._capture()
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._fault = "gazebo_measurement_unavailable"
                self.record("MEASUREMENT_FAILED", error=str(error), cachedMeasurementFresh=self._fresh(),
                            sourceSequence=self._measured.sample["sequence"] if self._measured.sample else None)
            with self._lock:
                self._state_seq += 1
                mobility, sample = self.profile.mobility, self._measured.sample
                lease_healthy = self._lease_channel_healthy()
                faults = [fault for fault in (self._fault, self._lease_fault) if fault]
                message = ProfiledDriverState(robot_id=self.profile.robot_id, profile_revision=self.revision,
                    commissioning_id=self.commissioning_id, sequence=self._state_seq,
                    observed_at_unix_ms=sample["capturedAtUnixMs"] if sample else int(time.time() * 1000),
                    commissioned=True, armed=not self._stopped,
                    healthy=self._fresh() and not self._fault and lease_healthy,
                    watchdog_active=True, stop_procedure_id=mobility.safe_stop.procedure_id,
                    command_loss_timeout_ms=min(mobility.safe_stop.command_loss_timeout_ms, 500),
                    max_linear_speed_mps=mobility.max_linear_speed_mps,
                    max_angular_speed_radps=mobility.max_angular_speed_radps,
                    supported_tools=[t for t in self.profile.tools if t not in {"observe_scene", "emergency_stop"}],
                    faults=faults)
                message.spatial_navigation_revision = ""
                self._state.publish(message)

        def _cancel_motion(self, goal, reason):
            response = self.hub.request("/cancel", {"robotId": self.robot_id, "commandId": goal.command_id})
            if response.get("commandId") != goal.command_id:
                raise ValueError("hub cancel command identity mismatch")
            self.record("CANCEL_ENTERED", commandId=goal.command_id, reason=reason, physicalStopVerified=False)

        def _execute(self, handle):
            goal = handle.request
            result = ProfiledExecute.Result(robot_id=self.profile.robot_id, profile_revision=self.revision,
                command_id=goal.command_id, success=False, code="CANCELLED", verification_confidence=0.)
            admitted_ms, admitted_sample = int(time.time() * 1000), self._measured.sample
            parameters = json.loads(goal.parameters_json)
            desired = parameters.get("goalPose", parameters.get("goal_pose"))
            self.record("GOAL", commandId=goal.command_id, capability=goal.capability, goalPose=desired,
                        catalogRevision=goal.catalog_revision, approvalId=goal.approval_id,
                        admissionSequence=admitted_sample["sequence"], admittedAtUnixMs=admitted_ms)
            dispatched = False
            try:
                lease_deadline = time.monotonic() + min(.4, goal.lease_ms / 1000)
                while not self._lease_valid(goal) and time.monotonic() < lease_deadline:
                    if handle.is_cancel_requested or self._stopped:
                        handle.canceled() if handle.is_cancel_requested else handle.abort()
                        return result
                    time.sleep(.005)
                if not self._lease_valid(goal):
                    result.code = "LEASE_EXPIRED_BEFORE_DISPATCH"
                    handle.abort()
                    return result
                if handle.is_cancel_requested or self._stopped:
                    handle.canceled() if handle.is_cancel_requested else handle.abort()
                    return result
                if goal.capability == "navigation.navigate":
                    with self._lock:
                        self._executed.add(goal.idempotency_key)
                    # Once this POST begins, a lost response is an uncertain action.
                    dispatched = True
                    payload = {**self._hub_lease(goal), "goalPose": desired}
                    sent_at = time.monotonic()
                    reply = self.hub.request("/goal", payload)
                    if reply.get("commandId") != goal.command_id:
                        raise ValueError("hub goal command identity mismatch")
                    self._acknowledge_lease(goal, payload, sent_at)
                    with self._lock:
                        self._dispatched = True
                    self.record("GAZEBO_GOAL_DISPATCHED", commandId=goal.command_id, goalPose=desired)
                while True:
                    measurement_expired = not self._fresh()
                    if handle.is_cancel_requested or self._stopped or not self._lease_valid(goal) or measurement_expired:
                        if dispatched:
                            self._cancel_motion(goal, "measurement_expired" if measurement_expired else "cancel_or_lease_or_stop")
                        result.code = ("CANCELLED" if handle.is_cancel_requested else
                                       "MEASUREMENT_EXPIRED" if measurement_expired else "LEASE_EXPIRED_OR_STOPPED")
                        handle.canceled() if handle.is_cancel_requested else handle.abort()
                        return result
                    if dispatched:
                        try:
                            status = self.hub.request("/status?" + urllib.parse.urlencode({
                                "robotId": self.robot_id, "commandId": goal.command_id}))
                        except urllib.error.HTTPError:
                            raise
                        except OSError as error:
                            # GET is safe to retry, unlike an ambiguous motion
                            # POST. No arrival can be inferred from a missing status.
                            self.record("STATUS_READ_RETRY", commandId=goal.command_id, error=str(error),
                                        arrivalVerified=False)
                            time.sleep(.05)
                            continue
                        if status.get("commandId") != goal.command_id:
                            raise ValueError("hub status command identity mismatch")
                        if status.get("blockedReason"):
                            raise ValueError("hub stopped the command: " + str(status["blockedReason"]))
                    else:
                        status = None
                    observation_id, sample = self._capture()
                    post_command = (sample["sequence"] > admitted_sample["sequence"]
                                    and sample["simulationTime"] > admitted_sample["simulationTime"]
                                    and sample["capturedAtUnixMs"] >= admitted_ms)
                    distance = math.dist(sample["pose"][:3], desired[:3])
                    speed = math.sqrt(sum(v*v for v in sample["velocity"]))
                    dot = sum(a*b for a, b in zip(sample["pose"][3:], desired[3:]))
                    orientation_error = 2 * math.acos(min(1., abs(dot)))
                    at_goal = (distance <= self.goals.get("arrivalToleranceM", .12) and speed <= .08
                               and orientation_error <= .15)
                    if post_command and (status is None or status.get("arrived") is True) and at_goal:
                        result.success, result.code = True, "GAZEBO_MEASURED_ARRIVAL"
                        result.observation_id, result.verification_confidence = observation_id, 1.
                        result.message = "Measured Gazebo link pose and velocity; simulation-state localization proxy"
                        self.record("MEASURED_ARRIVAL", commandId=goal.command_id, observationId=observation_id,
                                    pose=sample["pose"], velocity=sample["velocity"], distanceM=distance,
                                    orientationErrorRad=orientation_error,
                                    simulationTime=sample["simulationTime"], sourceSequence=sample["sequence"])
                        handle.succeed()
                        return result
                    if not dispatched and post_command:
                        result.code, result.observation_id = "GAZEBO_MEASURED_NOT_AT_GOAL", observation_id
                        handle.succeed()
                        return result
                    time.sleep(.05)
            except (OSError, ValueError, KeyError, TypeError) as error:
                result.code = "EXECUTION_OUTCOME_UNKNOWN" if dispatched else "GAZEBO_OBSERVATION_UNAVAILABLE"
                result.message = str(error)[:512]
                if dispatched:
                    with self._lock:
                        self._stopped = True
                    try:
                        self.hub.request("/stop", {"robotId": self.robot_id, "commandId": goal.command_id})
                    except (OSError, ValueError):
                        pass  # Hub watchdog still owns cessation if the channel is lost.
                self.record("EXECUTION_FAILED", commandId=goal.command_id, code=result.code, error=str(error))
                handle.abort()
                return result
            finally:
                with self._lock:
                    self._active, self._dispatched = None, False
                    self._leases.pop(goal.command_id, None)

        def _stop(self, request, response):
            response.robot_id, response.profile_revision = self.profile.robot_id, self.revision
            response.procedure_id = self.profile.mobility.safe_stop.procedure_id
            response.accepted = False
            if (request.robot_id != self.profile.robot_id or request.profile_revision != self.revision
                    or request.commissioning_id != self.commissioning_id or request.procedure_id != response.procedure_id):
                return response
            with self._lock:
                self._stopped = True
                active = self._active.command_id if self._active else ""
            command_id = request.command_id or active
            try:
                self.hub.request("/stop", {"robotId": self.robot_id, "commandId": command_id})
                response.accepted = True
                response.message = "Gazebo stop procedure entered; physicalStopVerified=false"
                self.record("SAFE_STOP", commandId=command_id, procedureId=request.procedure_id,
                            reason=request.reason, physicalStopVerified=False)
            except (OSError, ValueError) as error:
                response.message = str(error)[:512]
            return response

    return GazeboController


def main():
    import rclpy
    from rclpy.executors import MultiThreadedExecutor

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--robot-id", required=True, help="Gazebo hub entity, explicitly bound to the profile")
    parser.add_argument("--hub", required=True)
    parser.add_argument("--goals", type=Path, required=True)
    parser.add_argument("--commissioning-id", required=True)
    parser.add_argument("--namespace", default="")
    parser.add_argument("--journal", type=Path)
    args = parser.parse_args()
    rclpy.init(args=[])
    node = controller_class()(json.loads(args.profile.read_text()), args.commissioning_id,
        robot_id=args.robot_id, hub_url=args.hub, goals=json.loads(args.goals.read_text()),
        namespace=args.namespace, journal_path=args.journal)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.hub.request("/stop", {"robotId": node.robot_id,
                "commandId": node._active.command_id if node._active else ""})
        except (OSError, ValueError):
            pass
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
