from __future__ import annotations

import argparse
import copy
import threading
import time
import uuid
from concurrent import futures
from dataclasses import dataclass, field

import grpc
from google.protobuf.json_format import MessageToDict
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

from .rendering import SceneRenderer
from .tools import ToolContext, ToolResult
from .world import TabletopWorld


@dataclass
class _ActiveCommand:
    command_id: str
    fingerprint: tuple[object, ...]
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    events: list[robot_pb2.SkillEvent] | None = None


class RobotRuntimeService(robot_pb2_grpc.RobotRuntimeServicer):
    def __init__(self, world: TabletopWorld):
        self.world = world
        self._results: dict[str, tuple[tuple[object, ...], list[robot_pb2.SkillEvent]]] = {}
        self._commands_lock = threading.Lock()
        self._active_commands: dict[str, _ActiveCommand] = {}
        self._inflight: dict[str, _ActiveCommand] = {}
        self._estopped = False
        self._closed = False
        self.renderer = SceneRenderer()
        self._last_render_anomaly: str | None = None

    def GetRuntimeInfo(self, request, context):
        with self._commands_lock:
            estopped = self._estopped
        capabilities = self._capability_infos()
        return robot_pb2.RuntimeInfo(
            robot_id="xlerobot-mujoco-tabletop",
            adapter="mujoco",
            skills=[item.name for item in capabilities],
            cameras=["sim-main"],
            manipulation_ready=not estopped,
            blockers=["EMERGENCY_STOP_LATCHED"] if estopped else [],
            software_version="0.1.0-rc.2",
            protocol_version="1.0",
            runtime_version="0.1.0-rc.2",
            capabilities=capabilities,
        )

    def Observe(self, request, context):
        observation = self._observation()
        with self._commands_lock:
            estopped = self._estopped
        anomalies = ["EMERGENCY_STOP_LATCHED"] if estopped else []
        if self._last_render_anomaly:
            anomalies.append(f"RENDERING_UNAVAILABLE: {self._last_render_anomaly}")
        observation.semantic_state.CopyFrom(
            robot_pb2.SemanticState(
                activity="EMERGENCY_STOPPED" if estopped else "IDLE",
                mode="SIMULATION",
                emergency_stopped=estopped,
                anomalies=anomalies,
            )
        )
        yield observation

    def _capability_infos(self):
        with self._commands_lock:
            physical_ready = not self._estopped
        return [
            robot_pb2.CapabilityInfo(
                name="observe_scene",
                description="Return MuJoCo scene entities.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            robot_pb2.CapabilityInfo(
                name="resolve_targets",
                description="Resolve scene references in simulation.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            robot_pb2.CapabilityInfo(
                name="plan_grasp",
                description="Plan a simulated tabletop grasp.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            robot_pb2.CapabilityInfo(
                name="manipulation.pick",
                description="Pick an object in MuJoCo.",
                available=physical_ready,
                safety_level="physical_motion",
                cancellable=True,
                default_timeout_ms=15_000,
            ),
            robot_pb2.CapabilityInfo(
                name="verify_grasp",
                description="Verify simulated grasp state.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            robot_pb2.CapabilityInfo(
                name="manipulation.place",
                description="Place the held object in MuJoCo.",
                available=physical_ready,
                safety_level="physical_motion",
                cancellable=True,
                default_timeout_ms=15_000,
            ),
            robot_pb2.CapabilityInfo(
                name="verify_placement",
                description="Verify simulated placement state.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            robot_pb2.CapabilityInfo(
                name="recover_to_safe_pose",
                description="Return the simulated arm to safe pose.",
                available=physical_ready,
                safety_level="physical_motion",
                cancellable=True,
                recoverable=True,
                default_timeout_ms=15_000,
            ),
            robot_pb2.CapabilityInfo(
                name="emergency_stop",
                description="Latch the simulated safety stop.",
                available=True,
                safety_level="physical_motion",
                default_timeout_ms=5_000,
            ),
        ]

    def ExecuteSkill(self, request, context):
        yield from self.execute_for_test(request)

    def execute_for_test(self, command: robot_pb2.SkillCommand):
        fingerprint = self._fingerprint(command)
        with self._commands_lock:
            cached = self._results.get(command.idempotency_key)
            active = self._inflight.get(command.idempotency_key)
            if cached is None and active is None:
                active = _ActiveCommand(command.command_id, fingerprint)
                self._inflight[command.idempotency_key] = active
                self._active_commands[command.command_id] = active
                owner = True
            else:
                owner = False

        if cached is not None:
            cached_fingerprint, events = cached
            if cached_fingerprint != fingerprint:
                yield self._event(
                    command,
                    1,
                    robot_pb2.SKILL_EVENT_FAILED,
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency key was already used for different command content",
                )
                return
            yield from (copy.deepcopy(event) for event in events)
            return

        if not owner:
            if active is None or active.fingerprint != fingerprint:
                yield self._event(
                    command,
                    1,
                    robot_pb2.SKILL_EVENT_FAILED,
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency key is active for different command content",
                )
                return
            active.done.wait()
            yield from (copy.deepcopy(event) for event in active.events or [])
            return

        events: list[robot_pb2.SkillEvent] = []
        error = self._validate(command)
        if error:
            events.append(
                self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED, error, error)
            )
            self._finish_command(command, active, events)
            yield copy.deepcopy(events[0])
            return

        accepted = self._event(
            command, 1, robot_pb2.SKILL_EVENT_ACCEPTED, "ACCEPTED", "accepted"
        )
        events.append(accepted)
        yield copy.deepcopy(accepted)
        running = self._event(
            command, 2, robot_pb2.SKILL_EVENT_RUNNING, "RUNNING", "running", 0.25
        )
        events.append(running)
        yield copy.deepcopy(running)

        try:
            with self.world.lock:
                result = self._dispatch(command, active.cancel_event)
                if active.cancel_event.is_set():
                    self.world.recover_to_safe_pose()
        except Exception as exc:  # noqa: BLE001 - runtime fails closed on tool faults.
            result = ToolResult(False, "TOOL_EXECUTION_ERROR", str(exc), 0.0)

        if active.cancel_event.is_set():
            event_type = robot_pb2.SKILL_EVENT_CANCELLED
            code = "CANCELLED"
        else:
            event_type = (
                robot_pb2.SKILL_EVENT_SUCCEEDED
                if result.success
                else robot_pb2.SKILL_EVENT_FAILED
            )
            code = result.code
        terminal = self._event(
            command,
            3,
            event_type,
            code,
            result.message or code,
            1.0,
            result.confidence,
        )
        events.append(terminal)
        self._finish_command(command, active, events)
        yield copy.deepcopy(terminal)

    def _finish_command(
        self,
        command: robot_pb2.SkillCommand,
        active: _ActiveCommand,
        events: list[robot_pb2.SkillEvent],
    ) -> None:
        stored = [copy.deepcopy(event) for event in events]
        with self._commands_lock:
            self._results[command.idempotency_key] = (active.fingerprint, stored)
            active.events = stored
            if self._inflight.get(command.idempotency_key) is active:
                self._inflight.pop(command.idempotency_key, None)
            if self._active_commands.get(command.command_id) is active:
                self._active_commands.pop(command.command_id, None)
            active.done.set()

    def _validate(self, command: robot_pb2.SkillCommand) -> str:
        if command.schema_version != "robot.v1":
            return "SCHEMA_VERSION_UNSUPPORTED"
        if command.deadline_unix_ms <= int(time.time() * 1000):
            return "COMMAND_EXPIRED"
        if command.lease_ms == 0:
            return "LEASE_REQUIRED"
        if not command.idempotency_key:
            return "IDEMPOTENCY_KEY_REQUIRED"
        if command.safety_profile != "simulation":
            return "SAFETY_PROFILE_REJECTED"
        with self._commands_lock:
            estopped = self._estopped
        if estopped:
            return "EMERGENCY_STOP_LATCHED"
        return ""

    def _dispatch(
        self, command: robot_pb2.SkillCommand, cancel_event: threading.Event | None = None
    ) -> ToolResult:
        parameters = MessageToDict(command.parameters, preserving_proto_field_name=True)
        return self.world.tools.execute(
            command.skill,
            ToolContext(self.world, cancel_event),
            target_ref=command.target_ref,
            parameters=parameters,
        )

    def Cancel(self, request, context):
        with self._commands_lock:
            active = self._active_commands.get(request.command_id)
            if active is not None:
                active.cancel_event.set()
        accepted = active is not None
        return robot_pb2.CancelResult(
            accepted=accepted, state="CANCELLED" if accepted else "UNKNOWN"
        )

    def EmergencyStop(self, request, context):
        with self._commands_lock:
            self._estopped = True
            active = list(self._active_commands.values())
            for command in active:
                command.cancel_event.set()
        return robot_pb2.EStopResult(latched=True, stopped_unix_ms=int(time.time() * 1000))

    def _observation(self) -> robot_pb2.Observation:
        with self.world.lock:
            observation = robot_pb2.Observation(
                observation_id=f"obs-{uuid.uuid4()}",
                wall_time_unix_ms=int(time.time() * 1000),
                monotonic_time_ns=time.monotonic_ns(),
                entities=[
                    robot_pb2.SceneEntity(
                        entity_id=entity.entity_id,
                        category=entity.category,
                        attributes=entity.attributes,
                        pose_xyz_quat=[*entity.position, 1.0, 0.0, 0.0, 0.0],
                        confidence=entity.confidence,
                        relation=entity.relation,
                    )
                    for entity in self.world.entities()
                ],
            )
            state = self.world.robot_state()
            try:
                frame = self.renderer.render(self.world.model, self.world.data)
                if frame is not None:
                    observation.compressed_image = frame.data
                    observation.image_media_type = frame.media_type
                    self._last_render_anomaly = None
                else:
                    self._last_render_anomaly = (
                        self.renderer.anomaly or "renderer returned no frame"
                    )
            except Exception as exc:  # noqa: BLE001 - state remains valid without graphics.
                self._last_render_anomaly = str(exc)
        if self._last_render_anomaly:
            state["render_anomaly"] = self._last_render_anomaly
        observation.robot_state.update(state)
        return observation

    def close(self) -> None:
        with self._commands_lock:
            if self._closed:
                return
            self._closed = True
            active = list(self._active_commands.values())
            for command in active:
                command.cancel_event.set()
        with self.world.lock:
            self.renderer.close()

    @staticmethod
    def _event(command, sequence, event_type, code, message, progress=0.0, confidence=0.0):
        return robot_pb2.SkillEvent(
            command_id=command.command_id,
            sequence=sequence,
            type=event_type,
            code=code,
            message=message,
            progress=progress,
            verification_confidence=confidence,
            monotonic_time_ns=time.monotonic_ns(),
        )

    @staticmethod
    def _fingerprint(command: robot_pb2.SkillCommand) -> tuple[object, ...]:
        return (
            command.schema_version,
            command.task_id,
            command.skill,
            command.target_ref,
            command.parameters.SerializeToString(deterministic=True),
            command.safety_profile,
        )


def serve(address: str, seed: int) -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    service = RobotRuntimeService(TabletopWorld.seeded(seed))
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(
        service, server
    )
    server.add_insecure_port(address)
    server.start()
    try:
        server.wait_for_termination()
    finally:
        service.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1:50051")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    serve(args.listen, args.seed)


if __name__ == "__main__":
    main()
