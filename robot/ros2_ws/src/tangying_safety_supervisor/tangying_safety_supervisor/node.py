from __future__ import annotations

import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Int64, String


class SafetySupervisorNode(Node):
    def __init__(self):
        super().__init__("tangying_safety_supervisor")
        self.declare_parameter("heartbeat_timeout_ms", 1000)
        self._last_heartbeat_ms = 0
        self._latched = False
        self._estop = self.create_publisher(Bool, "emergency_stop", 10)
        self._reason = self.create_publisher(String, "safety_stop_reason", 10)
        self.create_subscription(Int64, "command_lease_heartbeat", self._on_heartbeat, 10)
        self.create_timer(0.05, self._check_watchdog)

    def _on_heartbeat(self, message: Int64) -> None:
        if not self._latched:
            self._last_heartbeat_ms = message.data

    def _check_watchdog(self) -> None:
        if self._latched or self._last_heartbeat_ms == 0:
            return
        timeout = self.get_parameter("heartbeat_timeout_ms").value
        now_ms = int(time.time() * 1000)
        if now_ms - self._last_heartbeat_ms > timeout:
            self._latched = True
            self._estop.publish(Bool(data=True))
            self._reason.publish(String(data="COMMAND_LEASE_EXPIRED"))


def main(args=None):
    rclpy.init(args=args)
    node = SafetySupervisorNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
