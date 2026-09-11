from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import threading
import time
import uuid
from concurrent import futures
from dataclasses import dataclass, field

import grpc
from google.protobuf.json_format import MessageToDict
from tangying_robot_gateway.contracts import mutates_world
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
    safety_stop_reason: str = ""
    committed: bool = False


class RobotRuntimeService(robot_pb2_grpc.RobotRuntimeServicer):
    def __init__(
        self,
        world: TabletopWorld,
        robot_id: str = "xlerobot-mujoco-tabletop",
        *,
        adapter: str = "mujoco",
        cameras: tuple[str, ...] = ("sim-main",),
        allow_monotonic_grant_adoption: bool = False,
        render_width: int = 320,
        render_height: int = 240,
    ):
        self.world = world
        self._robot_id = robot_id
        self._adapter = adapter
        self._cameras = cameras
        self._allow_monotonic_grant_adoption = allow_monotonic_grant_adoption
        self._render_width = render_width
        self._render_height = render_height
        self._results: dict[str, tuple[tuple[object, ...], list[robot_pb2.SkillEvent]]] = {}
        self._commands_lock = threading.Lock()
        self._active_commands: dict[str, _ActiveCommand] = {}
        self._inflight: dict[str, _ActiveCommand] = {}
        self._estopped = False
        self._estop_reason = ""
        self._closed = False
        self._adapter_version = "0.5.0"
        self._resource_grants: dict[str, tuple[str, int]] = {}
        self.renderer = SceneRenderer(width=render_width, height=render_height)
        self._last_render_anomaly: str | None = None

    def GetRuntimeInfo(self, request, context):
        with self._commands_lock:
            estopped = self._estopped
        capabilities = self._capability_infos()
        return robot_pb2.RuntimeInfo(
            robot_id=self._robot_id,
            adapter=self._adapter,
            skills=[item.name for item in capabilities],
            cameras=list(self._cameras),
            manipulation_ready=not estopped,
            blockers=["EMERGENCY_STOP_LATCHED"] if estopped else [],
            software_version="0.5.0",
            protocol_version="1.0",
            runtime_version=self._adapter_version,
            capabilities=capabilities,
            catalog_revision=self._catalog_revision(capabilities),
            adapter_version=self._adapter_version,
        )

    def _catalog_revision(self, capabilities=None) -> str:
        capabilities = capabilities or self._capability_infos()
        tools = []
        for capability in sorted(capabilities, key=lambda item: item.name):
            side_effect = "read_only"
            if capability.name == "emergency_stop":
                side_effect = "emergency"
            elif capability.safety_level == "physical_motion":
                side_effect = "physical_atomic"
            tool = {
                "name": capability.name,
                "description": capability.description,
            }
            if capability.input_parameters:
                tool["inputParameters"] = sorted(capability.input_parameters)
            if capability.output_parameters:
                tool["outputParameters"] = sorted(capability.output_parameters)
            tool["sideEffectClass"] = side_effect
            tool["safetyLevel"] = capability.safety_level
            tool["available"] = capability.available
            tools.append(tool)
        wire = json.dumps(tools, ensure_ascii=False, separators=(",", ":")).encode()
        return hashlib.sha256(wire).hexdigest()

    def register_resource(self, resource_id: str, *, owner: str, token: int) -> None:
        if not resource_id or not owner or token <= 0:
            raise ValueError("resource id, owner and positive fencing token are required")
        with self._commands_lock:
            self._resource_grants[resource_id] = (owner, token)

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

        def info(name, description, *, safety_level, available=True, **kwargs):
            # mutates_world comes from the shared contract so the simulator, the
            # adapter runtime and the Agent's closure gate cannot disagree about
            # which tools require fresh post-command evidence.
            return robot_pb2.CapabilityInfo(
                name=name, description=description, available=available,
                safety_level=safety_level, mutates_world=mutates_world(name), **kwargs,
            )

        return [
            info(
                "observe_scene",
                description="Return MuJoCo scene entities.",
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            info(
                "resolve_targets",
                description="Resolve scene references in simulation.",
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            info(
                "plan_grasp",
                description="Plan a simulated tabletop grasp.",
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            info(
                "manipulation.pick",
                description="Pick an object in MuJoCo.",
                available=physical_ready,
                safety_level="physical_motion",
                cancellable=True,
                default_timeout_ms=15_000,
            ),
            info(
                "verify_grasp",
                description="Verify simulated grasp state.",
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            info(
                "verify_arrival",
                description="Verify the base reached a room goal using a fresh RGB-D/pose capture.",
                safety_level="read_only",
                default_timeout_ms=5_000,
                input_parameters=["goalPose"],
            ),
            info(
                "manipulation.place",
                description="Place the held object in MuJoCo.",
                available=physical_ready,
                safety_level="physical_motion",
                cancellable=True,
                default_timeout_ms=15_000,
            ),
            info(
                "verify_placement",
                description="Verify simulated placement state.",
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            info(
                "recover_to_safe_pose",
                description="Return the simulated arm to safe pose.",
                available=physical_ready,
                safety_level="physical_motion",
                cancellable=True,
                recoverable=True,
                default_timeout_ms=15_000,
            ),
            info(
                "emergency_stop",
                description="Latch the simulated safety stop.",
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
                result = self._dispatch(command, active)
                if active.cancel_event.is_set():
                    self._recover_cancelled_command(command, active)
        except Exception as exc:  # noqa: BLE001 - runtime fails closed on tool faults.
            result = ToolResult(False, "TOOL_EXECUTION_ERROR", str(exc), 0.0)

        if active.safety_stop_reason:
            event_type = robot_pb2.SKILL_EVENT_SAFETY_STOPPED
            code = "EMERGENCY_STOP_LATCHED"
            message = active.safety_stop_reason
        elif active.cancel_event.is_set():
            event_type = robot_pb2.SKILL_EVENT_CANCELLED
            code = "CANCELLED"
            message = result.message or code
        else:
            event_type = (
                robot_pb2.SKILL_EVENT_SUCCEEDED
                if result.success
                else robot_pb2.SKILL_EVENT_FAILED
            )
            code = result.code
            message = result.message or code
        terminal = self._event(
            command,
            3,
            event_type,
            code,
            message,
            1.0,
            result.confidence,
        )
        events.append(terminal)
        self._finish_command(command, active, events)
        yield copy.deepcopy(terminal)

    def _recover_cancelled_command(self, command, active):
        if command.skill == "manipulation.place" and not active.committed:
            self.world.recover_cancelled_place()
        else:
            self.world.recover_to_safe_pose()

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
        if command.robot_id and command.robot_id != self._robot_id:
            return "ROBOT_ID_MISMATCH"
        current_catalog_revision = self._catalog_revision()
        if command.catalog_revision and command.catalog_revision != current_catalog_revision:
            return "TOOL_CATALOG_STALE"
        if command.resource_id:
            if not command.catalog_revision:
                return "TOOL_CATALOG_REVISION_REQUIRED"
            if command.fencing_token == 0:
                return "FENCING_TOKEN_REQUIRED"
            expected_grant = (
                command.robot_id or self._robot_id,
                command.fencing_token,
            )
            adopted = False
            with self._commands_lock:
                grant = self._resource_grants.get(command.resource_id)
                if (
                    grant != expected_grant
                    and self._allow_monotonic_grant_adoption
                    and grant is not None
                    and grant[0] == expected_grant[0]
                    and expected_grant[1] > grant[1]
                ):
                    self._resource_grants[command.resource_id] = expected_grant
                    grant = expected_grant
                    adopted = True
            if grant != expected_grant:
                return "FENCING_TOKEN_STALE"
            if adopted:
                adopter = getattr(self.world, "adopt_fencing_token", None)
                if adopter is not None:
                    adopter(command.fencing_token)
        if command.safety_profile != "simulation":
            return "SAFETY_PROFILE_REJECTED"
        with self._commands_lock:
            estopped = self._estopped
        if estopped:
            return "EMERGENCY_STOP_LATCHED"
        return ""

    def _dispatch(
        self, command: robot_pb2.SkillCommand, active: _ActiveCommand | None = None
    ) -> ToolResult:
        parameters = MessageToDict(command.parameters, preserving_proto_field_name=True)
        return self.world.tools.execute(
            command.skill,
            ToolContext(
                self.world,
                active.cancel_event if active is not None else None,
                (lambda: self._try_commit(active)) if active is not None else None,
            ),
            target_ref=command.target_ref,
            parameters=parameters,
        )

    def _try_commit(self, active: _ActiveCommand) -> bool:
        with self._commands_lock:
            if active.cancel_event.is_set() or active.safety_stop_reason:
                return False
            active.committed = True
            return True

    def Cancel(self, request, context):
        with self._commands_lock:
            active = self._active_commands.get(request.command_id)
            if active is not None and not active.committed:
                active.cancel_event.set()
                accepted = True
            else:
                accepted = False
        return robot_pb2.CancelResult(
            accepted=accepted, state="CANCELLED" if accepted else "UNKNOWN"
        )

    def EmergencyStop(self, request, context):
        with self._commands_lock:
            self._estopped = True
            self._estop_reason = request.reason or "EMERGENCY_STOP_LATCHED"
            active = list(self._active_commands.values())
            for command in active:
                command.safety_stop_reason = self._estop_reason
                command.cancel_event.set()
        return robot_pb2.EStopResult(latched=True, stopped_unix_ms=int(time.time() * 1000))

    def _observation(self) -> robot_pb2.Observation:
        # Lock-free reads of the rolling snapshot: skill execution holds the
        # world lock, but observation must never block on it or the live
        # god view and harness telemetry freeze during every pick/place.
        entities = self.world.cached_entities()
        if entities is None:
            entities = self.world.entities()
        state = self.world.cached_robot_state()
        if state is None:
            state = self.world.robot_state()
        render_data = self.world.cached_render_data()
        if render_data is None:
            with self.world.lock:
                # MuJoCo 3.3.x does not expose mj_copyData in Python, while
                # MjData's copy protocol provides the same independent state
                # snapshot and also works on newer bindings.
                render_data = copy.copy(self.world.data)
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
                for entity in entities
            ],
        )
        try:
            frame = self.renderer.render(self.world.model, render_data)
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
            command.robot_id,
            command.catalog_revision,
            command.world_revision_basis,
            command.resource_id,
            command.fencing_token,
        )


def serve(
    address: str,
    seed: int,
    robot_id: str = "xlerobot-mujoco-tabletop",
    xml_path: str | None = None,
    human_speed: float = 0.0,
    perception: str = "ground-truth",
    scene: str = "tabletop",
    calibration_root: str | None = None,
) -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    if perception == "rgbd":
        from .rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld
        world = RgbdTabletopWorld.seeded(seed,xml_path=xml_path,human_speed=human_speed,robot_id=robot_id,scene=scene)
        service = RgbdRuntimeService(world,robot_id=robot_id,calibration_root=calibration_root)
    elif perception == "ground-truth":
        if scene != "tabletop":
            raise ValueError("home and home_task scenes require --perception rgbd; ground-truth is tabletop-only")
        service = RobotRuntimeService(
            TabletopWorld.seeded(seed, xml_path=xml_path, human_speed=human_speed), robot_id=robot_id
        )
    else:
        raise ValueError("unknown perception mode")
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
    parser.add_argument("--robot-id", default="xlerobot-mujoco-tabletop")
    parser.add_argument("--xml", default=None, help="override task model XML path")
    parser.add_argument("--perception",choices=("rgbd","ground-truth"),
                        default=os.environ.get("TANGYING_SIM_PERCEPTION","ground-truth"),
                        help="rgbd: robot head camera perception; ground-truth: legacy simulator debug")
    parser.add_argument("--scene", choices=("tabletop", "home", "home_task"), default=os.environ.get("TANGYING_SIM_SCENE", "tabletop"),
                        help="commissioned scene: tabletop, home navigation, or home_task mobile manipulation")
    parser.add_argument("--calibration-dir", default=os.environ.get("TANGYING_SIM_CALIBRATION_DIR", ""),
                        help="directory holding calibration.json; empty derives it from the model without persisting")
    parser.add_argument("--human-speed", type=float, default=0.0,
                        help="wall-clock seconds per physics step; slows execution to a watchable speed (0 = as fast as possible)")
    args = parser.parse_args()
    serve(args.listen, args.seed, robot_id=args.robot_id, xml_path=args.xml,
          human_speed=args.human_speed,perception=args.perception,scene=args.scene,
          calibration_root=args.calibration_dir or None)


if __name__ == "__main__":
    main()
