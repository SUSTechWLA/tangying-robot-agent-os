"""Unified tool contract for the robot tool layer.

This module is the single place that defines what a robot tool *is*. It does
not own hardware, transport or safety: those already exist in
``runtime.Result`` (wire result), ``safety.SafetySupervisor`` (the only
veto) and ``service.py`` (the single execution RPC). The tool layer sits in
front of them and is responsible for exactly three things:

1. describing a capability in a form an LLM can call (JSON Schema + metadata),
2. turning semantic arguments into the parameter shape the runtime accepts,
3. normalising whatever comes back into one structured ``ToolResult``.

Design notes:
- ``ToolResult`` is an LLM-facing projection. The runtime keeps its own
  fine-grained codes, because ``core/closedloop`` classifies them to decide
  whether a retry is safe. ``ToolError`` is the standard set the LLM reasons
  over, and the mapping between the two is explicit and tested.
- ``recoverable`` is not inferred from the error string. It comes from the
  same closed-loop classification the Go Agent uses, so the two sides cannot
  disagree about whether a failure may be retried.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .runtime import Capability, Result

# --------------------------------------------------------------------------
# Standard error codes (LLM-facing)
# --------------------------------------------------------------------------


class ToolError(StrEnum):
    """The standard failure vocabulary exposed to an LLM."""

    TIMEOUT = "TIMEOUT"
    NOT_FOUND = "NOT_FOUND"
    UNREACHABLE = "UNREACHABLE"
    COLLISION_RISK = "COLLISION_RISK"
    HARDWARE_ERROR = "HARDWARE_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_PARAM = "INVALID_PARAM"
    BUSY = "BUSY"
    CANCELLED = "CANCELLED"
    SAFETY_STOP = "SAFETY_STOP"


class RecoveryClass(StrEnum):
    """Mirror of ``core/closedloop.Class``, the recovery decision authority."""

    TRANSIENT = "TRANSIENT"
    PERCEPTION = "PERCEPTION"
    PLANNING = "PLANNING"
    PERMISSION = "PERMISSION"
    RESOURCE = "RESOURCE"
    VALIDATION = "VALIDATION"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    FATAL = "FATAL"


# Runtime code -> (standard code, recovery class).
#
# The keys are the codes this repository actually emits; they are the same
# table core/closedloop classifies. Order is irrelevant here because the
# lookup is exact, but every entry must stay in sync with the Go side.
# tests/tool_layer/test_tool_contract.py asserts the two agree.
_RUNTIME_CODE_TABLE: dict[str, tuple[ToolError, RecoveryClass]] = {
    # Outcome unknown: the action may have reached the hardware.
    "EXECUTION_OUTCOME_UNKNOWN": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "PHYSICAL_OUTCOME_UNKNOWN": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "RUNTIME_JOURNAL_UNAVAILABLE": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "BACKEND_STOP_FAILED": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "SERVICE_SHUTDOWN": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    # Approval, profile, catalog and arming state.
    "APPROVAL_REQUIRED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "SAFETY_PROFILE_REJECTED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "TOOL_CATALOG_REVISION_REQUIRED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "TOOL_CATALOG_STALE": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "SKILL_NOT_ALLOWED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "CAPABILITY_UNAVAILABLE": (ToolError.HARDWARE_ERROR, RecoveryClass.PERMISSION),
    "ROBOT_NOT_ARMED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "MOBILE_BASE_DISABLED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "EMERGENCY_STOP_LATCHED": (ToolError.SAFETY_STOP, RecoveryClass.PERMISSION),
    "EMERGENCY_STOPPED": (ToolError.SAFETY_STOP, RecoveryClass.PERMISSION),
    "RECOVERY_POLICY_REQUIRED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "CALIBRATION_REQUIRED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "ROBOT_PROFILE_INVALID": (ToolError.HARDWARE_ERROR, RecoveryClass.PERMISSION),
    # Ownership, leases and duplicate suppression.
    "RESOURCE_GRANT_REQUIRED": (ToolError.BUSY, RecoveryClass.RESOURCE),
    "FENCING_TOKEN_REQUIRED": (ToolError.BUSY, RecoveryClass.RESOURCE),
    "FENCING_TOKEN_STALE": (ToolError.BUSY, RecoveryClass.RESOURCE),
    "RESOURCE_OWNER_MISMATCH": (ToolError.BUSY, RecoveryClass.RESOURCE),
    "RESOURCE_LEASE_EXPIRED": (ToolError.BUSY, RecoveryClass.RESOURCE),
    "ROBOT_BUSY": (ToolError.BUSY, RecoveryClass.RESOURCE),
    "ROBOT_COMMISSIONING_ACTIVE": (ToolError.BUSY, RecoveryClass.RESOURCE),
    "IDEMPOTENCY_CONFLICT": (ToolError.BUSY, RecoveryClass.RESOURCE),
    "TARGET_REFERENCE_CONFLICT": (ToolError.BUSY, RecoveryClass.RESOURCE),
    # Perception and localisation: re-observe before acting again.
    "OBJECT_NOT_FOUND": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "DESTINATION_NOT_FOUND": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "LOCATION_NOT_FOUND": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    # A grasp that could not be detected is a grasp whose outcome is unestablished,
    # not a perception failure: calling it PERCEPTION tells an operator to
    # re-observe and try again, which retries a grasp that may have succeeded.
    # core/closedloop already has it — and its GRASP_NOT_OBSERVED sibling — as the
    # unknown case, and this side was the drifted one.
    "GRASP_NOT_DETECTED": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    # The XLeRobot driver's own blocker codes. They reach the agent through the real
    # robot's fault report, which did not exist until the backend was given a fault
    # ledger — so these codes had never been classified anywhere, and would have been
    # reported as an unknown physical outcome over a missing source directory.
    "UPSTREAM_NOT_FOUND": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "SERIAL_PORTS_UNAVAILABLE": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "XLEROBOT_LEROBOT_INTEGRATION_MISSING": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "MAX_RELATIVE_TARGET_INVALID": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "MAX_ACTION_CHUNK_LENGTH_INVALID": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "NAV_OBSERVATION_INVALID": (ToolError.HARDWARE_ERROR, RecoveryClass.PERCEPTION),
    "NAV_OBSERVATION_LOST": (ToolError.HARDWARE_ERROR, RecoveryClass.PERCEPTION),
    "NAV_POSE_INVALID": (ToolError.HARDWARE_ERROR, RecoveryClass.PERCEPTION),
    "NAV_LOCALIZATION_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.PERCEPTION),
    "NAV_PATH_OUT_OF_VIEW": (ToolError.UNREACHABLE, RecoveryClass.PERCEPTION),
    "NAV_MAP_NOT_READY": (ToolError.UNREACHABLE, RecoveryClass.PERCEPTION),
    "RECONSTRUCTION_INVALID": (ToolError.HARDWARE_ERROR, RecoveryClass.PERCEPTION),
    "ENTITY_PROVIDER_REQUIRED": (ToolError.HARDWARE_ERROR, RecoveryClass.PERCEPTION),
    "NAV_ARMS_STOWED": (ToolError.BUSY, RecoveryClass.PERCEPTION),
    # Planning: the same command cannot succeed by repeating it.
    "TARGET_UNREACHABLE": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "NAV_WORKSPACE_LIMIT": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "NAV_DEADLINE_EXCEEDED": (ToolError.TIMEOUT, RecoveryClass.PLANNING),
    "NAV_STEP_LIMIT": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "NAV_KINEMATICS_INVALID": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "XLEROBOT_MAX_RELATIVE_TARGET": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "XLEROBOT_MAX_ACTION_CHUNK_LENGTH": (ToolError.INVALID_PARAM, RecoveryClass.PLANNING),
    "POLICY_ACTION_CHUNK_REQUIRED": (ToolError.HARDWARE_ERROR, RecoveryClass.PLANNING),
    "COLLISION_RISK": (ToolError.COLLISION_RISK, RecoveryClass.PLANNING),
    "WORKSPACE_LIMIT": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    # Malformed or unauthorised command shape.
    "TOOL_PARAMETERS_INVALID": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "INVALID_PARAMETERS": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "COMMAND_PARAMETERS_INVALID": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "SCHEMA_VERSION_UNSUPPORTED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "LEASE_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "LEASE_TOO_LONG": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "IDEMPOTENCY_KEY_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "TASK_ID_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "COMMAND_ID_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "ROBOT_ID_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "ROBOT_ID_MISMATCH": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "OBJECT_ID_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "WORLD_REVISION_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "ACTION_VALUE_OUT_OF_RANGE": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "GRIPPER_VALUE_OUT_OF_RANGE": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "ACTION_CHUNK_MALFORMED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "VERIFIER_INVALID_RESULT": (ToolError.HARDWARE_ERROR, RecoveryClass.VALIDATION),
    "VERIFIER_REQUIRED": (ToolError.HARDWARE_ERROR, RecoveryClass.VALIDATION),
    # Infrastructure hiccups that can be retried in place.
    "NAV_VELOCITY_STALE": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "NAV_COMMAND_STALE": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "NAV_STATUS_TIMEOUT": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "NAV_STATUS_INVALID": (ToolError.HARDWARE_ERROR, RecoveryClass.TRANSIENT),
    "NAV_BRIDGE_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "COMMAND_LEASE_EXPIRED": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "COMMAND_EXPIRED": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "BACKEND_ERROR": (ToolError.HARDWARE_ERROR, RecoveryClass.TRANSIENT),
    # Operator-level outcomes.
    "CANCELLED": (ToolError.CANCELLED, RecoveryClass.FATAL),
    "VERIFICATION_FAILED": (ToolError.HARDWARE_ERROR, RecoveryClass.FATAL),
    "VERIFICATION_CONFIDENCE_LOW": (ToolError.HARDWARE_ERROR, RecoveryClass.FATAL),
    "VERIFICATION_UNAVAILABLE": (ToolError.HARDWARE_ERROR, RecoveryClass.FATAL),
    # An unexpected tool exception: the physical result is undetermined, because
    # the tool may have reached the hardware before it threw. FATAL said "needs a
    # person", which is true but omits the part that matters first — reconcile the
    # world before doing anything else.
    "TOOL_EXECUTION_ERROR": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    # The rest of the runtime's codes, added because they were missing.
    #
    # The recovery class here is NOT a second opinion: core/closedloop is the
    # authority, and each class below was taken from it. What had happened is that
    # this mirror drifted — 87 codes the runtime emits were absent, and an absent
    # code does not fail loudly. `classify` falls through to
    # HARDWARE_ERROR/UNKNOWN_OUTCOME, so the robot told an operator "result
    # unknown, do not retry" for a failure the agent had called PERCEPTION and
    # advised re-observing; and it handed an LLM the words "hardware error" for a
    # navigation planning failure. The cross-language agreement test exists to
    # catch exactly this and had been failing, unnoticed, because it lives in a
    # suite that needs the project interpreter to run.
    #
    # The standard word (left column) is derived, not curated: a short list of
    # rules reads the code's own name — a *CONTACT or *OBSTACLE is a collision
    # risk, a *_REQUIRED or *_MISMATCH is an invalid parameter, a *DEADLINE is a
    # timeout — and anything the rules do not name takes its class's default word.
    # That is deliberate: guessing 87 operator-facing words by hand would have
    # produced a table nobody could review, and the word matters less than the
    # class. A later pass may refine individual words; the classes must not change
    # without changing Go first.
    "ARM_NOT_FOUND": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "CALIBRATION_CHANGED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "CAMERA_LAYOUT_MISMATCH": (ToolError.INVALID_PARAM, RecoveryClass.TRANSIENT),
    "CAMERA_PARENT_MISMATCH": (ToolError.INVALID_PARAM, RecoveryClass.TRANSIENT),
    "CONNECTION_REFUSED": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "CONTINUATION_FRAME_MISMATCH": (ToolError.INVALID_PARAM, RecoveryClass.PERCEPTION),
    "CONTINUATION_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "DEPTH_STARVED": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "DESTINATION_ID_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "DESTINATION_NOT_ALLOWED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "EMPTY_NAVIGATION_ROUTE": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "EOF_LEASE_EXPIRED": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "EXECUTION_PORTS_MISSING": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "EXECUTION_STORE_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "GOAL_NOT_CLEAR": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "GRASP_FAILED": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "GRASP_NOT_OBSERVED": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "GRASP_NOT_REACHED": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "GRIPPER_OCCUPIED": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "GROUNDING_ABSENT": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "HELD_OBJECT_NOT_FOUND": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "INVALID_ARGUMENT": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "INVALID_POSE": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "LOCALIZATION_NOT_CLEAR": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "LOCALIZATION_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.PERCEPTION),
    "MAPPING_ACTIVE": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "MAP_TOO_LARGE": (ToolError.INVALID_PARAM, RecoveryClass.TRANSIENT),
    "MOTION_STOPPING": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "MOTOR_LAYOUT_MISMATCH": (ToolError.INVALID_PARAM, RecoveryClass.TRANSIENT),
    "NAV_ARRIVAL_OBSERVATION_INVALID": (ToolError.INVALID_PARAM, RecoveryClass.PERCEPTION),
    "NAV_CONTROLLER_CLOSED": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "NAV_DEPTH_UNKNOWN": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "NAV_FORWARD_ONLY": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "NAV_GOAL_NOT_REACHED": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "NAV_MODEL_COLLISION": (ToolError.COLLISION_RISK, RecoveryClass.PLANNING),
    "NAV_OBSTACLE_OBSERVED": (ToolError.COLLISION_RISK, RecoveryClass.PERCEPTION),
    "NAV_ODOM_GOAL_NOT_REACHED": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "NAV_PATH_OCCLUDED": (ToolError.COLLISION_RISK, RecoveryClass.PERCEPTION),
    "NAV_PLANAR_ONLY": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "NAV_ROTATION_LIMIT": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "NAV_ROTATION_POSITION_MISMATCH": (ToolError.INVALID_PARAM, RecoveryClass.PLANNING),
    "NAV_ROUTE_LIMIT": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
    "NAV_STOW_CONTACT": (ToolError.COLLISION_RISK, RecoveryClass.UNKNOWN_OUTCOME),
    "NAV_STOW_CONTACT_PREDICTED": (ToolError.COLLISION_RISK, RecoveryClass.PLANNING),
    "NAV_STOW_ENVELOPE_MISMATCH": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "NAV_STOW_JOINT_LIMIT": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "NAV_STOW_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.PERCEPTION),
    "NAV_STOW_REQUIRES_EMPTY_GRIPPERS": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "NAV_STOW_START_UNSUPPORTED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "NAV_VELOCITY_INVALID": (ToolError.INVALID_PARAM, RecoveryClass.PERCEPTION),
    "NOT_HOLDING_OBJECT": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "NO_KNOWN_PATH": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "OBJECT_NOT_AVAILABLE": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "PATH_CLEARANCE_INSUFFICIENT": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "PLACEMENT_NOT_OBSERVED": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "PLACEMENT_NOT_VERIFIED": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "PLACE_NOT_REACHED": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "POLICY_COMPATIBILITY_OR_ACTION_REJECTED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "POLICY_OBSERVATION_NOT_READY": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "POLICY_PROVIDER_TIMEOUT": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "POLICY_PROVIDER_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "PRE_POSITION_NO_CLEAR_POSE": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "PRE_POSITION_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.PERCEPTION),
    "RECEIPT_CAPACITY": (ToolError.BUSY, RecoveryClass.TRANSIENT),
    "RELEASE_CLEARANCE_NOT_REACHED": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "REQUEST_ID_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "REQUEST_IN_PROGRESS": (ToolError.BUSY, RecoveryClass.TRANSIENT),
    "REQUEST_TOO_LARGE": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "RESOURCE_NOT_OWNED": (ToolError.BUSY, RecoveryClass.PERMISSION),
    "REVISION_CONFLICT": (ToolError.BUSY, RecoveryClass.PERMISSION),
    "REVISION_REQUIRED": (ToolError.INVALID_PARAM, RecoveryClass.PERMISSION),
    "RPC_DEADLINE_EXCEEDED": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "RPC_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "RUNTIME_NOT_READY": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "SAFETY_STOPPED": (ToolError.SAFETY_STOP, RecoveryClass.PERMISSION),
    "SCAN_NOT_READY": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "SCAN_TOO_SMALL": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "SEMANTIC_AMBIGUOUS": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "SERVICE_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "STALE_CAPTURE": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "SURVEY_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "TARGET_AMBIGUOUS": (ToolError.NOT_FOUND, RecoveryClass.PERCEPTION),
    "TRANSPORT_ERROR": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "UNCLASSIFIED_EXECUTION_FAILURE": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "UNVERIFIED_WORLD_MUTATION": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "WORKFLOW_FAULT": (ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME),
    "WORLD_NOT_READY": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
}

# Codes this tool layer raises itself, before any runtime call.
TOOL_LAYER_CODE_TABLE: dict[str, tuple[ToolError, RecoveryClass]] = {
    "TOOL_TIMEOUT": (ToolError.TIMEOUT, RecoveryClass.TRANSIENT),
    "TOOL_NOT_REGISTERED": (ToolError.NOT_FOUND, RecoveryClass.VALIDATION),
    "TOOL_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.TRANSIENT),
    "TOOL_INVALID_PARAM": (ToolError.INVALID_PARAM, RecoveryClass.VALIDATION),
    "TOOL_COMPOSITE_ABORTED": (ToolError.HARDWARE_ERROR, RecoveryClass.PERCEPTION),
    "TOOL_CANCELLED": (ToolError.CANCELLED, RecoveryClass.FATAL),
    "SAFETY_STOP_ACTIVE": (ToolError.SAFETY_STOP, RecoveryClass.PERMISSION),
    "PERMISSION_DENIED": (ToolError.PERMISSION_DENIED, RecoveryClass.PERMISSION),
    "COMPONENT_NOT_FOUND": (ToolError.NOT_FOUND, RecoveryClass.VALIDATION),
    # The arm request needs an inverse-kinematics solver this embodiment does
    # not have. It is a planning answer, not a hardware fault: the model should
    # change the request, not retry it or ask for service.
    "IK_UNAVAILABLE": (ToolError.UNREACHABLE, RecoveryClass.PLANNING),
}

RECOVERABLE_CLASSES = frozenset({RecoveryClass.TRANSIENT, RecoveryClass.PERCEPTION})

# Standard codes are reachable from their own tool-level recoverability:
# TIMEOUT and NOT_FOUND are retryable, COLLISION_RISK means replan rather than
# retry, and a requested permission or a latched stop is not ours to retry.
_STANDARD_CODE_RECOVERY: dict[ToolError, RecoveryClass] = {
    ToolError.TIMEOUT: RecoveryClass.TRANSIENT,
    ToolError.NOT_FOUND: RecoveryClass.PERCEPTION,
    ToolError.UNREACHABLE: RecoveryClass.PLANNING,
    ToolError.COLLISION_RISK: RecoveryClass.PLANNING,
    ToolError.HARDWARE_ERROR: RecoveryClass.UNKNOWN_OUTCOME,
    ToolError.PERMISSION_DENIED: RecoveryClass.PERMISSION,
    ToolError.INVALID_PARAM: RecoveryClass.VALIDATION,
    ToolError.BUSY: RecoveryClass.RESOURCE,
    ToolError.CANCELLED: RecoveryClass.FATAL,
    ToolError.SAFETY_STOP: RecoveryClass.PERMISSION,
}


def standard_error(code: str) -> tuple[ToolError, RecoveryClass, bool]:
    """Project a runtime or tool-layer code onto the LLM vocabulary.

    Returns the standard code, its recovery class and whether an automatic
    retry is permitted. A code that is already a standard ``ToolError`` passes
    through unchanged, so the function is idempotent. Anything unrecognised is
    a hardware error with an unknown outcome: the caller must not assume a
    retry is safe.
    """

    if isinstance(code, ToolError):
        recovery = _STANDARD_CODE_RECOVERY[code]
        return code, recovery, recovery in RECOVERABLE_CLASSES
    if not isinstance(code, str) or not code.strip():
        return ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME, False
    normalized = code.strip().upper()
    entry = _RUNTIME_CODE_TABLE.get(normalized) or TOOL_LAYER_CODE_TABLE.get(normalized)
    if entry is None:
        try:
            standard = ToolError(normalized)
        except ValueError:
            return ToolError.HARDWARE_ERROR, RecoveryClass.UNKNOWN_OUTCOME, False
        recovery = _STANDARD_CODE_RECOVERY[standard]
        return standard, recovery, recovery in RECOVERABLE_CLASSES
    error, recovery = entry
    return error, recovery, recovery in RECOVERABLE_CLASSES


def known_runtime_codes() -> frozenset[str]:
    """Codes the projection recognises; used to keep Go and Python in sync."""

    return frozenset(_RUNTIME_CODE_TABLE) | frozenset(TOOL_LAYER_CODE_TABLE)


def recovery_class_map() -> dict[str, str]:
    """Runtime code -> recovery class, for cross-language agreement tests."""

    combined = {**_RUNTIME_CODE_TABLE, **TOOL_LAYER_CODE_TABLE}
    return {code: recovery.value for code, (_, recovery) in combined.items()}


# --------------------------------------------------------------------------
# Tool result
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """One structured tool outcome, safe to hand to an LLM as-is.

    ``frozen`` matters: the result crosses the runtime boundary and is stored
    as evidence, so nothing downstream may mutate it after validation.
    """

    success: bool
    error_code: str | None = None
    error_message: str | None = None
    recoverable: bool = False
    data: Mapping[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.success) is not bool:
            raise ValueError("ToolResult.success must be a bool")
        if self.success and self.error_code:
            raise ValueError("a successful ToolResult must not carry an error code")
        if not self.success and not self.error_code:
            raise ValueError("a failed ToolResult must carry an error code")
        if self.error_code is not None and self.error_code not in set(ToolError):
            raise ValueError(f"unknown tool error code {self.error_code!r}")

    def with_call_id(self, tool_call_id: str | None) -> ToolResult:
        if not tool_call_id or tool_call_id == self.tool_call_id:
            return self
        return ToolResult(
            success=self.success, error_code=self.error_code, error_message=self.error_message,
            recoverable=self.recoverable, data=self.data, timestamp=self.timestamp,
            tool_call_id=tool_call_id,
        )

    def with_data(self, **extra: Any) -> ToolResult:
        """Return a copy carrying additional structured detail.

        A result is frozen so a stored verdict cannot be rewritten in place, so
        metadata is added by producing a new value rather than mutating one an
        observer may already hold.
        """

        return ToolResult(
            success=self.success, error_code=self.error_code, error_message=self.error_message,
            recoverable=self.recoverable, data={**dict(self.data), **extra},
            timestamp=self.timestamp, tool_call_id=self.tool_call_id,
        )

    @property
    def summary(self) -> str:
        """One line for a log or a model-facing transcript."""

        if self.success:
            return "ok" + (f": {dict(self.data)}" if self.data else "")
        return f"{self.error_code}: {self.error_message or ''}".strip()

    @classmethod
    def ok(cls, **data: Any) -> ToolResult:
        return cls(success=True, data=data)

    @classmethod
    def failure(
        cls,
        code: ToolError | str,
        message: str = "",
        *,
        recoverable: bool | None = None,
        **data: Any,
    ) -> ToolResult:
        """Build a failure, deriving ``recoverable`` from the code by default.

        Callers may override only when they have stronger information than the
        code carries; passing ``recoverable`` explicitly is a deliberate act.
        """

        error, _, retryable = standard_error(str(code))
        return cls(
            success=False, error_code=error.value, error_message=message or error.value,
            recoverable=retryable if recoverable is None else recoverable, data=data,
        )

    @classmethod
    def from_runtime(cls, result: Result, **data: Any) -> ToolResult:
        """Normalise a wire ``Result``.

        ``Result.success`` is a trigger to look at the world, not proof the
        world changed; the closure gate on the Agent side stays authoritative
        for completion. This projection only classifies the outcome.
        """

        if result.success:
            return cls(success=True, data=data)
        # The adapter may attach structured detail (why a plan was refused,
        # the latch state of a stop); the caller-supplied data wins on
        # conflicts so a tool can label its own failure.
        merged = dict(result.payload)
        merged.update(data)
        merged.setdefault("runtime_code", result.code)
        if result.observation_id:
            merged.setdefault("observation_id", result.observation_id)
        error, _, retryable = standard_error(result.code)
        return cls(
            success=False, error_code=error.value,
            error_message=result.message or result.code,
            recoverable=retryable, data=merged,
        )

    @classmethod
    def from_exception(cls, exc: BaseException, *, context: str = "") -> ToolResult:
        """Convert an unexpected fault into a failure the caller can act on."""

        if isinstance(exc, TimeoutError):
            return cls.failure(ToolError.TIMEOUT, str(exc) or context or "timed out")
        if isinstance(exc, PermissionError):
            return cls.failure(ToolError.PERMISSION_DENIED, str(exc) or context)
        if isinstance(exc, (TypeError, ValueError, KeyError)):
            return cls.failure(ToolError.INVALID_PARAM, f"{type(exc).__name__}: {exc}")
        return cls.failure(ToolError.HARDWARE_ERROR, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# Tool definition
# --------------------------------------------------------------------------


class SafetyLevel(int):
    """Safety classification, ordered so a supervisor can compare numerically."""

    QUERY = 0
    LOW_SPEED_MOTION = 1
    NORMAL_MOTION = 2
    CONTACT = 3
    SAFETY = 4


@dataclass(frozen=True)
class RobotTool:
    """A capability an LLM may call.

    A tool carries no hardware state and performs no I/O of its own: ``handler``
    receives validated arguments and returns a ``ToolResult``. Anything that
    touches the robot goes through the runtime, which keeps the safety
    supervisor as the single veto point.
    """

    name: str
    description: str
    parameters_schema: Mapping[str, Any]
    returns_schema: Mapping[str, Any]
    safety_level: int
    timeout_s: float
    distributed_node: str
    idempotent: bool
    handler: Callable[..., ToolResult]
    mutates_world: bool = False
    # "primary" tools are advertised to an LLM; "fallback" tools exist for
    # diagnostics and coordinate-level recovery but are not offered by default.
    llm_visibility: str = "primary"

    def __post_init__(self) -> None:
        if "." in self.name:
            raise ValueError(f"tool name {self.name!r} must not contain a dot")
        if not self.name or self.name != self.name.lower() or not self.name.replace("_", "").isalnum():
            raise ValueError(f"tool name {self.name!r} must be lower snake case")
        if not 0 <= self.safety_level <= SafetyLevel.SAFETY:
            raise ValueError(f"tool {self.name} has invalid safety level {self.safety_level}")
        if self.timeout_s <= 0:
            raise ValueError(f"tool {self.name} requires a positive timeout")
        if not self.description.strip():
            raise ValueError(f"tool {self.name} requires a description")
        if not self.distributed_node.strip():
            raise ValueError(f"tool {self.name} requires a target node or capability group")
        if self.llm_visibility not in ("primary", "fallback"):
            raise ValueError(f"tool {self.name} has invalid llm_visibility {self.llm_visibility!r}")

    def execute(self, **kwargs: Any) -> ToolResult:
        """Run the tool. Argument validation is the executor's job."""

        try:
            result = self.handler(**kwargs)
        except Exception as exc:  # noqa: BLE001 - the contract must never leak a raw fault
            return ToolResult.from_exception(exc, context=f"{self.name} failed")
        if not isinstance(result, ToolResult):
            return ToolResult.failure(
                ToolError.HARDWARE_ERROR, f"{self.name} returned {type(result).__name__}, not ToolResult",
            )
        return result

    def capability_info(self, *, available: bool = True, blockers: Iterable[str] = ()) -> Capability:
        """Describe this tool through the runtime capability contract.

        Reusing ``Capability`` means the existing registration, health and
        ``mutates_world`` closure machinery applies without a second registry.
        """

        properties = self.parameters_schema.get("properties") or {}
        return Capability(
            name=self.name,
            description=self.description,
            available=available,
            blockers=list(blockers),
            cancellable=not self.idempotent,
            recoverable=not self.idempotent,
            default_timeout_ms=int(self.timeout_s * 1000),
            safety_level=_safety_level_name(self.safety_level),
            input_parameters=sorted(properties),
            output_parameters=sorted(self.returns_schema.get("properties") or {}),
            mutates_world=self.mutates_world,
        )

    def openai_schema(self) -> dict[str, Any]:
        """OpenAI / LangChain function-calling entry for this tool."""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _plain(self.parameters_schema),
            },
        }


def _safety_level_name(level: int) -> str:
    if level >= SafetyLevel.SAFETY:
        return "safety_critical"
    if level >= SafetyLevel.CONTACT:
        return "physical_contact"
    if level >= SafetyLevel.LOW_SPEED_MOTION:
        return "physical_motion"
    return "read_only"


def _plain(value: Any) -> Any:
    """Deep-copy a schema into plain JSON types, preserving tuple-free order."""

    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


class ToolRegistry:
    """The set of tools a node offers, discoverable by name.

    Registration is the only mutation; lookups never fail on a missing tool
    with an exception, because "this node does not offer that tool" is a
    normal distributed answer, not a programming error.
    """

    def __init__(self, tools: Iterable[RobotTool] = ()) -> None:
        self._tools: dict[str, RobotTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: RobotTool) -> None:
        if not isinstance(tool, RobotTool):
            raise TypeError("only RobotTool instances can be registered")
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def replace(self, tool: RobotTool) -> None:
        """Re-register under an existing name, for adapter reloads."""

        self._tools[tool.name] = tool

    def get(self, name: str) -> RobotTool | None:
        return self._tools.get(name)

    def require(self, name: str) -> RobotTool:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(name)
        return tool

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def select(
        self,
        *,
        namespace: str | None = None,
        max_safety_level: int | None = None,
        llm_only: bool = False,
    ) -> tuple[RobotTool, ...]:
        """Discovery filter used by health reports and schema export."""

        selected = []
        for tool in self._tools.values():
            if namespace is not None and not tool.name.startswith(f"{namespace}_"):
                continue
            if max_safety_level is not None and tool.safety_level > max_safety_level:
                continue
            if llm_only and tool.llm_visibility != "primary":
                continue
            selected.append(tool)
        return tuple(sorted(selected, key=lambda item: item.name))

    def openai_tools(self, *, llm_only: bool = True) -> list[dict[str, Any]]:
        return [tool.openai_schema() for tool in self.select(llm_only=llm_only)]

    def capability_infos(self, *, unavailable: Mapping[str, Iterable[str]] | None = None):
        """Capability view for the runtime, with per-tool health blockers."""

        blocked = {name: tuple(reasons) for name, reasons in (unavailable or {}).items()}
        return [
            tool.capability_info(available=tool.name not in blocked, blockers=blocked.get(tool.name, ()))
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
        ]
