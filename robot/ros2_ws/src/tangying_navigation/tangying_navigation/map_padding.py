"""Extend map bounds with unknown cells, never invent free space.

A forward camera can produce an RTAB-Map grid whose near boundary clips the
robot footprint. Nav2 StaticLayer cannot clear a polygon with out-of-map
vertices, so the start cell remains unknown and every route fails. Padding
allows the existing footprint-clearing policy to operate within grid bounds.
"""
from __future__ import annotations

import math

import numpy as np


def pad_grid(data, width, height, resolution, margin=.75):
    if not math.isfinite(resolution) or resolution <= 0 or width <= 0 or height <= 0:
        raise ValueError('invalid grid dimensions')
    cells = math.ceil(margin / resolution)
    grid = np.asarray(data, dtype=np.int8).reshape(height, width)
    return np.pad(grid, cells, constant_values=-1), cells * resolution


def main(args=None):
    import copy

    import rclpy
    from nav_msgs.msg import OccupancyGrid
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile

    from .contracts import quaternion_matrix
    rclpy.init(args=args)
    node = Node('map_bounds_padding')
    qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    publisher = node.create_publisher(OccupancyGrid, '/map', qos)

    def receive(message):
        try:
            grid, offset = pad_grid(message.data, message.info.width, message.info.height, message.info.resolution)
            result = copy.deepcopy(message)
            result.info.height, result.info.width = grid.shape
            q = message.info.origin.orientation
            shift = quaternion_matrix([q.w, q.x, q.y, q.z]) @ np.array([-offset, -offset, 0.])
            result.info.origin.position.x += float(shift[0])
            result.info.origin.position.y += float(shift[1])
            result.info.origin.position.z += float(shift[2])
            result.data = grid.ravel().tolist()
            publisher.publish(result)
        except ValueError as error:
            node.get_logger().warning(f'Refused malformed occupancy grid: {error}')

    node.create_subscription(OccupancyGrid, '/rtabmap/raw_grid_map', receive, qos)
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
