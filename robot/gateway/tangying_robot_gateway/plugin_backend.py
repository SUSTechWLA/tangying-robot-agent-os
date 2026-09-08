"""Trusted local robot adapters behind the shared runtime safety boundary."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from .backend import RobotBackend, capability
from .contracts import (
    PHYSICAL_TOOLS,
    TOOL_PARAMETERS,
    RobotProfile,
    validate_reconstruction,
    validate_tool_parameters,
)
from .rgbd_images import validate_observation_images
from .runtime import (
    Command,
    Observation,
    ObservationRequest,
    ReconstructionCapture,
    Result,
    RuntimeInfo,
    SceneEntity,
    SemanticState,
    validate_result,
)


def project_entities(reconstruction) -> list[SceneEntity]:
    return [SceneEntity(
        entity_id=item.entity_id, category=item.category, attributes=dict(item.attributes),
        pose_xyz_quat=list(item.pose), confidence=item.confidence, relation=item.relation,
    ) for item in reconstruction.entities]


class ReconstructionTracker:
    """Reject a source rewind or modification of an already identified frame."""

    def __init__(self):
        self._sources: dict[str, tuple[int, str, str, int]] = {}
        self._lock = threading.Lock()

    def accept(self, reconstruction) -> None:
        wire = reconstruction.to_wire()
        digest = hashlib.sha256(json.dumps(wire, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self._lock:
            previous = self._sources.get(reconstruction.source_id)
            if previous is not None:
                sequence, observation_id, fingerprint, observed_ms = previous
                if reconstruction.sequence < sequence or (
                    reconstruction.sequence == sequence and digest != fingerprint
                ) or (
                    reconstruction.sequence > sequence and (
                        reconstruction.observation_id == observation_id
                        or reconstruction.observed_at_unix_ms < observed_ms
                    )
                ):
                    raise ValueError("reconstruction source sequence or observation identity regressed")
            self._sources[reconstruction.source_id] = (
                reconstruction.sequence, reconstruction.observation_id, digest,
                reconstruction.observed_at_unix_ms,
            )


class PluginBackend(RobotBackend):
    """Bind canonical tools to a commissioned driver without assuming its joints.

    Constructing an adapter never connects, arms or moves hardware. Physical
    tools remain unavailable unless the local readiness callback explicitly
    reports that the driver has been armed. The callback must also incorporate
    driver faults and commissioning requirements for the selected tool set.
    """

    def __init__(
        self,
        profile: dict[str, Any] | RobotProfile,
        *,
        observation_provider: Callable[[], dict[str, Any] | ReconstructionCapture],
        handlers: Mapping[str, Callable[[Command], Result]],
        stop: Callable[[str], None],
        physical_ready: Callable[[], bool] | None = None,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        disconnect: Callable[[], None] | None = None,
        simulation: bool = False,
    ):
        self._profile = RobotProfile.model_validate(copy.deepcopy(profile))
        if not callable(observation_provider) or not callable(stop):
            raise TypeError("observation provider and emergency stop callback are required")
        if any(name not in self._profile.tools or not callable(handler) for name, handler in handlers.items()):
            raise ValueError("handlers must implement tools declared in the profile")
        if {"observe_scene", "emergency_stop"} & set(handlers):
            raise ValueError("observation and emergency stop use the dedicated callbacks")
        if simulation and any(sensor.source_type != "sim_ground_truth" for sensor in self._profile.sensors):
            raise ValueError("simulation adapters must identify their perception as sim_ground_truth")
        self._observation_provider = observation_provider
        self._handlers = dict(handlers)
        self._stop = stop
        self._physical_ready = physical_ready
        self._state_provider = state_provider
        self._disconnect = disconnect
        self._simulation = simulation
        self._tracker = ReconstructionTracker()
        self._stop_generation = 0

    def capabilities(self) -> RuntimeInfo:
        try:
            ready = self._physical_ready is not None and self._physical_ready() is True
        except Exception:  # noqa: BLE001 - readiness failure cannot enable motion
            ready = False
        capabilities = []
        for name in self._profile.tools:
            blockers = []
            if name not in self._handlers and name not in {"observe_scene", "emergency_stop"}:
                blockers.append("TOOL_HANDLER_REQUIRED")
            if name in PHYSICAL_TOOLS and name != "emergency_stop" and not ready:
                blockers.append("ROBOT_NOT_ARMED")
            schema = TOOL_PARAMETERS[name].model_json_schema(by_alias=True)
            capabilities.append(capability(
                name, f"Canonical {name} tool for {self._profile.model_id}.",
                available=not blockers, blockers=blockers,
                safety_level="physical_motion" if name in PHYSICAL_TOOLS else "read_only",
                cancellable=name in PHYSICAL_TOOLS and name != "emergency_stop",
                input_parameters=list(schema.get("properties", {})),
                output_parameters=["success", "code", "observation_id", "confidence"],
            ))
        available = {item.name for item in capabilities if item.available}
        return RuntimeInfo(
            robot_id=self._profile.robot_id, adapter=self._profile.adapter_id,
            adapter_version=self._profile.adapter_version, software_version="0.2.0",
            manipulation_ready={"manipulation.pick", "manipulation.place"} <= available,
            # Per-tool missing handlers must not disable another available
            # tool. The global blocker only describes shared driver readiness.
            blockers=["ROBOT_NOT_ARMED"] if not ready and set(self._profile.tools) & (PHYSICAL_TOOLS - {"emergency_stop"}) else [],
            capabilities=capabilities, robot_profile=self._profile.to_wire(),
        )

    def observe(self, request: ObservationRequest) -> Observation:
        provided = self._observation_provider()
        capture = provided if isinstance(provided, ReconstructionCapture) else ReconstructionCapture(provided)
        images = validate_observation_images(capture)
        reconstruction = validate_reconstruction(capture.reconstruction, self._profile)
        self._tracker.accept(reconstruction)
        state = self._state_provider() if self._state_provider is not None else {}
        if not isinstance(state, dict):
            raise TypeError("state provider must return an object")
        # JSON serialization rejects NaN/Infinity and non-transportable SDK types.
        json.dumps(state, allow_nan=False)
        return Observation(
            observation_id=reconstruction.observation_id,
            wall_time_unix_ms=reconstruction.observed_at_unix_ms,
            monotonic_time_ns=time.monotonic_ns(),
            semantic_state=SemanticState(mode="SIMULATION" if self._simulation else "ROBOT_RUNTIME"),
            robot_state=copy.deepcopy(state), entities=project_entities(reconstruction),
            reconstruction=reconstruction.to_wire(),
            **images,
        )

    def execute(self, command: Command) -> Result:
        stop_generation = self._stop_generation
        try:
            validate_tool_parameters(command.capability, command.parameters, self._profile,
                                     target_ref=command.target_ref)
        except (ValueError, KeyError) as exc:
            return Result(False, "TOOL_PARAMETERS_INVALID", str(exc), confidence=0.0)
        advertised = next((item for item in self.capabilities().capabilities if item.name == command.capability), None)
        if advertised is None or not advertised.available:
            return Result(False, "CAPABILITY_UNAVAILABLE", confidence=0.0)
        if command.capability == "emergency_stop":
            self.stop(command.parameters.get("reason", "COMMAND_EMERGENCY_STOP"))
            return Result(True, "ESTOPPED")
        try:
            # Re-sample before any tool uses a scene, including motion, so an
            # earlier healthy poll cannot authorize motion after sensor loss.
            observation = self.observe(ObservationRequest())
        except Exception as exc:  # noqa: BLE001 - sensor driver faults must block execution
            return Result(False, "RECONSTRUCTION_INVALID", str(exc), confidence=0.0)
        if command.capability == "observe_scene":
            return Result(True, observation_id=observation.observation_id)
        # A sensor provider can outlive cancellation or the command lease.
        # Never invoke a handler after a stop occurred during that observation,
        # even if a vendor readiness callback has not caught up yet.
        if stop_generation != self._stop_generation:
            return Result(False, "COMMAND_STOPPED", confidence=0.0)
        advertised = next((item for item in self.capabilities().capabilities if item.name == command.capability), None)
        if advertised is None or not advertised.available:
            return Result(False, "CAPABILITY_UNAVAILABLE", confidence=0.0)
        result = self._handlers[command.capability](command)
        # Do not hide a malformed post-motion result behind an ordinary known
        # failure. The common service must classify it as unknown and stop.
        return validate_result(result)

    def stop(self, reason: str) -> None:
        self._stop_generation += 1
        self._stop(reason)

    def disconnect(self) -> None:
        if self._disconnect is not None:
            self._disconnect()
