from __future__ import annotations

import argparse
import copy
import threading
import time
from concurrent import futures
from pathlib import Path

import grpc
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

from .backend import RobotBackend
from .safety import SafetySupervisor


class RobotGatewayService(robot_pb2_grpc.RobotGatewayServicer):
    def __init__(self, backend: RobotBackend):
        self.backend = backend
        self.safety = SafetySupervisor(backend=backend)
        self._results: dict[str, tuple[tuple[object, ...], list[robot_pb2.SkillEvent]]] = {}

    def GetCapabilities(self, request, context):
        capabilities = self.backend.capabilities()
        if self.safety.estop_latched:
            capabilities.manipulation_ready = False
            capabilities.blockers.append("EMERGENCY_STOP_LATCHED")
        return capabilities

    def Observe(self, request, context):
        yield self.backend.observe(request)

    def ExecuteSkill(self, request, context):
        yield from self.execute_for_test(request)

    def execute_for_test(self, command: robot_pb2.SkillCommand):
        if command.idempotency_key in self._results:
            fingerprint, events = self._results[command.idempotency_key]
            if fingerprint != self._fingerprint(command):
                yield self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED, "IDEMPOTENCY_CONFLICT")
                return
            yield from (copy.deepcopy(event) for event in events)
            return

        decision = self.safety.start(command)
        if not decision.allowed:
            events = [self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED, decision.code)]
        else:
            events = [
                self._event(command, 1, robot_pb2.SKILL_EVENT_ACCEPTED, "ACCEPTED", 0.0),
                self._event(command, 2, robot_pb2.SKILL_EVENT_RUNNING, "RUNNING", 0.25),
            ]
            watchdog_stop = threading.Event()
            watchdog = threading.Thread(
                target=self._watch_command,
                args=(watchdog_stop, command.lease_ms),
                name=f"lease-watchdog-{command.command_id}",
                daemon=True,
            )
            watchdog.start()
            try:
                result = self.backend.execute(command)
            finally:
                watchdog_stop.set()
                watchdog.join(timeout=0.2)
            if self.safety.estop_latched:
                event_type = robot_pb2.SKILL_EVENT_SAFETY_STOPPED
            elif result.success:
                event_type = robot_pb2.SKILL_EVENT_SUCCEEDED
            else:
                event_type = robot_pb2.SKILL_EVENT_FAILED
            events.append(
                self._event(
                    command,
                    3,
                    event_type,
                    result.code,
                    1.0,
                    result.confidence,
                    result.message,
                    result.observation_id,
                )
            )
            self.safety.complete(command.command_id)
        self._results[command.idempotency_key] = (self._fingerprint(command), events)
        yield from (copy.deepcopy(event) for event in events)

    def _watch_command(self, stop: threading.Event, lease_ms: int) -> None:
        interval = max(0.005, min(0.05, lease_ms / 10_000))
        while not stop.wait(interval):
            self.safety.tick()
            if self.safety.estop_latched:
                return

    def Cancel(self, request, context):
        accepted = self.backend.cancel(request.command_id, request.reason)
        self.safety.complete(request.command_id)
        return robot_pb2.CancelResult(accepted=accepted, state="CANCELLED" if accepted else "UNKNOWN")

    def EmergencyStop(self, request, context):
        self.safety.emergency_stop(request.reason or "REMOTE_EMERGENCY_STOP")
        return robot_pb2.EStopResult(latched=True, stopped_unix_ms=int(time.time() * 1000))

    def Pair(self, request, context):
        if not request.pairing_code:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "pairing code is required")
        return robot_pb2.PairResponse(
            robot_id=self.backend.capabilities().robot_id,
            robot_certificate=b"pairing-requires-deployment-ca",
            expires_unix_ms=int(time.time() * 1000) + 300_000,
        )

    @staticmethod
    def _event(
        command,
        sequence,
        event_type,
        code,
        progress=0.0,
        confidence=0.0,
        message="",
        observation_id="",
    ):
        return robot_pb2.SkillEvent(
            command_id=command.command_id,
            sequence=sequence,
            type=event_type,
            code=code,
            message=message or code,
            progress=progress,
            verification_confidence=confidence,
            observation_id=observation_id,
            monotonic_time_ns=time.monotonic_ns(),
        )

    @staticmethod
    def _fingerprint(command):
        return (
            command.schema_version,
            command.task_id,
            command.skill,
            command.target_ref,
            command.parameters.SerializeToString(deterministic=True),
            command.safety_profile,
        )


def start_server(
    backend: RobotBackend,
    address: str,
    *,
    server_key: Path | None = None,
    server_cert: Path | None = None,
    client_ca: Path | None = None,
    allow_insecure: bool = False,
):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    robot_pb2_grpc.add_RobotGatewayServicer_to_server(RobotGatewayService(backend), server)
    if allow_insecure:
        server.add_insecure_port(address)
    elif server_key and server_cert and client_ca:
        credentials = grpc.ssl_server_credentials(
            [(server_key.read_bytes(), server_cert.read_bytes())],
            root_certificates=client_ca.read_bytes(),
            require_client_auth=True,
        )
        server.add_secure_port(address, credentials)
    else:
        raise ValueError("mTLS credentials are required unless allow_insecure is explicit")
    server.start()
    return server


def serve(backend: RobotBackend, address: str, **security) -> None:
    server = start_server(backend, address, **security)
    server.wait_for_termination()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="0.0.0.0:50051")
    parser.parse_args()
    raise SystemExit("start the gateway through the ROS 2 xlerobot adapter launch file")


if __name__ == "__main__":
    main()
