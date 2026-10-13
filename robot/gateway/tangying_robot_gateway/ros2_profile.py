"""Fail-closed profile bridge shared by ROS2 transport and CPU-only contract tests.

The device controller, not this bridge, owns stabilization and its independent
command-loss watchdog. Nothing in this module arms a robot.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from typing import Annotated

from pydantic import Field

from .contracts import (
    CANONICAL_TOOLS,
    PHYSICAL_TOOLS,
    Contract,
    Identifier,
    RobotProfile,
    validate_pose,
    validate_reconstruction,
    validate_tool_parameters,
)
from .plugin_backend import PluginBackend, ReconstructionTracker
from .runtime import Result
from .spatial_localization import (
    SpatialNavigationDeployment,
    canonical_json,
    pose_reached,
    strict_json,
)


def profile_revision(profile: RobotProfile | dict) -> str:
    wire = RobotProfile.model_validate(profile).to_wire()
    return hashlib.sha256(json.dumps(wire, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def navigation_state(payload: str, profile: RobotProfile) -> dict:
    """Bound map semantics to this reconstruction and commissioned coordinates."""
    if not isinstance(payload, str) or len(payload.encode()) > 64 * 1024:
        raise ValueError("ROS2 robot state exceeds 64 KiB")
    value = strict_json(payload or "{}", maximum=64 * 1024)
    if not isinstance(value, dict) or set(value) - {"active_map", "semantic_navigation", "base_pose", "base_pose_frame", "spatial_localization"}:
        raise ValueError("ROS2 state contains unsupported fields")
    json.dumps(value, allow_nan=False)
    if "base_pose" in value:
        pose = value["base_pose"]
        if not isinstance(pose, list) or any(type(v) not in (int, float) for v in pose):
            raise ValueError("ROS2 base pose must be a finite world pose")
        validate_pose(pose)
        if value.get("base_pose_frame", "world") != "world":
            raise ValueError("ROS2 base pose must use the world frame")
        value["base_pose_frame"] = "world"
    elif "base_pose_frame" in value:
        raise ValueError("ROS2 base pose frame has no pose")
    active = value.get("active_map")
    if active is not None:
        if not isinstance(active, dict) or set(active) != {"mapId", "mapRevision", "calibrationRevision"}:
            raise ValueError("ROS2 active map must identify its exact map and calibration")
        for item in active.values():
            if not isinstance(item, str) or not item or len(item) > 256 or any(c.isspace() for c in item):
                raise ValueError("ROS2 map identity invalid")
        if active["calibrationRevision"] not in {s.transform_revision for s in profile.sensors}:
            raise ValueError("ROS2 active map calibration not in commissioned profile")
    nav = value.get("semantic_navigation")
    if nav is not None:
        keys = {"schemaVersion", "frameId", "robotId", "mapId", "mapRevision", "calibrationRevision", "goals", "aliases", "routeEdges"}
        if not isinstance(nav, dict) or set(nav) != keys or active is None:
            raise ValueError("ROS2 semantic navigation requires its active map and complete contract")
        if (nav["schemaVersion"] != "semantic.navigation.v1" or nav["frameId"] != "world"
                or nav["robotId"] != profile.robot_id or any(nav[key] != item for key, item in active.items())):
            raise ValueError("ROS2 semantic navigation identity mismatch")
        goals, aliases, edges = nav["goals"], nav["aliases"], nav["routeEdges"]
        if not isinstance(goals, dict) or not 1 <= len(goals) <= 128 or not isinstance(aliases, dict) or len(aliases) > 256 or not isinstance(edges, dict) or set(edges) != set(goals):
            raise ValueError("ROS2 semantic graph size or node set invalid")
        for name, pose in goals.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 128:
                raise ValueError("ROS2 semantic goal name invalid")
            validate_tool_parameters("navigation.navigate", {"goalPose": pose}, profile)
            if not isinstance(edges[name], list) or len(edges[name]) > 128 or any(not isinstance(n, str) or n not in goals for n in edges[name]):
                raise ValueError("ROS2 semantic route references an unknown goal")
        if any(not isinstance(alias, str) or not alias.strip() or len(alias) > 128 or not isinstance(goal, str) or goal not in goals for alias, goal in aliases.items()):
            raise ValueError("ROS2 semantic alias invalid")
    return value


class DriverState(Contract):
    robot_id: Identifier
    profile_revision: Identifier
    commissioning_id: Identifier
    sequence: int = Field(gt=0, le=9_007_199_254_740_991)
    observed_at_unix_ms: int = Field(gt=0)
    commissioned: bool
    armed: bool
    healthy: bool
    watchdog_active: bool
    stop_procedure_id: Identifier
    command_loss_timeout_ms: int = Field(gt=0, le=60_000)
    max_linear_speed_mps: float = Field(gt=0)
    max_angular_speed_radps: float = Field(gt=0)
    supported_tools: list[Identifier] = Field(max_length=len(CANONICAL_TOOLS))
    faults: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    spatial_navigation_revision: str = Field(default="", max_length=256)


class Ros2ProfileBridge:
    def __init__(self, profile, transport, *, commissioning_id: str, state_max_age_ms=500, spatial_navigation=None):
        self.profile = RobotProfile.model_validate(profile)
        if self.profile.mobility is None:
            raise ValueError("ROS2 mobile bridge requires an explicit mobility profile")
        if not commissioning_id or len(commissioning_id) > 256 or any(c.isspace() for c in commissioning_id):
            raise ValueError("a fixed local commissioning_id is required")
        if type(state_max_age_ms) is not int or not 1 <= state_max_age_ms <= 5000:
            raise ValueError("driver state maximum age must be 1..5000 ms")
        if spatial_navigation is not None and not isinstance(spatial_navigation, SpatialNavigationDeployment):
            raise ValueError("spatial_navigation must be a validated local deployment")
        if spatial_navigation is not None and spatial_navigation._profile != self.profile:
            raise ValueError("spatial deployment profile differs from bridge")
        self.spatial_navigation = spatial_navigation
        self.spatial_navigation_revision = spatial_navigation.revision if spatial_navigation else ""
        self._localization_sequences = {}
        self.revision = profile_revision(self.profile)
        self.transport, self.commissioning_id = transport, commissioning_id
        self.state_max_age_ms = state_max_age_ms
        self._lock = threading.RLock()
        self._state = self._observation = None
        self._state_sequence = 0
        self._state_received = self._observation_received = 0.0
        self._state_error = "NO_DRIVER_STATE"
        self._observation_error = "NO_RECONSTRUCTION"
        self._tracker = ReconstructionTracker()
        self._captures = OrderedDict()
        self._accepted_sequences = {}
        self._capture_context = threading.local()
        self._stopped = False
        self._active_command = ""
        self._last_readiness_failure = {}

    def _identity(self, robot_id, revision):
        if robot_id != self.profile.robot_id or revision != self.revision:
            raise ValueError("ROS2 robot/profile identity mismatch")

    def receive_state(self, value: dict):
        with self._lock:
            try:
                state = DriverState.model_validate(value)
                self._identity(state.robot_id, state.profile_revision)
                if state.spatial_navigation_revision != self.spatial_navigation_revision:
                    raise ValueError("ROS2 controller spatial navigation deployment mismatch")
                mobility = self.profile.mobility
                if state.commissioning_id != self.commissioning_id or state.stop_procedure_id != mobility.safe_stop.procedure_id:
                    raise ValueError("ROS2 commissioning or stop procedure mismatch")
                if state.sequence <= self._state_sequence:
                    raise ValueError("ROS2 driver state sequence regressed or repeated")
                if not 0 <= int(time.time() * 1000) - state.observed_at_unix_ms <= self.state_max_age_ms:
                    raise ValueError("ROS2 driver state is stale or future dated")
                if (state.command_loss_timeout_ms > mobility.safe_stop.command_loss_timeout_ms
                        or state.max_linear_speed_mps > mobility.max_linear_speed_mps
                        or state.max_angular_speed_radps > mobility.max_angular_speed_radps):
                    raise ValueError("ROS2 controller watchdog or speeds exceed commissioned limits")
                expected = set(self.profile.tools) - {"observe_scene", "emergency_stop"}
                if len(set(state.supported_tools)) != len(state.supported_tools) or set(state.supported_tools) != expected:
                    raise ValueError("ROS2 controller tool set disagrees with profile")
                self._state_sequence = state.sequence
                self._state, self._state_received, self._state_error = state, time.monotonic(), ""
            except Exception as exc:
                self._state, self._state_error = None, str(exc)
                raise

    def receive_observation(self, robot_id: str, revision: str, payload: str, robot_state_json="{}"):
        with self._lock:
            try:
                self._identity(robot_id, revision)
                if not isinstance(payload, str) or len(payload.encode()) > 2 * 1024 * 1024:
                    raise ValueError("ROS2 reconstruction exceeds 2 MiB")
                observed = validate_reconstruction(strict_json(payload, maximum=2 * 1024 * 1024), self.profile)
                robot_state = navigation_state(robot_state_json, self.profile)
                if ("active_map" in robot_state
                        and robot_state["active_map"]["calibrationRevision"] != observed.transform_revision):
                    raise ValueError("ROS2 semantic map and capture calibration disagree")
                if self.spatial_navigation is not None:
                    localized = self.spatial_navigation.localization(robot_state.get("spatial_localization"), observed)
                    expected_map = {"mapId": localized["mapId"], "mapRevision": localized["mapRevision"],
                                    "calibrationRevision": localized["transformRevision"]}
                    if robot_state.get("active_map") != expected_map:
                        raise ValueError("ROS2 spatial localization and active map disagree")
                    if "base_pose" in robot_state and robot_state["base_pose"] != localized["pose"]:
                        raise ValueError("ROS2 base pose and spatial localization disagree")
                    previous_local = self._localization_sequences.get(localized["sourceId"])
                    if previous_local is not None and (localized["sequence"] < previous_local[0]
                            or (localized["sequence"] == previous_local[0] and canonical_json(localized) != previous_local[1])):
                        raise ValueError("ROS2 spatial localization sequence regressed or changed")
                    robot_state.update(spatial_localization=localized, base_pose=localized["pose"], base_pose_frame="world")
                elif "spatial_localization" in robot_state:
                    raise ValueError("ROS2 spatial localization requires a local deployment")
                self._tracker.accept(observed)
                previous = self._captures.get(observed.observation_id)
                if previous is not None and (previous[0] != observed or previous[2] != robot_state):
                    raise ValueError("ROS2 observation identity was reused for different content")
                # Re-broadcasting a capture never extends its monotonic lifetime.
                if previous is None:
                    previous = (observed, time.monotonic(), robot_state)
                    self._captures[observed.observation_id] = previous
                    if len(self._captures) > 64:
                        self._captures.popitem(last=False)
                if self.spatial_navigation is not None:
                    self._localization_sequences[localized["sourceId"]] = (localized["sequence"], canonical_json(localized))
                self._observation_received = previous[1]
                self._accepted_sequences[observed.source_id] = observed.sequence
                self._observation, self._observation_error = observed, ""
            except Exception as exc:
                self._observation, self._observation_error = None, str(exc)
                raise

    def observe(self):
        with self._lock:
            if self._observation is None:
                raise ValueError(self._observation_error)
            observed = validate_reconstruction(self._observation, self.profile)
            sensor = next(s for s in self.profile.sensors if s.source_id == observed.source_id)
            if (time.monotonic() - self._observation_received) * 1000 > sensor.max_age_ms:
                raise ValueError("ROS2 reconstruction exceeded monotonic lifetime")
            captured_state = self._captures[observed.observation_id][2]
            if self.spatial_navigation is not None:
                self.spatial_navigation.localization(captured_state["spatial_localization"], observed,
                                                     received_monotonic=self._observation_received)
            self._capture_context.robot_state = copy.deepcopy(captured_state)
            return copy.deepcopy(observed.to_wire())

    def ready(self):
        with self._lock:
            state = self._state
            def unavailable(reason):
                self._last_readiness_failure = {"reason": reason, "atUnixMs": int(time.time() * 1000),
                    "stateAgeMs": None if state is None else int(time.time() * 1000) - state.observed_at_unix_ms,
                    "stateReceiveAgeMs": (time.monotonic() - self._state_received) * 1000,
                    "observationReceiveAgeMs": (time.monotonic() - self._observation_received) * 1000}
                return False
            if self._stopped:
                return unavailable("LOCAL_STOP_LATCHED")
            if state is None:
                return unavailable(self._state_error)
            if not all((state.commissioned, state.armed, state.healthy, state.watchdog_active)) or state.faults:
                return unavailable("CONTROLLER_STATUS_NOT_READY")
            wall_age = int(time.time() * 1000) - state.observed_at_unix_ms
            if not 0 <= wall_age <= self.state_max_age_ms or (time.monotonic() - self._state_received) * 1000 > self.state_max_age_ms:
                return unavailable("DRIVER_STATE_EXPIRED")
            try:
                self.observe()
                return True if self.transport.ready() is True else unavailable("ROS2_ENDPOINT_UNAVAILABLE")
            except Exception as exc:  # noqa: BLE001 - any local driver fault must disable motion
                return unavailable(str(exc))

    def state(self):
        with self._lock:
            # PluginBackend reads this on the same thread immediately after
            # observe(). Concurrent DDS captures cannot pair a new map with the
            # previous reconstruction.
            robot_state = getattr(self._capture_context, "robot_state", {})
            self._capture_context.robot_state = {}
            return {**copy.deepcopy(robot_state), "transport": "ros2.profiled.v1", "profileRevision": self.revision,
                    "commissioningId": self.commissioning_id, "driverStateSequence": self._state_sequence,
                    "driverStateError": self._state_error, "observationError": self._observation_error,
                    "lastReadinessFailure": copy.deepcopy(self._last_readiness_failure),
                    "locallyStopped": self._stopped, "spatialNavigationRevision": self.spatial_navigation_revision}

    def execute(self, command):
        physical = command.capability in PHYSICAL_TOOLS
        start_ms = int(time.time() * 1000)
        start_monotonic = time.monotonic()
        with self._lock:
            if self._stopped or (physical and not self.ready()):
                return Result(False, "ROS2_DRIVER_NOT_READY", confidence=0.0)
            self._active_command = command.command_id
            admission_sequences = dict(self._accepted_sequences)
        goal = {
            "schema_version": "ros2.execution.v1", "robot_id": self.profile.robot_id,
            "adapter_id": self.profile.adapter_id, "profile_revision": self.revision,
            "commissioning_id": self.commissioning_id, "catalog_revision": command.catalog_revision,
            "task_id": command.task_id, "command_id": command.command_id,
            "capability": command.capability, "target_ref": command.target_ref,
            "parameters_json": json.dumps(command.parameters, separators=(",", ":"), allow_nan=False),
            "lease_ms": command.lease_ms, "deadline_unix_ms": command.deadline_unix_ms,
            "idempotency_key": command.idempotency_key, "safety_profile": command.safety_profile,
            "approval_id": command.approval_id, "world_revision_basis": command.world_revision_basis,
            "resource_id": command.resource_id, "fencing_token": command.fencing_token,
            "navigation_plan_json": "",
        }

        def tick():
            with self._lock:
                if self._stopped or (physical and not self.ready()):
                    raise RuntimeError("ROS2 driver readiness was lost during command")
            if int(time.time() * 1000) >= command.deadline_unix_ms:
                raise TimeoutError("ROS2 command deadline expired")

        navigation_plan = None
        try:
            tick()
            if self.spatial_navigation is not None and command.capability == "navigation.navigate":
                with self._lock:
                    observed = self._observation
                    localized = self.spatial_navigation.localization(
                        self._captures[observed.observation_id][2]["spatial_localization"], observed,
                        received_monotonic=self._observation_received)
                navigation_plan, goal["navigation_plan_json"] = self.spatial_navigation.plan(
                    localized, command.parameters["goalPose"], command.command_id)
                # Static search can outlive the capture: never dispatch a route
                # rooted at a position that changed while planning.
                tick()
                with self._lock:
                    current = self._captures[self._observation.observation_id][2]["spatial_localization"]
                    if not pose_reached(current["pose"], localized["pose"]):
                        raise ValueError("ROS2 localization changed during spatial planning")
            result = self.transport.execute(goal, tick=tick)
            tick()
            self._identity(result["robot_id"], result["profile_revision"])
            if result["command_id"] != command.command_id or type(result["success"]) is not bool:
                raise ValueError("ROS2 action result command identity/type mismatch")
            expected_plan_id = navigation_plan["planId"] if navigation_plan else ""
            if result.get("navigation_plan_id", "") != expected_plan_id:
                raise ValueError("ROS2 action result navigation plan identity mismatch")
            confidence = result["verification_confidence"]
            if isinstance(confidence, bool) or not isinstance(confidence, (float, int)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("ROS2 action result confidence invalid")
            observation_id = result["observation_id"]
            if result["success"] and (physical or command.capability.startswith("verify_")):
                # DDS topics and action results may arrive in either order.
                deadline = min(time.monotonic() + .5, time.monotonic() + max(0, command.deadline_unix_ms - int(time.time() * 1000)) / 1000)
                while True:
                    tick()
                    with self._lock:
                        captured = self._captures.get(observation_id)
                        if captured is not None:
                            observed = validate_reconstruction(captured[0], self.profile)
                            sensor = next(s for s in self.profile.sensors if s.source_id == observed.source_id)
                            if (observed.sequence > admission_sequences.get(observed.source_id, 0)
                                    and captured[1] > start_monotonic and observed.observed_at_unix_ms >= start_ms
                                    and (time.monotonic() - captured[1]) * 1000 <= sensor.max_age_ms):
                                if self.spatial_navigation is not None and command.capability in {"navigation.navigate", "verify_arrival"}:
                                    actual = self.spatial_navigation.localization(captured[2]["spatial_localization"], observed,
                                                                                 received_monotonic=captured[1])
                                    target = command.parameters.get("goalPose")
                                    if target is None or not pose_reached(actual["pose"], target):
                                        raise ValueError("ROS2 success lacks measured xyz/quaternion arrival")
                                break
                    if time.monotonic() >= deadline:
                        raise ValueError(f"ROS2 successful action lacks its fresh post-command observation: {observation_id}")
                    time.sleep(.005)
            return Result(result["success"], result["code"], result["message"], observation_id, confidence)
        finally:
            with self._lock:
                self._active_command = ""

    def stop(self, reason):
        with self._lock:
            self._stopped = True
            command_id = self._active_command
        procedure = self.profile.mobility.safe_stop.procedure_id
        result = self.transport.stop({"robot_id": self.profile.robot_id, "profile_revision": self.revision,
                                      "commissioning_id": self.commissioning_id, "procedure_id": procedure,
                                      "command_id": command_id, "reason": reason})
        self._identity(result["robot_id"], result["profile_revision"])
        if result["accepted"] is not True or result["procedure_id"] != procedure:
            raise RuntimeError("ROS2 controller did not acknowledge the registered safe-stop procedure")

    def backend(self):
        bridge = self

        class ProfiledBackend(PluginBackend):
            def capabilities(self):
                info = super().capabilities()
                if bridge.spatial_navigation is not None:
                    for item in info.capabilities:
                        if item.name == "navigation.navigate":
                            item.description += " Spatial deployment revision: " + bridge.spatial_navigation_revision + "."
                return info

            def wait_command_capture(self, command, completed_ns, completed_ms):
                # The common Runtime embeds this immutable post-completion
                # snapshot into the command result. Go can retain exact evidence
                # even while live telemetry advances to a newer DDS capture.
                deadline = min(time.monotonic() + .5,
                               time.monotonic() + max(0, command.deadline_unix_ms - int(time.time() * 1000)) / 1000)
                while time.monotonic() < deadline:
                    with bridge._lock:
                        observed = bridge._observation
                        if (observed is not None and observed.observed_at_unix_ms > completed_ms
                                and bridge._observation_received * 1_000_000_000 > completed_ns):
                            return True
                        if bridge._stopped:
                            return False
                    time.sleep(.005)
                return False

        return ProfiledBackend(
            self.profile, observation_provider=self.observe,
            handlers={name: self.execute for name in self.profile.tools if name not in {"observe_scene", "emergency_stop"}},
            stop=self.stop, stop_policy_id=self.profile.mobility.safe_stop.procedure_id,
            physical_ready=self.ready, state_provider=self.state, disconnect=self.transport.close,
        )
