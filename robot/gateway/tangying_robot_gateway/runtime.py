"""Transport-neutral Robot Runtime domain contracts.

These types are shared by safety and hardware backends.  Protobuf conversion
belongs in ``service.py`` so robot execution remains independent of gRPC, ROS 2
messages, and vendor SDK message types.
"""

from __future__ import annotations

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
    capabilities: list[Capability] = field(default_factory=list)

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


@dataclass(frozen=True)
class Result:
    success: bool
    code: str = "OK"
    message: str = ""
    observation_id: str = ""
    confidence: float = 1.0
