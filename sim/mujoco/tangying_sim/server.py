from __future__ import annotations

import argparse
import copy
import time
import uuid
from concurrent import futures

import grpc
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

from .world import ActionResult, TabletopWorld


class RobotRuntimeService(robot_pb2_grpc.RobotRuntimeServicer):
    def __init__(self, world: TabletopWorld):
        self.world = world
        self._results: dict[str, tuple[tuple[object, ...], list[robot_pb2.SkillEvent]]] = {}
        self._estopped = False

    def GetRuntimeInfo(self, request, context):
        capabilities = self._capability_infos()
        return robot_pb2.RuntimeInfo(
            robot_id="mujoco-tabletop",
            adapter="mujoco",
            skills=[item.name for item in capabilities],
            cameras=["sim-main"],
            manipulation_ready=not self._estopped,
            blockers=["EMERGENCY_STOP_LATCHED"] if self._estopped else [],
            software_version="0.1.0-rc.2",
            protocol_version="1.0",
            runtime_version="0.1.0-rc.2",
            capabilities=capabilities,
        )

    def Observe(self, request, context):
        observation = self._observation()
        observation.semantic_state.CopyFrom(
            robot_pb2.SemanticState(
                activity="EMERGENCY_STOPPED" if self._estopped else "IDLE",
                mode="SIMULATION",
                emergency_stopped=self._estopped,
                anomalies=["EMERGENCY_STOP_LATCHED"] if self._estopped else [],
            )
        )
        yield observation

    def _capability_infos(self):
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
        if command.idempotency_key in self._results:
            fingerprint, events = self._results[command.idempotency_key]
            if fingerprint != self._fingerprint(command):
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
        events = self._execute(command)
        self._results[command.idempotency_key] = (self._fingerprint(command), events)
        yield from (copy.deepcopy(event) for event in events)

    def _execute(self, command: robot_pb2.SkillCommand) -> list[robot_pb2.SkillEvent]:
        error = self._validate(command)
        if error:
            return [self._event(command, 1, robot_pb2.SKILL_EVENT_FAILED, error, error)]
        events = [
            self._event(command, 1, robot_pb2.SKILL_EVENT_ACCEPTED, "ACCEPTED", "accepted"),
            self._event(command, 2, robot_pb2.SKILL_EVENT_RUNNING, "RUNNING", "running", 0.25),
        ]
        result = self._dispatch(command)
        event_type = robot_pb2.SKILL_EVENT_SUCCEEDED if result.success else robot_pb2.SKILL_EVENT_FAILED
        events.append(
            self._event(
                command,
                3,
                event_type,
                result.code,
                result.message or result.code,
                1.0,
                result.confidence,
            )
        )
        return events

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
        if self._estopped:
            return "EMERGENCY_STOP_LATCHED"
        return ""

    def _dispatch(self, command: robot_pb2.SkillCommand) -> ActionResult:
        if command.skill == "manipulation.pick":
            return self.world.pick(command.target_ref)
        if command.skill == "manipulation.place":
            return self.world.place(command.target_ref)
        if command.skill == "verify_grasp":
            return self.world.verify_grasp(command.target_ref)
        if command.skill == "verify_placement":
            object_id = command.parameters.fields.get("objectId")
            if object_id is None:
                return ActionResult(False, "OBJECT_ID_REQUIRED")
            return self.world.verify_inside(object_id.string_value, command.target_ref)
        if command.skill in {"observe_scene", "resolve_targets", "plan_grasp", "recover_to_safe_pose"}:
            return ActionResult(True)
        return ActionResult(False, "SKILL_NOT_ALLOWED", command.skill)

    def Cancel(self, request, context):
        return robot_pb2.CancelResult(accepted=True, state="CANCELLED")

    def EmergencyStop(self, request, context):
        self._estopped = True
        return robot_pb2.EStopResult(latched=True, stopped_unix_ms=int(time.time() * 1000))

    def _observation(self) -> robot_pb2.Observation:
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
        observation.robot_state.update(
            {
                "step_count": self.world.step_count,
                "pick_count": self.world.pick_count,
                "held": self.world._held or "",
                "simulation": True,
            }
        )
        return observation

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
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(
        RobotRuntimeService(TabletopWorld.seeded(seed)), server
    )
    server.add_insecure_port(address)
    server.start()
    server.wait_for_termination()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1:50051")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    serve(args.listen, args.seed)


if __name__ == "__main__":
    main()
