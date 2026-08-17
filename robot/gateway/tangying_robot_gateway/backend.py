from __future__ import annotations

from dataclasses import dataclass

from tangying_robot_proto.robot.v1 import robot_pb2


@dataclass(frozen=True)
class BackendResult:
    success: bool
    code: str = "OK"
    message: str = ""
    observation_id: str = ""
    confidence: float = 1.0


class RobotBackend:
    def capabilities(self) -> robot_pb2.RobotCapabilities:
        return robot_pb2.RobotCapabilities(
            robot_id="robot-edge",
            adapter="backend",
            skills=[],
            manipulation_ready=False,
            blockers=["BACKEND_CAPABILITIES_NOT_IMPLEMENTED"],
        )

    def observe(self, request: robot_pb2.ObserveRequest) -> robot_pb2.Observation:
        raise NotImplementedError

    def execute(self, command: robot_pb2.SkillCommand) -> BackendResult:
        raise NotImplementedError

    def cancel(self, command_id: str, reason: str) -> bool:
        self.stop(reason)
        return True

    def stop(self, reason: str) -> None:
        raise NotImplementedError
