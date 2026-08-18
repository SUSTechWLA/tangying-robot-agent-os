from __future__ import annotations

from .runtime import (
    Capability,
    Command,
    Observation,
    ObservationRequest,
    Result,
    RuntimeInfo,
    SemanticState,
)

BackendResult = Result


def capability(
    name: str,
    description: str,
    *,
    available: bool,
    safety_level: str,
    blockers: list[str] | None = None,
    cancellable: bool = False,
    recoverable: bool = False,
    default_timeout_ms: int = 30_000,
    input_parameters: list[str] | None = None,
    output_parameters: list[str] | None = None,
) -> Capability:
    return Capability(
        name=name,
        description=description,
        available=available,
        blockers=blockers or [],
        cancellable=cancellable,
        recoverable=recoverable,
        default_timeout_ms=default_timeout_ms,
        safety_level=safety_level,
        input_parameters=input_parameters or [],
        output_parameters=output_parameters or [],
    )


def semantic_state(
    *,
    activity: str = "IDLE",
    mode: str = "ROBOT_RUNTIME",
    emergency_stopped: bool = False,
    anomalies: list[str] | None = None,
    last_error: str = "",
) -> SemanticState:
    return SemanticState(
        activity=activity,
        mode=mode,
        emergency_stopped=emergency_stopped,
        anomalies=anomalies or [],
        last_error=last_error,
    )


class RobotBackend:
    def capabilities(self) -> RuntimeInfo:
        return RuntimeInfo(
            robot_id="robot-edge",
            adapter="backend",
            manipulation_ready=False,
            blockers=["BACKEND_CAPABILITIES_NOT_IMPLEMENTED"],
        )

    def observe(self, request: ObservationRequest) -> Observation:
        raise NotImplementedError

    def execute(self, command: Command) -> Result:
        raise NotImplementedError

    def cancel(self, command_id: str, reason: str) -> bool:
        self.stop(reason)
        return True

    def stop(self, reason: str) -> None:
        raise NotImplementedError
