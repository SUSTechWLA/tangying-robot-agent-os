from __future__ import annotations

import argparse
import copy
import hashlib
import json
import threading
import time
from concurrent import futures
from pathlib import Path

import grpc
from google.protobuf.json_format import MessageToDict, ParseDict
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

from .backend import BackendResult, RobotBackend, semantic_state
from .journal import RuntimeJournal
from .runtime import Command, Observation, ObservationRequest, RuntimeInfo, SemanticState
from .safety import PHYSICAL_SKILLS, SafetySupervisor


def command_from_proto(value: robot_pb2.SkillCommand) -> Command:
    return Command(
        schema_version=value.schema_version,
        command_id=value.command_id,
        task_id=value.task_id,
        capability=value.skill,
        target_ref=value.target_ref,
        parameters=MessageToDict(value.parameters) if value.parameters else {},
        deadline_unix_ms=value.deadline_unix_ms,
        lease_ms=value.lease_ms,
        idempotency_key=value.idempotency_key,
        safety_profile=value.safety_profile,
        approval_id=value.approval_id,
    )


def runtime_info_to_proto(value: RuntimeInfo) -> robot_pb2.RuntimeInfo:
    result = robot_pb2.RuntimeInfo(
        robot_id=value.robot_id,
        adapter=value.adapter,
        skills=value.skills,
        manipulation_ready=value.manipulation_ready,
        blockers=value.blockers,
        software_version=value.software_version,
        protocol_version=value.protocol_version,
        runtime_version=value.runtime_version,
    )
    for source in value.capabilities:
        target = result.capabilities.add()
        target.name = source.name
        target.description = source.description
        target.available = source.available
        target.blockers.extend(source.blockers)
        target.cancellable = source.cancellable
        target.recoverable = source.recoverable
        target.default_timeout_ms = source.default_timeout_ms
        target.safety_level = source.safety_level
        target.input_parameters.extend(source.input_parameters)
        target.output_parameters.extend(source.output_parameters)
    return result


def observation_from_proto(value: robot_pb2.ObserveRequest) -> ObservationRequest:
    return ObservationRequest(streams=tuple(value.streams), max_rate_hz=value.max_rate_hz)


def semantic_state_to_proto(value: SemanticState) -> robot_pb2.SemanticState:
    return robot_pb2.SemanticState(
        activity=value.activity,
        mode=value.mode,
        emergency_stopped=value.emergency_stopped,
        anomalies=value.anomalies,
        last_error=value.last_error,
    )


def observation_to_proto(value: Observation) -> robot_pb2.Observation:
    result = robot_pb2.Observation(
        observation_id=value.observation_id,
        wall_time_unix_ms=value.wall_time_unix_ms,
        monotonic_time_ns=value.monotonic_time_ns,
        semantic_state=semantic_state_to_proto(value.semantic_state),
    )
    if value.robot_state:
        ParseDict(value.robot_state, result.robot_state)
    for source in value.entities:
        target = result.entities.add()
        target.entity_id = source.entity_id
        target.category = source.category
        target.attributes.update(source.attributes)
        target.pose_xyz_quat.extend(source.pose_xyz_quat)
        target.confidence = source.confidence
        target.relation = source.relation
    return result


class RobotRuntimeService(robot_pb2_grpc.RobotRuntimeServicer):
    def __init__(self, backend: RobotBackend, journal: RuntimeJournal | None = None):
        self.backend = backend
        self.journal = journal or RuntimeJournal(None)
        self.safety = SafetySupervisor(backend=backend, journal=self.journal)
        self._results: dict[str, tuple[str, list[robot_pb2.SkillEvent]]] = {}
        self._results_lock = threading.Lock()
        self._cancelled: set[str] = set()

    def GetRuntimeInfo(self, request, context):
        info = self.backend.capabilities()
        info.protocol_version = "1.0"
        if not info.runtime_version:
            info.runtime_version = info.software_version
        if self.safety.estop_latched:
            info.manipulation_ready = False
            info.blockers.append("EMERGENCY_STOP_LATCHED")
            for item in info.capabilities:
                if item.safety_level == "physical_motion" or item.name in PHYSICAL_SKILLS:
                    item.available = False
                    item.blockers.append("EMERGENCY_STOP_LATCHED")
        return runtime_info_to_proto(info)

    def Observe(self, request, context):
        observation = self.backend.observe(observation_from_proto(request))
        runtime_state = self._semantic_state()
        backend_state = observation.semantic_state
        for anomaly in backend_state.anomalies:
            if anomaly not in runtime_state.anomalies:
                runtime_state.anomalies.append(anomaly)
        if runtime_state.last_error == "" and backend_state.last_error:
            runtime_state.last_error = backend_state.last_error
        observation.semantic_state = runtime_state
        yield observation_to_proto(observation)

    def ExecuteSkill(self, request, context):
        yield from self.execute_for_test(request)

    def execute_for_test(self, request: robot_pb2.SkillCommand):
        command = command_from_proto(request)
        fingerprint = self._fingerprint(command)
        if command.idempotency_key:
            persisted = self.journal.lookup(command.idempotency_key, fingerprint)
            if persisted.status == "conflict":
                yield self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED, "IDEMPOTENCY_CONFLICT")
                return
            if persisted.status == "replay":
                for encoded in persisted.events:
                    event = robot_pb2.SkillEvent()
                    event.ParseFromString(bytes.fromhex(encoded))
                    yield event
                return
        with self._results_lock:
            cached = self._results.get(command.idempotency_key)
        if cached is not None:
            fingerprint, events = cached
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
                try:
                    result = self.backend.execute(command)
                except Exception as exc:  # noqa: BLE001 - fail closed on any backend fault
                    result = BackendResult(False, "BACKEND_ERROR", str(exc))
            finally:
                watchdog_stop.set()
                watchdog.join(timeout=0.2)
            if self.safety.estop_latched:
                event_type = robot_pb2.SKILL_EVENT_SAFETY_STOPPED
                code = self.safety.last_stop_reason or "SAFETY_STOPPED"
            elif command.command_id in self._cancelled:
                event_type = robot_pb2.SKILL_EVENT_CANCELLED
                code = "CANCELLED"
                self._cancelled.discard(command.command_id)
            elif result.success:
                event_type = robot_pb2.SKILL_EVENT_SUCCEEDED
                code = result.code
            else:
                event_type = robot_pb2.SKILL_EVENT_FAILED
                code = result.code
            events.append(
                self._event(
                    command,
                    3,
                    event_type,
                    code,
                    1.0,
                    result.confidence,
                    result.message or code,
                    result.observation_id,
                )
            )
            self.safety.complete(command.command_id)
        with self._results_lock:
            self._results[command.idempotency_key] = (fingerprint, events)
        if command.idempotency_key:
            self.journal.record(
                command.idempotency_key,
                fingerprint,
                [event.SerializeToString(deterministic=True).hex() for event in events],
            )
        yield from (copy.deepcopy(event) for event in events)

    def _watch_command(self, stop: threading.Event, lease_ms: int) -> None:
        interval = max(0.005, min(0.05, lease_ms / 10_000))
        while not stop.wait(interval):
            self.safety.tick()
            if self.safety.estop_latched:
                return

    def Cancel(self, request, context):
        accepted = self.safety.cancel(request.command_id, request.reason)
        if accepted:
            self._cancelled.add(request.command_id)
        return robot_pb2.CancelResult(
            accepted=accepted, state="CANCELLED" if accepted else "UNKNOWN"
        )

    def EmergencyStop(self, request, context):
        self.safety.emergency_stop(request.reason or "REMOTE_EMERGENCY_STOP")
        return robot_pb2.EStopResult(latched=True, stopped_unix_ms=int(time.time() * 1000))

    def _semantic_state(self) -> SemanticState:
        if self.safety.estop_latched:
            return semantic_state(
                activity="EMERGENCY_STOPPED",
                emergency_stopped=True,
                anomalies=[self.safety.last_stop_reason or "EMERGENCY_STOP_LATCHED"],
                last_error=self.safety.last_stop_reason,
            )
        if self.safety.active_command_id:
            return semantic_state(activity="EXECUTING")
        return semantic_state(activity="IDLE")

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
        digest = hashlib.sha256()
        for value in (
            command.schema_version,
            command.task_id,
            command.capability,
            command.target_ref,
            command.safety_profile,
        ):
            digest.update(value.encode())
            digest.update(b"\0")
        digest.update(
            json.dumps(
                command.parameters,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        )
        return digest.hexdigest()


def start_server(
    backend: RobotBackend,
    address: str,
    *,
    journal: RuntimeJournal | None = None,
    server_key: Path | None = None,
    server_cert: Path | None = None,
    client_ca: Path | None = None,
    allow_insecure: bool = False,
):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(
        RobotRuntimeService(backend, journal=journal), server
    )
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
