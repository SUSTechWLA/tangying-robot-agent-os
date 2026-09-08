"""Transport-neutral Robot Runtime domain contracts.

These types are shared by safety and hardware backends.  Protobuf conversion
belongs in ``service.py`` so robot execution remains independent of gRPC, ROS 2
messages, and vendor SDK message types.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Capability:
    name: str
    description: str = ""
    available: bool = False
    blockers: list[str] = field(default_factory=list)
    cancellable: bool = False
    recoverable: bool = False
    default_timeout_ms: int = 30_000
    safety_level: str = "read_only"
    input_parameters: list[str] = field(default_factory=list)
    output_parameters: list[str] = field(default_factory=list)


@dataclass
class RuntimeInfo:
    robot_id: str
    adapter: str
    manipulation_ready: bool
    blockers: list[str] = field(default_factory=list)
    software_version: str = ""
    protocol_version: str = ""
    runtime_version: str = ""
    adapter_version: str = ""
    catalog_revision: str = ""
    capabilities: list[Capability] = field(default_factory=list)
    robot_profile: dict[str, Any] | None = None

    @property
    def skills(self) -> list[str]:
        return [item.name for item in self.capabilities]


@dataclass
class SemanticState:
    activity: str = "IDLE"
    mode: str = "ROBOT_RUNTIME"
    emergency_stopped: bool = False
    anomalies: list[str] = field(default_factory=list)
    last_error: str = ""


@dataclass
class SceneEntity:
    entity_id: str
    category: str
    attributes: dict[str, str] = field(default_factory=dict)
    pose_xyz_quat: list[float] = field(default_factory=list)
    confidence: float = 0.0
    relation: str = ""


@dataclass(frozen=True)
class ObservationRequest:
    streams: tuple[str, ...] = ()
    max_rate_hz: int = 1


@dataclass
class Observation:
    observation_id: str
    wall_time_unix_ms: int
    monotonic_time_ns: int
    semantic_state: SemanticState = field(default_factory=SemanticState)
    robot_state: dict[str, Any] = field(default_factory=dict)
    entities: list[SceneEntity] = field(default_factory=list)
    reconstruction: dict[str, Any] = field(default_factory=dict)
    compressed_image: bytes = b""
    image_media_type: str = ""
    compressed_depth_image: bytes = b""
    depth_image_media_type: str = ""


@dataclass(frozen=True)
class ReconstructionCapture:
    """One provider transaction containing reconstruction and its capture images.

    Providers construct this from one RgbdFrame. Fetching another frame to fill
    an image field would violate this contract even if both frames are fresh.
    Legacy reconstruction-only providers may continue returning dictionaries.
    """

    reconstruction: dict[str, Any]
    compressed_image: bytes = b""
    image_media_type: str = ""
    compressed_depth_image: bytes = b""
    depth_image_media_type: str = ""


@dataclass(frozen=True)
class Command:
    schema_version: str
    command_id: str
    task_id: str
    capability: str
    target_ref: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    deadline_unix_ms: int = 0
    lease_ms: int = 0
    idempotency_key: str = ""
    safety_profile: str = ""
    approval_id: str = ""
    robot_id: str = ""
    catalog_revision: str = ""
    world_revision_basis: int = 0
    resource_id: str = ""
    fencing_token: int = 0


@dataclass(frozen=True)
class Result:
    success: bool
    code: str = "OK"
    message: str = ""
    observation_id: str = ""
    confidence: float = 1.0


class InvalidToolResult(ValueError):
    """A dispatched tool did not return a trustworthy terminal result."""


def validate_result(value: Result) -> Result:
    if not isinstance(value, Result):
        raise InvalidToolResult("tool must return a Result")
    # Read adapter-owned fields once, then return a fresh frozen domain value.
    # A custom subclass or getter cannot change the terminal result between
    # validation, journal serialization and event publication.
    success, code, message = value.success, value.code, value.message
    observation_id, confidence = value.observation_id, value.confidence
    if (type(success) is not bool or type(code) is not str or not code
            or type(message) is not str or type(observation_id) is not str
            or type(confidence) not in (int, float)
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise InvalidToolResult("tool must return Result with boolean success, text fields and finite confidence in [0,1]")
    return Result(success, code, message, observation_id, confidence)
