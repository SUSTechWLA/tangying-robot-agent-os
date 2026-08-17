from __future__ import annotations

import json
import threading
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String
from tangying_robot_msgs.action import ExecuteSkill


class GatewayNode(Node):
    def __init__(self):
        super().__init__("tangying_robot_gateway")
        self._action = ActionClient(self, ExecuteSkill, "execute_skill")
        self._scene_lock = threading.Lock()
        self._scene_entities: list[dict] = []
        self.create_subscription(String, "scene_entities", self._on_scene, 10)

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

        sent = self._action.send_goal_async(goal)
        goal_handle = self._wait_future(sent, timeout_seconds)
        if not goal_handle.accepted:
            raise RuntimeError("execute_skill goal was rejected")
        wrapped = self._wait_future(goal_handle.get_result_async(), timeout_seconds)
        return wrapped.result

    @staticmethod
    def _wait_future(future, timeout_seconds: float):
        deadline = time.monotonic() + timeout_seconds
        while not future.done():
            if time.monotonic() >= deadline:
                raise TimeoutError("ROS 2 action timed out")
            time.sleep(0.01)
        return future.result()


def main(args=None):
    rclpy.init(args=args)
    node = GatewayNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
