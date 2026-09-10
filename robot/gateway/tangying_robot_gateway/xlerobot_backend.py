from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from numbers import Real
from pathlib import Path
from typing import Any

from .backend import BackendResult, RobotBackend, capability
from .runtime import (
    Command,
    Observation,
    ObservationRequest,
    RuntimeInfo,
    SceneEntity,
    SemanticState,
)

READ_ONLY_SKILLS = {"observe_scene", "resolve_targets", "plan_grasp"}
VERIFY_SKILLS = {"verify_grasp", "verify_placement", "verify_arrival"}
MAX_ACTION_CHUNK_LENGTH = 64
MAX_ABSOLUTE_ACTION_VALUE = 100.0
# Kept independent of the optional hardware driver import for gateway-only installs.
ALLOWED_ACTION_KEYS = frozenset(
    f"{side}_arm_{joint}.pos"
    for side in ("left", "right")
    for joint in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
) | {"head_motor_1.pos", "head_motor_2.pos"}
MOBILE_BASE_KEYS = {"x.vel", "theta.vel"}


def validate_action_chunk(actions: Any, max_length: int = MAX_ACTION_CHUNK_LENGTH) -> BackendResult | None:
    if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length <= 0:
        return BackendResult(False, "MAX_ACTION_CHUNK_LENGTH_INVALID")
    if not isinstance(actions, list) or not actions:
        return BackendResult(False, "POLICY_ACTION_CHUNK_REQUIRED")
    if len(actions) > max_length:
        return BackendResult(
            False,
            "ACTION_CHUNK_TOO_LONG",
            f"{len(actions)} > {max_length}",
        )
    for action in actions:
        if not isinstance(action, dict) or not action:
            return BackendResult(False, "ACTION_CHUNK_MALFORMED")
        for key, value in action.items():
            if key in MOBILE_BASE_KEYS:
                return BackendResult(False, "MOBILE_BASE_DISABLED", key)
            if not isinstance(key, str) or key not in ALLOWED_ACTION_KEYS:
                return BackendResult(False, "ACTION_KEY_REJECTED", str(key))
            if not isinstance(value, Real) or isinstance(value, bool):
                return BackendResult(False, "ACTION_VALUE_NOT_NUMERIC", str(key))
            try:
                number = float(value)
            except OverflowError:
                return BackendResult(False, "ACTION_VALUE_OUT_OF_RANGE", str(key))
            if not math.isfinite(number):
                return BackendResult(False, "ACTION_VALUE_NOT_FINITE", str(key))
            if abs(number) > MAX_ABSOLUTE_ACTION_VALUE:
                return BackendResult(False, "ACTION_VALUE_OUT_OF_RANGE", str(key))
            if key.endswith("gripper.pos") and not 0.0 <= number <= MAX_ABSOLUTE_ACTION_VALUE:
                return BackendResult(False, "GRIPPER_VALUE_OUT_OF_RANGE", str(key))
    return None


class XLeRobotDirectBackend(RobotBackend):
    """ROS2-free XLeRobot backend.

    It implements the same RobotBackend contract as the ROS 2 gateway but calls
    XLeRobotDriver directly in this process. Perception and verification remain
    pluggable hardware integrations. Policy inference belongs on the laptop;
    this runtime accepts only a bounded action chunk already attached to the
    command and fails closed when it is absent.
    """

    def __init__(
        self,
        driver: Any,
        *,
        entity_provider: Callable[[], list[dict[str, Any]]] | None = None,
        verifier: Callable[[str, str, dict[str, Any]], BackendResult] | None = None,
        robot_id: str = "xlerobot-edge-direct",
    ):
        self.driver = driver
        self.entity_provider = entity_provider
        self.verifier = verifier
        if not robot_id or not robot_id.strip():
            raise ValueError("robot_id must not be empty")
        self.robot_id = robot_id

    @classmethod
    def from_env(
        cls,
        *,
        entity_provider: Callable[[], list[dict[str, Any]]] | None = None,
        verifier: Callable[[str, str, dict[str, Any]], BackendResult] | None = None,
    ) -> XLeRobotDirectBackend:
        from xlerobot_adapter.driver import XLeRobotDriver

        driver = XLeRobotDriver(
            upstream_root=Path(os.getenv("XLEROBOT_UPSTREAM_ROOT", "/opt/XLeRobot")),
            calibration_root=Path(
                os.getenv(
                    "XLEROBOT_CALIBRATION_ROOT",
                    "/var/lib/tangying-robot-agent-os/calibration",
                )
            ),
            ports=(
                os.getenv("XLEROBOT_PORT1", "/dev/tangying-left"),
                os.getenv("XLEROBOT_PORT2", "/dev/tangying-right"),
            ),
            max_relative_target=float(
                os.getenv("XLEROBOT_MAX_RELATIVE_TARGET", "8.0")
            ),
            max_action_chunk_length=int(
                os.getenv("XLEROBOT_MAX_ACTION_CHUNK_LENGTH", "64")
            ),
        )
        return cls(driver, entity_provider=entity_provider, verifier=verifier,
                   robot_id=os.getenv("ROBOT_ID", "xlerobot-edge-direct"))

    def capabilities(self) -> RuntimeInfo:
        driver_capabilities = self.driver.capabilities()
        driver_ready = driver_capabilities.manipulation_ready and bool(getattr(self.driver, "is_armed", False))
        driver_blockers = list(driver_capabilities.blockers)
        if not getattr(self.driver, "is_armed", False):
            driver_blockers.append("ROBOT_NOT_ARMED")
        entity_ready = self.entity_provider is not None
        verify_ready = self.verifier is not None
        physical_ready = driver_ready and entity_ready and verify_ready
        physical_blockers = list(driver_blockers)
        if not entity_ready:
            physical_blockers.append("ENTITY_PROVIDER_REQUIRED")
        if not verify_ready:
            physical_blockers.append("VERIFIER_REQUIRED")
        capabilities = [
            capability(
                "observe_scene",
                "Return grounded scene entities from the robot perception stack.",
                available=entity_ready,
                safety_level="read_only",
                blockers=[] if entity_ready else ["ENTITY_PROVIDER_REQUIRED"],
                default_timeout_ms=5_000,
                input_parameters=["streams", "max_rate_hz"],
                output_parameters=["entities"],
            ),
            capability(
                "resolve_targets",
                "Resolve grounded object and destination references.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            capability(
                "plan_grasp",
                "Plan a tabletop grasp without moving the robot.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            capability(
                "manipulation.pick",
                "Execute a bounded, policy-provided pick action chunk.",
                available=physical_ready,
                safety_level="physical_motion",
                blockers=[] if physical_ready else list(physical_blockers),
                cancellable=True,
                recoverable=False,
                default_timeout_ms=15_000,
                input_parameters=["target_ref", "action_chunk"],
                output_parameters=["grasp_state"],
            ),
            capability(
                "verify_grasp",
                "Verify the current grasp with the external perception verifier.",
                available=verify_ready,
                safety_level="read_only",
                blockers=[] if verify_ready else ["VERIFIER_REQUIRED"],
                default_timeout_ms=5_000,
                input_parameters=["object_id"],
                output_parameters=["verification_confidence"],
            ),
            capability(
                "manipulation.place",
                "Execute a bounded, policy-provided place action chunk.",
                available=physical_ready,
                safety_level="physical_motion",
                blockers=[] if physical_ready else list(physical_blockers),
                cancellable=True,
                recoverable=False,
                default_timeout_ms=15_000,
                input_parameters=["target_ref", "action_chunk"],
                output_parameters=["placement_state"],
            ),
            capability(
                "verify_placement",
                "Verify the final placement with the external perception verifier.",
                available=verify_ready,
                safety_level="read_only",
                blockers=[] if verify_ready else ["VERIFIER_REQUIRED"],
                default_timeout_ms=5_000,
                input_parameters=["object_id", "destination_id"],
                output_parameters=["verification_confidence"],
            ),
            capability(
                "verify_arrival",
                "Verify a mobile base waypoint with a fresh RGB-D and localization capture.",
                available=verify_ready,
                safety_level="read_only",
                blockers=[] if verify_ready else ["VERIFIER_REQUIRED"],
                default_timeout_ms=5_000,
                input_parameters=["goal_pose"],
                output_parameters=["verification_confidence"],
            ),
            capability(
                "recover_to_safe_pose",
                "Requires a separately commissioned recovery trajectory; use attended local recovery in V1.",
                available=False,
                safety_level="physical_motion",
                blockers=["RECOVERY_POLICY_REQUIRED"],
                cancellable=True,
                recoverable=False,
                default_timeout_ms=15_000,
                input_parameters=["action_chunk"],
                output_parameters=["safe_pose_reached"],
            ),
            capability(
                "emergency_stop",
                "Immediately disable torque and latch the safety stop.",
                available=True,
                safety_level="physical_motion",
                cancellable=False,
                recoverable=False,
                default_timeout_ms=5_000,
            ),
        ]
        return RuntimeInfo(
            robot_id=self.robot_id,
            adapter="xlerobot_direct",
            manipulation_ready=physical_ready,
            blockers=[] if physical_ready else list(physical_blockers),
            software_version="0.3.0",
            capabilities=capabilities,
        )

    def observe(self, request: ObservationRequest) -> Observation:
        anomalies: list[str] = []
        last_error = ""
        entities: list[dict[str, Any]] = []
        if self.entity_provider is not None:
            try:
                entities = self.entity_provider()
                if not isinstance(entities, list):
                    raise TypeError("entity provider must return a list")
                identifiers = set()
                for entity in entities:
                    if not isinstance(entity, dict):
                        raise TypeError("scene entity must be an object")
                    identifier = entity.get("entity_id")
                    if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                        raise ValueError("scene entity IDs must be nonempty and unique")
                    identifiers.add(identifier)
                    if not isinstance(entity.get("category"), str) or not entity["category"]:
                        raise ValueError("scene entity category is required")
                    attributes = entity.get("attributes", {})
                    if not isinstance(attributes, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in attributes.items()):
                        raise ValueError("scene entity attributes must be string pairs")
                    pose = entity.get("pose_xyz_quat", [])
                    if not isinstance(pose, list) or len(pose) not in (0, 7) or any(type(v) not in (int, float) or not math.isfinite(v) for v in pose):
                        raise ValueError("scene entity pose must be empty or seven finite numbers")
                    confidence = entity.get("confidence", 0.0)
                    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                        raise ValueError("scene entity confidence must be finite and between zero and one")
                    if not isinstance(entity.get("relation", ""), str):
                        raise TypeError("scene entity relation must be a string")
            except Exception as exc:  # noqa: BLE001 - perception faults must fail closed
                anomalies.append("ENTITY_PROVIDER_FAILED")
                last_error = str(exc)
                entities = []
        observation = Observation(
            observation_id=f"direct-{time.monotonic_ns()}",
            wall_time_unix_ms=int(time.time() * 1000),
            monotonic_time_ns=time.monotonic_ns(),
            semantic_state=SemanticState(
                anomalies=anomalies,
                last_error=last_error,
            ),
        )
        for entity in entities:
            observation.entities.append(
                SceneEntity(
                    entity_id=str(entity.get("entity_id", "")),
                    category=str(entity.get("category", "")),
                    attributes={
                        str(key): str(value)
                        for key, value in entity.get("attributes", {}).items()
                    },
                    pose_xyz_quat=[
                        float(value) for value in entity.get("pose_xyz_quat", [])
                    ],
                    confidence=float(entity.get("confidence", 0.0)),
                    relation=str(entity.get("relation", "")),
                )
            )
        if hasattr(self.driver, "observation"):
            try:
                raw_state = self.driver.observation()
                if isinstance(raw_state, dict):
                    observation.robot_state = raw_state
            except Exception as exc:  # noqa: BLE001 - display fault is non-fatal but visible
                anomalies.append("OBSERVATION_FAILED")
                if last_error:
                    last_error += "; "
                last_error += str(exc)
                observation.semantic_state = SemanticState(
                    anomalies=anomalies, last_error=last_error
                )
        return observation

    def execute(self, command: Command) -> BackendResult:
        parameters = command.parameters

        if command.capability in READ_ONLY_SKILLS:
            return BackendResult(True)

        if command.capability in VERIFY_SKILLS:
            if self.verifier is None:
                return BackendResult(
                    False,
                    "VERIFICATION_UNAVAILABLE",
                    "install a perception verifier before treating a physical task as successful",
                    confidence=0.0,
                )
            try:
                result = self.verifier(command.capability, command.target_ref, parameters)
                if not isinstance(result, BackendResult):
                    return BackendResult(
                        False,
                        "VERIFIER_INVALID_RESULT",
                        "verifier must return BackendResult",
                        confidence=0.0,
                    )
                if type(result.confidence) not in (int, float) or not math.isfinite(result.confidence) or not 0 <= result.confidence <= 1:
                    return BackendResult(False, "VERIFIER_INVALID_RESULT", "verifier confidence must be finite and between zero and one", confidence=0.0)
                return result
            except Exception as exc:  # noqa: BLE001 - verification faults must fail closed
                return BackendResult(
                    False,
                    "VERIFIER_FAILED",
                    str(exc),
                    confidence=0.0,
                )

        if command.capability == "emergency_stop":
            self.stop(command.command_id or "COMMAND_EMERGENCY_STOP")
            return BackendResult(True, "ESTOPPED")

        if command.capability == "recover_to_safe_pose":
            return BackendResult(False, "RECOVERY_POLICY_REQUIRED", "V1 requires attended local recovery; no calibrated autonomous return trajectory is installed")

        actions = parameters.get("action_chunk", [])
        if not actions:
            return BackendResult(
                False,
                "POLICY_ACTION_CHUNK_REQUIRED",
                "the laptop did not provide a bounded action_chunk",
            )
        validation = validate_action_chunk(
            actions,
            max_length=getattr(self.driver, "max_action_chunk_length", MAX_ACTION_CHUNK_LENGTH),
        )
        if validation is not None:
            return validation
        result = self.driver.execute_action_chunk(actions)
        return BackendResult(
            success=result.success,
            code=result.code,
            message=result.message,
            confidence=1.0 if result.success else 0.0,
        )

    def stop(self, reason: str) -> None:
        self.driver.stop(reason)

    def reset_stop(self, *, operator_present: bool) -> bool:
        reset = getattr(self.driver, "reset_stop", None)
        if reset is None:
            return operator_present
        return bool(reset(operator_present=operator_present))
