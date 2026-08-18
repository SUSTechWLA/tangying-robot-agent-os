from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.protobuf.json_format import MessageToDict, ParseDict
from tangying_robot_proto.robot.v1 import robot_pb2

from .backend import BackendResult, RobotBackend, capability

READ_ONLY_SKILLS = {"observe_scene", "resolve_targets", "plan_grasp"}
VERIFY_SKILLS = {"verify_grasp", "verify_placement"}
MAX_ACTION_CHUNK_LENGTH = 64
MAX_ABSOLUTE_ACTION_VALUE = 100.0
ALLOWED_ACTION_PREFIXES = ("left_arm_", "right_arm_", "head_")
MOBILE_BASE_KEYS = {"x.vel", "theta.vel"}


def validate_action_chunk(actions: Any, max_length: int = MAX_ACTION_CHUNK_LENGTH) -> BackendResult | None:
    if not isinstance(actions, list) or not actions:
        return BackendResult(False, "POLICY_ACTION_CHUNK_REQUIRED")
    if len(actions) > max_length:
        return BackendResult(
            False,
            "ACTION_CHUNK_TOO_LONG",
            f"{len(actions)} > {max_length}",
        )
    for action in actions:
        if not isinstance(action, dict):
            return BackendResult(False, "ACTION_CHUNK_MALFORMED")
        for key, value in action.items():
            if key in MOBILE_BASE_KEYS:
                return BackendResult(False, "MOBILE_BASE_DISABLED", key)
            if not key.endswith(".pos") or not key.startswith(ALLOWED_ACTION_PREFIXES):
                return BackendResult(False, "ACTION_KEY_REJECTED", str(key))
            try:
                number = float(value)
            except (TypeError, ValueError):
                return BackendResult(False, "ACTION_VALUE_NOT_NUMERIC", str(key))
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
    XLeRobotDriver directly in this process. Perception and policy stay
    external, pluggable functions so the first hardware release can fail closed
    until a real scene-entity provider, policy and verifier are installed.
    """

    def __init__(
        self,
        driver: Any,
        *,
        entity_provider: Callable[[], list[dict[str, Any]]] | None = None,
        policy: Callable[[robot_pb2.SkillCommand, dict[str, Any]], list[dict[str, float]]] | None = None,
        verifier: Callable[[str, str, dict[str, Any]], BackendResult] | None = None,
    ):
        self.driver = driver
        self.entity_provider = entity_provider
        self.policy = policy
        self.verifier = verifier

    @classmethod
    def from_env(
        cls,
        *,
        entity_provider: Callable[[], list[dict[str, Any]]] | None = None,
        policy: Callable[[robot_pb2.SkillCommand, dict[str, Any]], list[dict[str, float]]] | None = None,
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
        return cls(driver, entity_provider=entity_provider, policy=policy, verifier=verifier)

    def capabilities(self) -> robot_pb2.RobotCapabilities:
        driver_capabilities = self.driver.capabilities()
        driver_ready = driver_capabilities.manipulation_ready
        entity_ready = self.entity_provider is not None
        verify_ready = self.verifier is not None
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
                available=driver_ready,
                safety_level="physical_motion",
                blockers=[] if driver_ready else list(driver_capabilities.blockers),
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
                available=driver_ready,
                safety_level="physical_motion",
                blockers=[] if driver_ready else list(driver_capabilities.blockers),
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
                "recover_to_safe_pose",
                "Move the arm back to the calibrated safe pose.",
                available=driver_ready,
                safety_level="physical_motion",
                blockers=[] if driver_ready else list(driver_capabilities.blockers),
                cancellable=True,
                recoverable=True,
                default_timeout_ms=15_000,
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
        return robot_pb2.RobotCapabilities(
            robot_id="xlerobot-edge-direct",
            adapter="xlerobot_direct",
            skills=[item.name for item in capabilities],
            manipulation_ready=driver_ready,
            blockers=list(driver_capabilities.blockers),
            software_version="0.2.0-dev",
            capabilities=capabilities,
        )

    def observe(self, request: robot_pb2.ObserveRequest) -> robot_pb2.Observation:
        anomalies: list[str] = []
        last_error = ""
        entities: list[dict[str, Any]] = []
        if self.entity_provider is not None:
            try:
                entities = list(self.entity_provider())
                if not isinstance(entities, list):
                    raise TypeError("entity provider must return a list")
            except Exception as exc:  # noqa: BLE001 - perception faults must fail closed
                anomalies.append("ENTITY_PROVIDER_FAILED")
                last_error = str(exc)
                entities = []
        observation = robot_pb2.Observation(
            observation_id=f"direct-{time.monotonic_ns()}",
            wall_time_unix_ms=int(time.time() * 1000),
            monotonic_time_ns=time.monotonic_ns(),
            semantic_state=robot_pb2.SemanticState(
                anomalies=anomalies,
                last_error=last_error,
            ),
        )
        for entity in entities:
            item = observation.entities.add()
            item.entity_id = str(entity.get("entity_id", ""))
            item.category = str(entity.get("category", ""))
            item.attributes.update({str(k): str(v) for k, v in entity.get("attributes", {}).items()})
            item.pose_xyz_quat.extend([float(value) for value in entity.get("pose_xyz_quat", [])])
            item.confidence = float(entity.get("confidence", 0.0))
            item.relation = str(entity.get("relation", ""))
        if hasattr(self.driver, "observation"):
            try:
                raw_state = self.driver.observation()
                if isinstance(raw_state, dict):
                    ParseDict(raw_state, observation.robot_state)
            except Exception as exc:  # noqa: BLE001 - display fault is non-fatal but visible
                anomalies.append("OBSERVATION_FAILED")
                if last_error:
                    last_error += "; "
                last_error += str(exc)
                observation.semantic_state.CopyFrom(
                    robot_pb2.SemanticState(anomalies=anomalies, last_error=last_error)
                )
        return observation

    def execute(self, command: robot_pb2.SkillCommand) -> BackendResult:
        parameters = MessageToDict(command.parameters) if command.parameters else {}

        if command.skill in READ_ONLY_SKILLS:
            return BackendResult(True)

        if command.skill in VERIFY_SKILLS:
            if self.verifier is None:
                return BackendResult(
                    False,
                    "VERIFICATION_UNAVAILABLE",
                    "install a perception verifier before treating a physical task as successful",
                    confidence=0.0,
                )
            try:
                result = self.verifier(command.skill, command.target_ref, parameters)
                if not isinstance(result, BackendResult):
                    return BackendResult(
                        False,
                        "VERIFIER_INVALID_RESULT",
                        "verifier must return BackendResult",
                        confidence=0.0,
                    )
                return result
            except Exception as exc:  # noqa: BLE001 - verification faults must fail closed
                return BackendResult(
                    False,
                    "VERIFIER_FAILED",
                    str(exc),
                    confidence=0.0,
                )

        if command.skill == "emergency_stop":
            self.stop(command.command_id or "COMMAND_EMERGENCY_STOP")
            return BackendResult(True, "ESTOPPED")

        actions = parameters.get("action_chunk", [])
        if not actions and self.policy is not None:
            try:
                actions = self.policy(command, parameters)
            except Exception as exc:  # noqa: BLE001 - policy faults must fail closed
                return BackendResult(False, "POLICY_PROVIDER_FAILED", str(exc))
        if not actions:
            return BackendResult(
                False,
                "POLICY_ACTION_CHUNK_REQUIRED",
                "no action_chunk was provided and no policy provider is configured",
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
