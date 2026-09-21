"""Dump the live navigation occupancy grid out of the Gazebo stack.

Reads one ``nav_msgs/OccupancyGrid`` from ``/map`` and writes a compact JSON
document: dimensions, resolution, origin, and the cells as base64-encoded
int8. Run it inside the container, redirect stdout to a file on the host.

    ros2 run  --  python3 dump_grid.py > /tmp/live-map.json

Why a topic read rather than the HTTP API: the navigation service exposes the
map's *metadata* (extent, revision, known-cell count) but not the cells, and the
question this measurement answers - how much of the house did the robot actually
map - needs the cells.
"""

import base64
import json
import sys

import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy


class GridDump(Node):
    def __init__(self):
        super().__init__("tangying_grid_dump")
        qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.message = None
        self.create_subscription(OccupancyGrid, "/map", self.on_grid, qos)

    def on_grid(self, message):
        self.message = message


def main() -> int:
    rclpy.init()
    node = GridDump()
    # The map is latched, so this either arrives immediately or the publisher is
    # not up; there is nothing to wait for beyond a generous bound.
    for _ in range(300):
        rclpy.spin_once(node, timeout_sec=0.1)
        if node.message is not None:
            break
    if node.message is None:
        print("no /map message received", file=sys.stderr)
        return 2
    message = node.message
    # OccupancyGrid data is int8: -1 unknown, 0 free, 100 occupied.
    raw = bytes((value & 0xFF) for value in message.data)
    document = {
        "width": int(message.info.width),
        "height": int(message.info.height),
        "resolution": float(message.info.resolution),
        "origin": [float(message.info.origin.position.x), float(message.info.origin.position.y)],
        "frameId": str(message.header.frame_id),
        "cellsB64": base64.b64encode(raw).decode("ascii"),
    }
    json.dump(document, sys.stdout)
    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
