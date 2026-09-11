"""The port between tool implementations and the Robot Runtime.

The tool layer must not invent capability names. ``safety.ALLOWED_SKILLS``,
``xlerobot_adapter._validate`` and the ROS 2 action server each hold an
explicit allow-list, and ``core/closedloop`` refuses to complete a write
without fresh evidence. So rather than adding ``navigation.stop`` as a new
skill, this module separates two kinds of call:

* **operations** — existing runtime skills (``navigation.navigate``,
  ``manipulation.pick``, ...) reached through the one execution RPC, which
  means the safety supervisor, the journal and the closure gate all still
  apply;
* **out-of-band requests** — things the runtime already supports but that are
  not skills at all: reading the latest observation, cancelling an in-flight
  goal, reporting hardware health. These bypass the skill queue by design,
  which is exactly how ``Cancel`` and ``EmergencyStop`` already work today.

A tool implementation therefore never needs the runtime to learn a new verb.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..runtime import Result

#: Safety profile used when a tool call does not name one. Simulation and the
#: desktop reference share this; a real deployment passes its own.
DEFAULT_SAFETY_PROFILE = "simulation"


@dataclass(frozen=True)
class OperationContext:
    """Everything the runtime needs to accept one command.

    These are the fields ``SafetySupervisor.evaluate`` checks. The tool layer
    supplies real values so a tool call is a first-class, auditable command
    rather than a privileged shortcut.
    """

    robot_id: str = ""
    task_id: str = ""
    command_id: str = ""
    idempotency_key: str = ""
    approval_id: str = ""
    safety_profile: str = DEFAULT_SAFETY_PROFILE
    world_revision_basis: int = 0
    resource_id: str = ""
    fencing_token: int = 0
    cancel_event: Any = None

    def for_operation(self, skill: str, *, timeout_s: float, suffix: str = "") -> OperationContext:
        """Derive a deterministic identity for one step of a tool call."""

        stem = self.command_id or f"tool-{uuid.uuid4().hex}"
        command_id = f"{stem}/{skill}{suffix}"
        return OperationContext(
            robot_id=self.robot_id, task_id=self.task_id or stem, command_id=command_id,
            idempotency_key=command_id, approval_id=self.approval_id,
            safety_profile=self.safety_profile, world_revision_basis=self.world_revision_basis,
            resource_id=self.resource_id, fencing_token=self.fencing_token,
            cancel_event=self.cancel_event,
        )


@dataclass(frozen=True)
class ObservationView:
    """A read-only view of the latest validated observation."""

    observation_id: str = ""
    observed_at_unix_ms: int = 0
    source_id: str = ""
    robot_state: Mapping[str, Any] = field(default_factory=dict)
    entities: tuple[Mapping[str, Any], ...] = ()
    points: tuple[tuple[float, ...], ...] = ()
    fresh: bool = False
    emergency_stopped: bool = False
    anomalies: tuple[str, ...] = ()
    #: Latest camera frame, when the observation included one. Kept opaque so
    #: the tool layer never decodes or re-encodes sensor data.
    frame: bytes = b""
    frame_media_type: str = ""

    def entity(self, entity_id: str) -> Mapping[str, Any] | None:
        for item in self.entities:
            if item.get("entityId") == entity_id or item.get("entity_id") == entity_id:
                return item
        return None


@dataclass(frozen=True)
class HardwareHealth:
    """Non-skill health report used by ``get_robot_status``."""

    reachable: bool
    mode: str = ""
    activity: str = ""
    errors: tuple[str, ...] = ()
    available_tools: tuple[str, ...] = ()
    battery_percent: float | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ArmPlan:
    """Joint waypoints an arm command should send, in the runtime's own keys.

    The tool layer never invents joint or actuator names. ``SafetySupervisor``
    accepts only keys with an allowed prefix and a ``.pos`` suffix, and those
    names differ per embodiment, so the adapter that owns the profile is the
    only component that can build them. ``waypoints`` is therefore a list of
    ready-to-send action dicts; ``final_joints`` and ``warnings`` are for the
    caller's report, not for constructing the command.
    """

    ok: bool
    waypoints: tuple[Mapping[str, float], ...] = ()
    joints: tuple[float, ...] = ()
    final_pose: tuple[float, ...] = ()
    code: str = "OK"
    message: str = ""
    warnings: tuple[str, ...] = ()


@runtime_checkable
class RobotAdapter(Protocol):
    """What a tool needs from the robot, and nothing more.

    Implementations exist for the live gateway (``plugin_backend``), for an
    in-process simulator, and for tests. A test double only has to implement
    these methods.
    """

    def execute(
        self,
        skill: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        target_ref: str = "",
        context: OperationContext | None = None,
        timeout_s: float | None = None,
    ) -> Result:
        """Dispatch one existing runtime skill and return its wire result."""

    def observe(
        self,
        *,
        streams: tuple[str, ...] = ("entities", "reconstruction"),
        context: OperationContext | None = None,
    ) -> ObservationView:
        """Read the latest validated observation without commanding motion."""

    def cancel(self, command_id: str, reason: str) -> bool:
        """Request cancellation of an in-flight skill; never a safety stop."""

    def health(self) -> HardwareHealth:
        """Report reachability and advertised capabilities."""

    def set_speed_limit(self, component: str, limit: float) -> Result:
        """Apply a bounded speed limit to one component."""

    def plan_arm_motion(
        self,
        *,
        component: str,
        joints: tuple[float, ...] | None = None,
        target_pose: tuple[float, ...] | None = None,
        relative: tuple[float, float, float] | None = None,
        frame_id: str = "base_link",
        velocity_scaling: float = 0.5,
    ) -> ArmPlan:
        """Resolve an arm request into sendable waypoints.

        Cartesian requests need inverse kinematics, which is embodiment
        specific and lives behind this port (MoveIt 2 or the vendor SDK).
        Passing joints through is a direct mapping. Exactly one of ``joints``,
        ``target_pose`` or ``relative`` is supplied.
        """

    def gripper_waypoints(self, component: str, width: float) -> tuple[Mapping[str, float], ...]:
        """Build the actuator keys that close or open one gripper to ``width``."""


def require_finite(value: Any, name: str, *, default: float | None = None) -> float:
    """Validate a numeric tool argument, refusing NaN and infinity."""

    import math

    if value is None:
        if default is None:
            raise ValueError(f"{name} is required")
        return default
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def require_fraction(value: Any, name: str, *, default: float) -> float:
    number = require_finite(value, name, default=default)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return number
