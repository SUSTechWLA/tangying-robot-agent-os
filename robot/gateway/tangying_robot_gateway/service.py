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
        robot_id=value.robot_id,
        catalog_revision=value.catalog_revision,
        world_revision_basis=value.world_revision_basis,
        resource_id=value.resource_id,
        fencing_token=value.fencing_token,
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
        adapter_version=value.adapter_version,
        catalog_revision=value.catalog_revision,
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
        self._execution_lock = threading.Lock()
        self._inflight: tuple[str, str] | None = None
        self._cancelled: set[str] = set()
        self._resource_grants: dict[str, tuple[str, int]] = dict(
            self.journal.resource_grants
        )

    def register_resource(self, resource_id: str, *, owner: str, token: int) -> None:
        if not resource_id or not owner or token <= 0:
            raise ValueError("resource id, owner and positive fencing token are required")
        with self._results_lock:
            current = self._resource_grants.get(resource_id)
            if current is not None and (
                token < current[1] or (token == current[1] and owner != current[0])
            ):
                raise ValueError("resource fencing token must be monotonic")
            self.journal.set_resource_grant(resource_id, owner, token)
            self._resource_grants[resource_id] = (owner, token)

    def GetRuntimeInfo(self, request, context):
        info = self.backend.capabilities()
        info.protocol_version = "1.0"
        if not info.runtime_version:
            info.runtime_version = info.software_version
        if not info.adapter_version:
            info.adapter_version = info.runtime_version or info.software_version or "v1"
        if self.safety.estop_latched:
            info.manipulation_ready = False
            info.blockers.append("EMERGENCY_STOP_LATCHED")
            for item in info.capabilities:
                if item.safety_level == "physical_motion" or item.name in PHYSICAL_SKILLS:
                    item.available = False
                    item.blockers.append("EMERGENCY_STOP_LATCHED")
        info.catalog_revision = self._catalog_revision(info)
        return runtime_info_to_proto(info)

    @staticmethod
    def _catalog_revision(info: RuntimeInfo) -> str:
        tools = []
        for item in sorted(info.capabilities, key=lambda value: value.name):
            side_effect = "read_only"
            if item.name == "emergency_stop":
                side_effect = "emergency"
            elif item.safety_level == "physical_motion":
                side_effect = "physical_atomic"
            tool = {"name": item.name}
            if item.description:
                tool["description"] = item.description
            if item.input_parameters:
                tool["inputParameters"] = sorted(item.input_parameters)
            if item.output_parameters:
                tool["outputParameters"] = sorted(item.output_parameters)
            tool["sideEffectClass"] = side_effect
            if item.safety_level:
                tool["safetyLevel"] = item.safety_level
            tool["available"] = item.available
            tools.append(tool)
        wire = json.dumps(tools, ensure_ascii=False, separators=(",", ":")).encode()
        return hashlib.sha256(wire).hexdigest()

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
        try:
            command = command_from_proto(request)
            fingerprint = self._fingerprint(command)
        except (ValueError, TypeError):
            yield self._event(request, 1, robot_pb2.SKILL_EVENT_FAILED,
                              "COMMAND_PARAMETERS_INVALID")
            return
        if command.capability == "emergency_stop":
            # Same authority as the dedicated mTLS EmergencyStop RPC: a stop
            # must preempt motion even while that motion owns admission.
            self.safety.emergency_stop("EMERGENCY_STOP_REQUESTED")
            yield self._event(command, 1, robot_pb2.SKILL_EVENT_SAFETY_STOPPED,
                              self.safety.last_stop_reason)
            return
        # Admission is per robot, including requests that reuse command_id with
        # a different key. Stop/observe RPCs never acquire this execution lock.
        if not self._execution_lock.acquire(blocking=False):
            code = "ROBOT_BUSY"
            if self._inflight and self._inflight[0] == command.idempotency_key:
                code = ("EXECUTION_OUTCOME_UNKNOWN" if self._inflight[1] == fingerprint
                        else "IDEMPOTENCY_CONFLICT")
            yield self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED, code)
            return
        self._inflight = (command.idempotency_key, fingerprint)
        try:
            yield from self._execute_command(command, fingerprint)
        except OSError:
            # If the write-ahead record or terminal flush fails, success is not
            # durable. Keep motion disabled and report an inspectable failure.
            self.safety.emergency_stop("RUNTIME_JOURNAL_UNAVAILABLE")
            yield self._event(command, 1, robot_pb2.SKILL_EVENT_SAFETY_STOPPED,
                              "RUNTIME_JOURNAL_UNAVAILABLE")
        finally:
            self.safety.complete(command.command_id)
            self._inflight = None
            self._execution_lock.release()

    def _execute_command(self, command: Command, fingerprint: str):
        if command.idempotency_key:
            persisted = self.journal.lookup(command.idempotency_key, fingerprint)
            if persisted.status == "conflict":
                yield self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED, "IDEMPOTENCY_CONFLICT")
                return
            if persisted.status in ("pending", "reconciled"):
                yield self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED,
                                  "EXECUTION_OUTCOME_UNKNOWN")
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

        identity_error = self._validate_identity(command)
        if identity_error:
            events = [self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED, identity_error)]
            with self._results_lock:
                self._results[command.idempotency_key] = (fingerprint, events)
            if command.idempotency_key:
                self.journal.record(
                    command.idempotency_key,
                    fingerprint,
                    [event.SerializeToString(deterministic=True).hex() for event in events],
                )
            yield copy.deepcopy(events[0])
            return

        decision = self.safety.start(command)
        if not decision.allowed:
            events = [self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED, decision.code)]
        else:
            self.journal.begin(command.idempotency_key, fingerprint)
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
                    # fsync and admission may outlast the original lease or
                    # deadline; check again immediately before side effects.
                    self.safety.tick()
                    if self.safety.estop_latched:
                        result = BackendResult(False, self.safety.last_stop_reason)
                    elif command.command_id in self._cancelled:
                        result = BackendResult(False, "CANCELLED")
                    else:
                        result = self.backend.execute(command)
                except Exception as exc:  # noqa: BLE001 - fail closed on any backend fault
                    result = BackendResult(False, "BACKEND_ERROR", str(exc))
            finally:
                watchdog_stop.set()
                watchdog.join(timeout=0.2)
            # Completion and cancellation admission share one decision point.
            # A cancel accepted before completion cannot turn into success.
            with self._results_lock:
                if self.safety.estop_latched:
                    event_type = robot_pb2.SKILL_EVENT_SAFETY_STOPPED
                    code = self.safety.last_stop_reason or "SAFETY_STOPPED"
                elif command.command_id in self._cancelled:
                    event_type = robot_pb2.SKILL_EVENT_CANCELLED
                    code = "CANCELLED"
                elif result.success:
                    event_type = robot_pb2.SKILL_EVENT_SUCCEEDED
                    code = result.code
                else:
                    event_type = robot_pb2.SKILL_EVENT_FAILED
                    code = result.code
                self._cancelled.discard(command.command_id)
                self.safety.complete(command.command_id)
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
        if command.idempotency_key:
            self.journal.record(
                command.idempotency_key,
                fingerprint,
                [event.SerializeToString(deterministic=True).hex() for event in events],
            )
        with self._results_lock:
            self._results[command.idempotency_key] = (fingerprint, events)
        yield from (copy.deepcopy(event) for event in events)

    def _validate_identity(self, command: Command) -> str:
        info = self.backend.capabilities()
        current_revision = self._catalog_revision(info)
        if command.robot_id and command.robot_id != info.robot_id:
            return "ROBOT_ID_MISMATCH"
        if command.catalog_revision and command.catalog_revision != current_revision:
            return "TOOL_CATALOG_STALE"
        if command.resource_id:
            if not command.catalog_revision:
                return "TOOL_CATALOG_REVISION_REQUIRED"
            if command.world_revision_basis == 0:
                return "WORLD_REVISION_REQUIRED"
            if command.fencing_token == 0:
                return "FENCING_TOKEN_REQUIRED"
            with self._results_lock:
                grant = self._resource_grants.get(command.resource_id)
                owner = command.robot_id or info.robot_id
                if grant is None:
                    return "RESOURCE_GRANT_REQUIRED"
                if grant[0] != owner or command.fencing_token != grant[1]:
                    return "FENCING_TOKEN_STALE"
        return ""

    def _watch_command(self, stop: threading.Event, lease_ms: int) -> None:
        interval = max(0.005, min(0.05, lease_ms / 10_000))
        while not stop.wait(interval):
            self.safety.tick()
            if self.safety.estop_latched:
                return

    def Cancel(self, request, context):
        # Publish cancellation before stop() can unblock execute(). Otherwise
        # a cooperative backend can finish before the terminal path sees it.
        with self._results_lock:
            if self.safety.active_command_id != request.command_id:
                return robot_pb2.CancelResult(accepted=False, state="UNKNOWN")
            self._cancelled.add(request.command_id)
        # If execution completes after admission, it sees the cancellation and
        # releases safety ownership itself. The request was still accepted.
        self.safety.cancel(request.command_id, request.reason)
        return robot_pb2.CancelResult(accepted=True, state="CANCELLED")

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
            command.robot_id,
            command.catalog_revision,
            str(command.world_revision_basis),
            command.resource_id,
            str(command.fencing_token),
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
