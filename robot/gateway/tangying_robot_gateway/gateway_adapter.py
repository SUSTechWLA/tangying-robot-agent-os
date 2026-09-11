"""The live gateway implementation of the ``RobotAdapter`` port.

The tool layer never talks to hardware, gRPC or ROS 2 directly. It calls this
adapter, which forwards into the existing ``PluginBackend`` — the same path the
current runtime already uses. That is the whole point: adding tools must not add
a second way to reach the robot.

Scope notes, deliberately explicit:

* **Arm motion.** An embodiment without an inverse-kinematics solver cannot
  honestly turn a Cartesian pose into joint targets. This adapter reports
  ``IK_UNAVAILABLE`` for pose requests rather than guessing, and passes joint
  requests through. Wiring MoveIt 2 or a vendor solver behind
  ``plan_arm_motion`` is the extension point; the contract and the checklist are
  in ``docs/development/arm-moveit-adapter.md``.
* **Speed limits.** There is no runtime-level actuator speed register today, so
  ``set_speed_limit`` scales the velocity envelope this adapter applies to
  motion parameters and records it. It is a software limit, not a hardware
  guarantee, and it says so in the audit log line.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from .runtime import Command, Result
from .tools.registry import (
    ArmPlan,
    HardwareHealth,
    ObservationView,
    OperationContext,
    require_finite,
)

LOGGER = logging.getLogger("tangying.tool_layer")


class GatewayRobotAdapter:
    """Adapter over an in-process ``PluginBackend``."""

    def __init__(
        self,
        backend: Any,
        *,
        robot_id: str = "",
        supports_cartesian_arm: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.robot_id = robot_id or getattr(backend, "robot_id", "")
        self.supports_cartesian_arm = supports_cartesian_arm
        self.clock = clock
        self._lock = threading.Lock()
        self._speed_limits: dict[str, float] = {}
        self._last_observation: ObservationView | None = None

    # -- commands --------------------------------------------------------

    def execute(
        self,
        skill: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        target_ref: str = "",
        context: OperationContext | None = None,
        timeout_s: float | None = None,
    ) -> Result:
        """Dispatch one runtime skill through the existing backend.

        The lease and deadline are derived from the tool's own budget, so a
        tool call is a normal, bounded command that the safety supervisor
        validates exactly like every other command.
        """

        settings = context or OperationContext(robot_id=self.robot_id)
        budget = float(timeout_s if timeout_s is not None else 30.0)
        lease_ms = max(1, int(min(budget, 60.0) * 1000))
        command = Command(
            schema_version="robot.v1",
            command_id=settings.command_id or f"tool-{skill}-{int(self.clock() * 1000)}",
            task_id=settings.task_id or "tool-layer",
            capability=skill,
            target_ref=target_ref,
            parameters=dict(self._scaled_parameters(skill, parameters or {})),
            deadline_unix_ms=int((time.time() + budget) * 1000),
            lease_ms=lease_ms,
            idempotency_key=settings.idempotency_key or settings.command_id or f"tool-{skill}",
            safety_profile=settings.safety_profile,
            approval_id=settings.approval_id,
            robot_id=settings.robot_id or self.robot_id,
            world_revision_basis=settings.world_revision_basis,
            resource_id=settings.resource_id,
            fencing_token=settings.fencing_token,
        )
        result = self.backend.execute(command)
        if not result.success:
            LOGGER.info("tool command refused: skill=%s code=%s", skill, result.code)
        return result

    def _scaled_parameters(self, skill: str, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        """Apply any recorded velocity limit to a motion request.

        Only motion skills are touched, and only by scaling an existing
        velocity-bearing field; nothing here invents a new parameter.
        """

        limits = dict(self._speed_limits)
        if not limits or skill not in {"navigation.navigate", "arm.move"}:
            return parameters
        component = "base" if skill == "navigation.navigate" else "right_arm"
        limit = limits.get(component)
        if limit is None:
            return parameters
        scaled = dict(parameters)
        if "velocity_scaling" in scaled:
            scaled["velocity_scaling"] = min(require_finite(scaled["velocity_scaling"], "velocity_scaling"), limit)
        return scaled

    # -- observation -----------------------------------------------------

    def observe(
        self,
        *,
        streams: tuple[str, ...] = ("entities", "reconstruction"),
        context: OperationContext | None = None,
    ) -> ObservationView:
        """Read the latest validated observation without commanding motion."""

        from .runtime import ObservationRequest

        try:
            observation = self.backend.observe(ObservationRequest(streams=tuple(streams), max_rate_hz=1))
        except Exception as exc:  # noqa: BLE001 - sensor faults must not look like data
            LOGGER.warning("observation failed: %s", exc)
            return ObservationView(fresh=False)

        reconstruction = _field(observation, "reconstruction", None) or {}
        semantic_state = _field(observation, "semantic_state", None)
        observed_at_ms = int(_field(observation, "wall_time_unix_ms", 0) or 0)
        view = ObservationView(
            observation_id=str(_field(observation, "observation_id", "") or ""),
            observed_at_unix_ms=observed_at_ms,
            source_id=str(_field(reconstruction, "source_id", "") or ""),
            robot_state=dict(_field(observation, "robot_state", {}) or {}),
            entities=tuple(_entity_mapping(item) for item in (_field(observation, "entities", ()) or ())),
            points=tuple(tuple(point) for point in (_field(reconstruction, "points", ()) or ())),
            fresh=_is_fresh(observed_at_ms, self.clock),
            emergency_stopped=bool(_field(semantic_state, "emergency_stopped", False)),
            anomalies=tuple(_field(semantic_state, "anomalies", ()) or ()),
            # The latest colour frame only; depth stays an implementation
            # detail of whichever consumer needs geometry.
            frame=bytes(_field(observation, "compressed_image", b"") or b""),
            frame_media_type=str(_field(observation, "image_media_type", "") or ""),
        )
        with self._lock:
            self._last_observation = view
        return view

    def cancel(self, command_id: str, reason: str) -> bool:
        try:
            return bool(self.backend.cancel(command_id, reason))
        except Exception as exc:  # noqa: BLE001 - cancellation must never raise upward
            LOGGER.warning("cancel failed for %s: %s", command_id, exc)
            return False

    def health(self) -> HardwareHealth:
        try:
            info = self.backend.capabilities()
        except Exception as exc:  # noqa: BLE001 - an unreachable runtime is a health answer
            return HardwareHealth(reachable=False, errors=(f"HARDWARE_ERROR: {exc}",))
        capabilities = getattr(info, "capabilities", ()) or ()
        return HardwareHealth(
            reachable=True,
            mode=str(getattr(info, "mode", "") or ""),
            activity=str(getattr(getattr(info, "semantic_state", None), "activity", "") or ""),
            errors=tuple(getattr(info, "blockers", ()) or ()),
            available_tools=tuple(sorted(
                item.name for item in capabilities if getattr(item, "available", False)
            )),
            battery_percent=None,  # no battery source is commissioned today
            detail={"robot_id": str(getattr(info, "robot_id", "") or self.robot_id)},
        )

    def set_speed_limit(self, component: str, limit: float) -> Result:
        self._speed_limits[component] = float(limit)
        LOGGER.info("software speed limit set: component=%s limit=%s", component, limit)
        return Result(True, "OK", "software speed limit applied", payload={"component": component,
                                                                          "limit": float(limit)})

    # -- arm planning ----------------------------------------------------

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
        """Resolve an arm request into actuator waypoints.

        Joint targets pass through. Cartesian and relative requests need an
        inverse-kinematics solver; without one this returns ``IK_UNAVAILABLE``
        instead of approximating, because a wrong joint target is a collision.
        """

        if joints is not None:
            return ArmPlan(
                ok=True, waypoints=(self._joint_waypoints(component, joints),),
                joints=tuple(float(value) for value in joints),
            )
        if not self.supports_cartesian_arm:
            requested = "target_pose" if target_pose is not None else "relative"
            return ArmPlan(
                ok=False, code="IK_UNAVAILABLE",
                message=(
                    f"this embodiment has no inverse-kinematics solver, so {requested} "
                    "cannot be converted into joint targets; use move_arm_to_joints or "
                    "commission a solver (see docs/development/arm-moveit-adapter.md)"
                ),
            )
        return ArmPlan(
            ok=False, code="IK_UNAVAILABLE",
            message="a Cartesian solver is declared but not wired; see the MoveIt 2 adapter guide",
        )

    def _joint_waypoints(self, component: str, joints: tuple[float, ...]) -> Mapping[str, float]:
        """Build ``<prefix><joint>.pos`` entries the supervisor accepts.

        The runtime only accepts keys with an allowed prefix and a ``.pos``
        suffix, so the prefix is derived from the component rather than from
        caller input.
        """

        prefix = "left_arm_" if component == "left_arm" else "right_arm_"
        profile = getattr(self.backend, "_profile", None)
        names = [joint.name for joint in getattr(profile, "joints", ()) or ()]
        if not names:
            names = [f"joint{index}" for index in range(len(joints))]
        if len(names) < len(joints):
            raise ValueError(
                f"profile declares {len(names)} joints but {len(joints)} targets were given",
            )
        return {f"{prefix}{name}.pos": float(value) for name, value in zip(names, joints, strict=False)}

    def gripper_waypoints(self, component: str, width: float) -> tuple[Mapping[str, float], ...]:
        prefix = "left_arm_" if component == "left_arm" else "right_arm_"
        return ({f"{prefix}gripper.pos": float(width)},)


def _field(value: Any, name: str, default: Any) -> Any:
    """Read a field from either a dataclass or a plain mapping.

    The runtime uses dataclasses, while a bridge or a test may hand back plain
    dicts; accepting both keeps the adapter from silently reading nothing.
    """

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _entity_mapping(entity: Any) -> Mapping[str, Any]:
    if isinstance(entity, Mapping):
        return dict(entity)
    attributes = getattr(entity, "attributes", None)
    return {
        "entityId": str(getattr(entity, "entity_id", "") or ""),
        "category": str(getattr(entity, "category", "") or ""),
        "attributes": dict(attributes or {}),
        "pose": list(getattr(entity, "pose", ()) or ()),
        "relation": str(getattr(entity, "relation", "") or ""),
        "confidence": float(getattr(entity, "confidence", 0.0) or 0.0),
    }


def _is_fresh(observed_at_unix_ms: int, clock: Callable[[], float]) -> bool:
    """Freshness is judged from the observation's own timestamp.

    A missing timestamp is not fresh: a tool must not treat "unknown age" as
    "current", because that would let a stale capture justify motion.
    """

    if not observed_at_unix_ms:
        return False
    now_ms = int(time.time() * 1000)
    age_ms = now_ms - int(observed_at_unix_ms)
    return 0 <= age_ms <= 5000
