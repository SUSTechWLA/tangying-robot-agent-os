from __future__ import annotations

import json
import os
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import Bool, Int64, String
from tangying_robot_gateway.backend import BackendResult, RobotBackend, capability
from tangying_robot_gateway.journal import RuntimeJournal
from tangying_robot_gateway.local_recovery import exclusive_runtime
from tangying_robot_gateway.runtime import Observation, RuntimeInfo, SceneEntity
from tangying_robot_gateway.service import start_server
from tangying_robot_msgs.action import ExecuteSkill

READ_ONLY_SKILLS = {"observe_scene", "resolve_targets", "plan_grasp"}
VERIFY_SKILLS = {"verify_grasp", "verify_placement", "verify_arrival"}


class ROSBackend(RobotBackend):
    def __init__(self, node: GatewayNode):
        self.node = node

    def capabilities(self):
        action_ready = self.node._action.wait_for_server(timeout_sec=0.1)
        # This legacy gateway has no result verifier integration. An available
        # action server cannot establish that a grasp or placement is verifiable.
        physical_blockers = ([] if action_ready else ["ROS_ACTION_SERVER_UNAVAILABLE"]) + ["VERIFIER_REQUIRED"]
        ready = False
        capabilities = [
            capability(
                "observe_scene",
                "Return scene entities published by the ROS 2 perception stack.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
                input_parameters=["streams", "max_rate_hz"],
                output_parameters=["entities"],
            ),
            capability(
                "resolve_targets",
                "Resolve grounded object and destination references.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            capability(
                "plan_grasp",
                "Plan a tabletop grasp without moving the robot.",
                available=True,
                safety_level="read_only",
                default_timeout_ms=5_000,
            ),
            capability(
                "manipulation.pick",
                "Execute a pick through the ROS 2 xlerobot_adapter action.",
                available=ready,
                safety_level="physical_motion",
                blockers=physical_blockers,
                cancellable=True,
                default_timeout_ms=15_000,
                input_parameters=["target_ref", "action_chunk"],
                output_parameters=["grasp_state"],
            ),
            capability(
                "verify_grasp",
                "Verify the current grasp from the ROS 2 perception stack.",
                available=False,
                blockers=["VERIFIER_REQUIRED"],
                safety_level="read_only",
                default_timeout_ms=5_000,
                input_parameters=["object_id"],
                output_parameters=["verification_confidence"],
            ),
            capability(
                "manipulation.place",
                "Execute a place through the ROS 2 xlerobot_adapter action.",
                available=ready,
                safety_level="physical_motion",
                blockers=physical_blockers,
                cancellable=True,
                default_timeout_ms=15_000,
                input_parameters=["target_ref", "action_chunk"],
                output_parameters=["placement_state"],
            ),
            capability(
                "verify_placement",
                "Verify the final placement from the ROS 2 perception stack.",
                available=False,
                blockers=["VERIFIER_REQUIRED"],
                safety_level="read_only",
                default_timeout_ms=5_000,
                input_parameters=["object_id", "destination_id"],
                output_parameters=["verification_confidence"],
            ),
            capability(
                "verify_arrival",
                "Verify a room waypoint from a fresh RGB-D and localization capture.",
                available=False,
                blockers=["VERIFIER_REQUIRED"],
                safety_level="read_only",
                default_timeout_ms=5_000,
                input_parameters=["goal_pose"],
                output_parameters=["verification_confidence"],
            ),
            capability(
                "recover_to_safe_pose",
                "Requires a separately commissioned recovery trajectory; use attended local recovery.",
                available=False,
                safety_level="physical_motion",
                blockers=["RECOVERY_POLICY_REQUIRED"],
                cancellable=True,
                recoverable=False,
                default_timeout_ms=15_000,
            ),
            capability(
                "emergency_stop",
                "Publish an emergency stop to the ROS 2 adapter and latch locally.",
                available=True,
                safety_level="physical_motion",
                default_timeout_ms=5_000,
            ),
        ]
        return RuntimeInfo(
            robot_id="xlerobot-edge",
            adapter="xlerobot_ros2",
            manipulation_ready=ready,
            blockers=physical_blockers,
            software_version="0.7.0",
            protocol_version="1.0",
            runtime_version="0.7.0",
            capabilities=capabilities,
        )

    def observe(self, request):
        entities = []
        for entity in self.node.scene_entities():
            entities.append(
                SceneEntity(
                    entity_id=str(entity.get("entity_id", "")),
                    category=str(entity.get("category", "")),
                    attributes={str(k): str(v) for k, v in entity.get("attributes", {}).items()},
                    pose_xyz_quat=[float(value) for value in entity.get("pose_xyz_quat", [])],
                    confidence=float(entity.get("confidence", 0.0)),
                    relation=str(entity.get("relation", "")),
                )
            )
        return Observation(
            observation_id=f"ros-{time.monotonic_ns()}",
            wall_time_unix_ms=int(time.time() * 1000),
            monotonic_time_ns=time.monotonic_ns(),
            entities=entities,
        )

    def execute(self, command):
        # ROS 2 stays an internal implementation detail. Read-only semantic
        # skills are served locally; only physical motion crosses the ROS
        # action boundary.
        if command.capability in READ_ONLY_SKILLS:
            return BackendResult(True)
        if command.capability in VERIFY_SKILLS:
            return BackendResult(
                False,
                "VERIFICATION_UNAVAILABLE",
                "install a ROS 2 verification provider before treating a physical task as successful",
                confidence=0.0,
            )
        if command.capability in {"manipulation.pick", "manipulation.place"}:
            return BackendResult(False, "VERIFIER_REQUIRED", "commission observation and result verification before physical execution", confidence=0.0)
        if command.capability == "recover_to_safe_pose":
            return BackendResult(False, "RECOVERY_POLICY_REQUIRED", "no calibrated autonomous recovery trajectory is installed", confidence=0.0)
        result = self.node.execute(command)
        return BackendResult(
            success=result.success,
            code=result.code,
            message=result.message,
            observation_id=result.observation_id,
            confidence=result.verification_confidence,
        )

    def stop(self, reason: str):
        self.node.publish_estop(reason)


class GatewayNode(Node):
    def __init__(self):
        super().__init__("tangying_ros_gateway")
        self.declare_parameter("grpc_listen", os.getenv("ROBOT_GRPC_LISTEN", "0.0.0.0:50051"))
        self.declare_parameter("allow_insecure", False)
        self.declare_parameter("runtime_journal", os.getenv(
            "ROBOT_RUNTIME_JOURNAL", "/var/lib/tangying-robot-agent-os/runtime-journal.json"))
        self.declare_parameter(
            "server_key",
            os.getenv(
                "ROBOT_SERVER_KEY",
                "/var/lib/tangying-robot-agent-os/certs/server.key",
            ),
        )
        self.declare_parameter(
            "server_cert",
            os.getenv(
                "ROBOT_SERVER_CERT",
                "/var/lib/tangying-robot-agent-os/certs/server.crt",
            ),
        )
        self.declare_parameter(
            "client_ca",
            os.getenv(
                "ROBOT_CLIENT_CA",
                "/var/lib/tangying-robot-agent-os/certs/client-ca.crt",
            ),
        )
        self._action = ActionClient(self, ExecuteSkill, "execute_skill")
        self._scene_lock = threading.Lock()
        self._scene_entities: list[dict] = []
        self._estop = self.create_publisher(Bool, "emergency_stop", 10)
        self._stop_reason = self.create_publisher(String, "safety_stop_reason", 10)
        self._lease_heartbeat = self.create_publisher(Int64, "command_lease_heartbeat", 10)
        self.create_subscription(String, "scene_entities", self._on_scene, 10)
        self._grpc_server = self._start_gateway()

    def _start_gateway(self):
        insecure = bool(self.get_parameter("allow_insecure").value)
        paths = {
            "server_key": Path(self.get_parameter("server_key").value),
            "server_cert": Path(self.get_parameter("server_cert").value),
            "client_ca": Path(self.get_parameter("client_ca").value),
        }
        journal_path = str(self.get_parameter("runtime_journal").value).strip()
        if not journal_path:
            raise ValueError("runtime_journal must name a durable journal file")
        # Own the durable command/estop record until all RPC workers have stopped.
        # A restart must retain uncertain commands and cannot clear a safety latch.
        with ExitStack() as ownership:
            ownership.enter_context(exclusive_runtime(Path(journal_path)))
            self._journal = RuntimeJournal(Path(journal_path))
            server = start_server(
                ROSBackend(self),
                self.get_parameter("grpc_listen").value,
                journal=self._journal,
                allow_insecure=insecure,
                **({} if insecure else paths),
            )
            self._runtime_owner = ownership.pop_all()
            return server

    def _on_scene(self, message: String) -> None:
        try:
            entities = json.loads(message.data)
            if isinstance(entities, list):
                with self._scene_lock:
                    self._scene_entities = entities
        except json.JSONDecodeError:
            self.get_logger().warning("ignored invalid scene_entities JSON")

    def scene_entities(self) -> list[dict]:
        with self._scene_lock:
            return [dict(entity) for entity in self._scene_entities]

    def execute(self, command, timeout_seconds: float = 30.0):
        if not self._action.wait_for_server(timeout_sec=2.0):
            raise RuntimeError("execute_skill action server is unavailable")
        goal = ExecuteSkill.Goal()
        goal.task_id = command.task_id
        goal.command_id = command.command_id
        goal.skill = command.capability
        goal.target_ref = command.target_ref
        goal.parameters_json = json.dumps(
            {key: value for key, value in command.parameters.items()}, separators=(",", ":")
        )
        goal.lease_ms = command.lease_ms
        goal.deadline_unix_ms = command.deadline_unix_ms
        goal.idempotency_key = command.idempotency_key
        goal.safety_profile = command.safety_profile
        goal.approval_id = command.approval_id
        goal_handle = self._wait_future(self._action.send_goal_async(goal), timeout_seconds)
        if not goal_handle.accepted:
            raise RuntimeError("execute_skill goal was rejected")
        try:
            return self._wait_future(
                goal_handle.get_result_async(),
                timeout_seconds,
                heartbeat=self._publish_lease_heartbeat,
            ).result
        finally:
            self._lease_heartbeat.publish(Int64(data=0))

    def _publish_lease_heartbeat(self) -> None:
        self._lease_heartbeat.publish(Int64(data=int(time.time() * 1000)))

    def publish_estop(self, reason: str) -> None:
        self._estop.publish(Bool(data=True))
        self._stop_reason.publish(String(data=reason))

    @staticmethod
    def _wait_future(future, timeout_seconds: float, heartbeat=None):
        deadline = time.monotonic() + timeout_seconds
        next_heartbeat = 0.0
        while not future.done():
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError("ROS 2 action timed out")
            if heartbeat is not None and now >= next_heartbeat:
                heartbeat()
                next_heartbeat = now + 0.1
            time.sleep(0.01)
        return future.result()

    def destroy_node(self):
        # If the server cannot confirm shutdown, retain journal ownership rather
        # than permit a second runtime to race potentially active RPCs.
        self._grpc_server.stop(grace=1).wait()
        try:
            return super().destroy_node()
        finally:
            self._runtime_owner.close()


def main(args=None):
    rclpy.init(args=args)
    node = GatewayNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
