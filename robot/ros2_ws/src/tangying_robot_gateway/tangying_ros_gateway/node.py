from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import Bool, Int64, String
from tangying_robot_gateway.backend import BackendResult, RobotBackend
from tangying_robot_gateway.service import start_server
from tangying_robot_msgs.action import ExecuteSkill
from tangying_robot_proto.robot.v1 import robot_pb2


class ROSBackend(RobotBackend):
    def __init__(self, node: GatewayNode):
        self.node = node

    def capabilities(self):
        ready = self.node._action.wait_for_server(timeout_sec=0.1)
        return robot_pb2.RobotCapabilities(
            robot_id="xlerobot-edge",
            adapter="xlerobot_ros2",
            skills=[
                "manipulation.pick",
                "manipulation.place",
                "recover_to_safe_pose",
                "emergency_stop",
            ],
            cameras=["head"],
            manipulation_ready=ready,
            blockers=[] if ready else ["ROS_ACTION_SERVER_UNAVAILABLE"],
            software_version="0.1.0-rc.1",
        )

    def observe(self, request):
        entities = []
        for entity in self.node.scene_entities():
            entities.append(
                robot_pb2.SceneEntity(
                    entity_id=str(entity.get("entity_id", "")),
                    category=str(entity.get("category", "")),
                    attributes={str(k): str(v) for k, v in entity.get("attributes", {}).items()},
                    pose_xyz_quat=[float(value) for value in entity.get("pose_xyz_quat", [])],
                    confidence=float(entity.get("confidence", 0.0)),
                    relation=str(entity.get("relation", "")),
                )
            )
        return robot_pb2.Observation(
            observation_id=f"ros-{time.monotonic_ns()}",
            wall_time_unix_ms=int(time.time() * 1000),
            monotonic_time_ns=time.monotonic_ns(),
            entities=entities,
        )

    def execute(self, command):
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
        self.declare_parameter("grpc_listen", "0.0.0.0:50051")
        self.declare_parameter("allow_insecure", False)
        self.declare_parameter("server_key", "/var/lib/tangying-robot/certs/server.key")
        self.declare_parameter("server_cert", "/var/lib/tangying-robot/certs/server.crt")
        self.declare_parameter("client_ca", "/var/lib/tangying-robot/certs/client-ca.crt")
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
        return start_server(
            ROSBackend(self),
            self.get_parameter("grpc_listen").value,
            allow_insecure=insecure,
            **({} if insecure else paths),
        )

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
        goal.skill = command.skill
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
        self._grpc_server.stop(grace=1)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = GatewayNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
