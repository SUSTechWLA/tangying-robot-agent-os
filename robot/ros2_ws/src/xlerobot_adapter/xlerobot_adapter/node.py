from __future__ import annotations

import json
import time
from pathlib import Path

import rclpy
from rclpy.action import ActionServer
from rclpy.node import Node
from std_msgs.msg import Bool
from tangying_robot_msgs.action import ExecuteSkill

from .driver import XLeRobotDriver


class XLeRobotAdapterNode(Node):
    def __init__(self):
        super().__init__("xlerobot_adapter")
        self.declare_parameter("upstream_root", "/opt/XLeRobot")
        self.declare_parameter(
            "calibration_root", "/var/lib/tangying-robot-agent-os/calibration"
        )
        self.declare_parameter("port1", "/dev/tangying-left")
        self.declare_parameter("port2", "/dev/tangying-right")
        self.driver = XLeRobotDriver(
            upstream_root=Path(self.get_parameter("upstream_root").value),
            calibration_root=Path(self.get_parameter("calibration_root").value),
            ports=(self.get_parameter("port1").value, self.get_parameter("port2").value),
        )
        self._server = ActionServer(self, ExecuteSkill, "execute_skill", self._execute)
        self.create_subscription(Bool, "emergency_stop", self._on_estop, 10)

    def _execute(self, goal_handle):
        request = goal_handle.request
        result = ExecuteSkill.Result()
        if request.deadline_unix_ms <= int(time.time() * 1000):
            result.code = "COMMAND_EXPIRED"
            result.message = result.code
            goal_handle.abort()
            return result
        try:
            parameters = json.loads(request.parameters_json or "{}")
        except json.JSONDecodeError:
            result.code = "INVALID_PARAMETERS"
            result.message = result.code
            goal_handle.abort()
            return result
        actions = parameters.get("action_chunk", [])
        executed = self.driver.execute_action_chunk(actions)
        result.success = executed.success
        result.code = executed.code
        result.message = executed.message or executed.code
        result.verification_confidence = 1.0 if executed.success else 0.0
        if executed.success:
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return result

    def _on_estop(self, message: Bool) -> None:
        if message.data:
            self.driver.stop("ROS_EMERGENCY_STOP")

    def destroy_node(self):
        self.driver.disconnect()
        self._server.destroy()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = XLeRobotAdapterNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
