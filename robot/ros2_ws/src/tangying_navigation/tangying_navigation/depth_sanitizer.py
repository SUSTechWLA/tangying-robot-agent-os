"""Prepare a depth stream for RGB-D SLAM without changing the raw sensor feed.

Real depth cameras use NaN, infinity or zero for pixels with no return.  Those
values must stay available to the UI and obstacle clearing path, but a SLAM
front-end benefits from a finite far-range value when it samples an image
feature.  This node performs that narrow conversion and preserves the original
encoding whenever possible.
"""

from __future__ import annotations

import copy

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image


class DepthSanitizer(Node):
    def __init__(self):
        super().__init__("depth_sanitizer")
        self.declare_parameter("input_topic", "/camera/base/depth/image_raw")
        self.declare_parameter("output_topic", "/camera/base/depth/rtabmap")
        self.declare_parameter("max_depth_m", 5.0)
        input_topic = str(self.get_parameter("input_topic").value)
        output_topic = str(self.get_parameter("output_topic").value)
        self.max_depth_m = max(0.1, float(self.get_parameter("max_depth_m").value))
        self.pub = self.create_publisher(Image, output_topic, qos_profile_sensor_data)
        self.sub = self.create_subscription(Image, input_topic, self.on_image, qos_profile_sensor_data)
        self._warned_encoding = False

    def on_image(self, msg: Image) -> None:
        if msg.encoding not in {"32FC1", "16UC1"}:
            if not self._warned_encoding:
                self.get_logger().warning(
                    f"unsupported depth encoding {msg.encoding}; forwarding raw frames"
                )
                self._warned_encoding = True
            self.pub.publish(msg)
            return
        if msg.width <= 0 or msg.height <= 0:
            return
        if msg.encoding == "32FC1":
            values = np.frombuffer(msg.data, dtype=np.float32)
            expected = msg.height * msg.width
            if values.size != expected:
                self.get_logger().warning("invalid 32FC1 depth size; dropping frame")
                return
            values = values.reshape(msg.height, msg.width).copy()
            invalid = (~np.isfinite(values)) | (values <= 0.0) | (values > self.max_depth_m)
            values[invalid] = self.max_depth_m
            out = copy.copy(msg)
            out.step = msg.width * 4
            out.data = values.astype(np.float32, copy=False).tobytes()
        else:
            values = np.frombuffer(msg.data, dtype=np.uint16)
            expected = msg.height * msg.width
            if values.size != expected:
                self.get_logger().warning("invalid 16UC1 depth size; dropping frame")
                return
            values = values.reshape(msg.height, msg.width).copy()
            max_mm = round(self.max_depth_m * 1000.0)
            invalid = (values == 0) | (values > max_mm)
            values[invalid] = max_mm
            out = copy.copy(msg)
            out.step = msg.width * 2
            out.data = values.astype(np.uint16, copy=False).tobytes()
        self.pub.publish(out)


def main(args=None):
    rclpy.init(args=args)
    node = DepthSanitizer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
