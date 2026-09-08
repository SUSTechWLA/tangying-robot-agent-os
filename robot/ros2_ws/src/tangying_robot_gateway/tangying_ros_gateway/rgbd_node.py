"""ROS 2 Image/CameraInfo/TF input for the strict, journaled plugin gateway.

Importing this module does not initialize ROS or connect hardware. The explicit
local factory starts subscriptions only; its default profile is sensor-only.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import numpy as np
from pydantic import Field
from tangying_robot_gateway.contracts import Contract, Identifier, RobotProfile
from tangying_robot_gateway.rgbd import RgbdPerception
from tangying_robot_gateway.ros_rgbd import (
    CameraInfoPacket,
    ImagePacket,
    RosRgbdInput,
    WorldTransform,
    create_rgbd_backend,
)


class RosRgbdConfig(Contract):
    profile_path: str = Field(min_length=1, max_length=4096)
    source_id: Identifier
    color_topic: Identifier = "camera/color/image_rect"
    depth_topic: Identifier = "camera/aligned_depth_to_color/image_raw"
    camera_info_topic: Identifier = "camera/color/camera_info"
    node_name: Identifier = "tangying_rgbd_source"
    namespace: str = Field(default="", max_length=256)
    rectified: bool = True
    max_skew_ms: int = Field(default=30, ge=0, le=100)
    queue_size: int = Field(default=4, ge=1, le=8)
    tf_timeout_ms: int = Field(default=100, ge=1, le=1000)
    first_frame_timeout_ms: int = Field(default=5000, ge=1, le=30_000)


def _stamp_ns(header) -> int:
    sec, nanosec = header.stamp.sec, header.stamp.nanosec
    if type(sec) is not int or type(nanosec) is not int or sec < 0 or not 0 <= nanosec < 1_000_000_000:
        raise ValueError("invalid ROS header timestamp")
    return sec * 1_000_000_000 + nanosec


def image_packet(message) -> ImagePacket:
    if message.is_bigendian not in (0, 1):
        raise ValueError("invalid ROS image endian flag")
    # Bound before copying the ROS-owned buffer. RosRgbdInput checks encoding,
    # dimensions, row strides and exact byte length before retaining it.
    if len(message.data) > (3840 * 4 + 4096) * 2160:
        raise ValueError("ROS image buffer exceeds bounded capture size")
    return ImagePacket(_stamp_ns(message.header), message.header.frame_id,
                       message.width, message.height, message.encoding, message.step,
                       bool(message.is_bigendian), bytes(message.data))


def camera_info_packet(message, *, rectified: bool) -> CameraInfoPacket:
    return CameraInfoPacket(
        _stamp_ns(message.header), message.header.frame_id, message.width, message.height,
        tuple(message.k), tuple(message.d), tuple(message.p), rectified,
        message.binning_x, message.binning_y,
        (message.roi.x_offset, message.roi.y_offset, message.roi.width, message.roi.height),
    )


def world_transform(message, revision: str) -> WorldTransform:
    translation, rotation = message.transform.translation, message.transform.rotation
    quaternion = np.asarray([rotation.w, rotation.x, rotation.y, rotation.z], dtype=float)
    if not np.isfinite(quaternion).all() or abs(float(quaternion @ quaternion) - 1.) > 1e-6:
        raise ValueError("ROS TF quaternion must be finite and normalized")
    w, x, y, z = quaternion
    matrix = np.eye(4)
    matrix[:3, :3] = [
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ]
    matrix[:3, 3] = [translation.x, translation.y, translation.z]
    stamp_ns = _stamp_ns(message.header)
    return WorldTransform(stamp_ns, message.child_frame_id, message.header.frame_id,
                          revision, matrix, static=stamp_ns == 0)


class RosRgbdSource:
    """Own a private ROS context and executor; no action server or motor is used.

    The executor keeps subscription and TF callbacks running while a gRPC
    request waits briefly for the exact capture-time transform. ``stop`` only
    stops this sensor source. A robot factory with physical tools must supply
    its actual local driver emergency stop to ``create_rgbd_backend``.
    """

    def __init__(self, profile: RobotProfile | dict, config: RosRgbdConfig):
        # ROS is optional for core SDK tests and direct hardware deployments.
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import CameraInfo, Image
        from tf2_ros import Buffer, TransformListener

        self.config = config
        self._closed = threading.Event()
        self._stopped = threading.Event()
        self._executor = self._node = self._thread = self._context = None
        self.input = RosRgbdInput(profile, config.source_id, transform_lookup=self._lookup,
                                  max_skew_ms=config.max_skew_ms, queue_size=config.queue_size)
        try:
            self._context = Context()
            rclpy.init(args=[], context=self._context)
            self._node = Node(config.node_name, namespace=config.namespace, context=self._context)
            self._buffer = Buffer(node=self._node)
            self._listener = TransformListener(self._buffer, self._node)
            self._subscriptions = [
                self._node.create_subscription(Image, config.color_topic,
                                                self._color, qos_profile_sensor_data),
                self._node.create_subscription(Image, config.depth_topic,
                                                self._depth, qos_profile_sensor_data),
                self._node.create_subscription(CameraInfo, config.camera_info_topic,
                                                self._info, qos_profile_sensor_data),
            ]
            self._executor = MultiThreadedExecutor(num_threads=2, context=self._context)
            self._executor.add_node(self._node)
            self._thread = threading.Thread(target=self._spin, name="ros-rgbd-input", daemon=True)
            self._thread.start()
        except Exception:
            self.close()
            raise

    def _spin(self):
        try:
            self._executor.spin()
        except Exception as exc:  # noqa: BLE001 - executor failure invalidates all sensor data
            if not self._closed.is_set():
                self.input.reject(f"ROS executor stopped: {exc}")
                self._stopped.set()

    def _accept(self, callback, packet):
        if self._closed.is_set() or self._stopped.is_set():
            return
        try:
            if self._node.get_parameter("use_sim_time").value is not False:
                raise ValueError("ROS RGB-D requires Unix wall-clock captures; use_sim_time must be false")
            callback(packet())
        except Exception as exc:  # noqa: BLE001 - malformed vendor messages must fail closed
            self.input.reject(str(exc))
            self._node.get_logger().warning(f"RGB-D input rejected: {exc}")

    def _color(self, message):
        self._accept(self.input.push_color, lambda: image_packet(message))

    def _depth(self, message):
        self._accept(self.input.push_depth, lambda: image_packet(message))

    def _info(self, message):
        self._accept(self.input.push_camera_info,
                     lambda: camera_info_packet(message, rectified=self.config.rectified))

    def _lookup(self, frame_id, stamp_ns):
        from rclpy.clock import ClockType
        from rclpy.duration import Duration
        from rclpy.time import Time

        if self._closed.is_set() or self._stopped.is_set():
            raise ValueError("ROS RGB-D source is stopped")
        message = self._buffer.lookup_transform(
            "world", frame_id, Time(nanoseconds=stamp_ns, clock_type=ClockType.ROS_TIME),
            timeout=Duration(nanoseconds=self.config.tf_timeout_ms * 1_000_000),
        )
        return world_transform(message, self.input.sensor.transform_revision)

    def read(self):
        if self._closed.is_set() or self._stopped.is_set():
            raise ValueError("ROS RGB-D source is stopped")
        return self.input.read()

    def wait_for_frame(self):
        deadline = time.monotonic() + self.config.first_frame_timeout_ms / 1000
        error = "no frame received"
        while time.monotonic() < deadline and not self._stopped.is_set():
            try:
                return self.read()
            except Exception as exc:  # noqa: BLE001 - startup has a bounded readiness deadline
                error = str(exc)
                self._closed.wait(.02)
        raise ValueError(f"ROS RGB-D did not become ready: {error}")

    def stop(self, reason):
        self._stopped.set()
        self.input.reject(reason)

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        self.stop("ROS_RGBD_SOURCE_CLOSED")
        try:
            if self._executor is not None:
                self._executor.shutdown(timeout_sec=2.)
        finally:
            try:
                if self._node is not None:
                    self._node.destroy_node()
            finally:
                if self._context is not None and self._context.ok():
                    self._context.shutdown()
                if self._thread is not None:
                    self._thread.join(timeout=2.)
                    if self._thread.is_alive():
                        raise RuntimeError("ROS RGB-D executor did not shut down")


def create_sensor_backend():
    """Trusted local factory for a camera-only profile and geometric point cloud.

    This ready-to-run entry point supplies no object recognition or successful
    manipulation placeholders. Deployment factories can combine RosRgbdSource,
    their detector and commissioned actuator handlers with create_rgbd_backend.
    """
    path_text = os.environ.get("TANGYING_ROS_RGBD_CONFIG", "")
    if not path_text:
        raise ValueError("TANGYING_ROS_RGBD_CONFIG must identify a trusted local JSON configuration")
    path = Path(path_text).expanduser().resolve()
    config = RosRgbdConfig.model_validate(json.loads(path.read_text()))
    profile_path = Path(config.profile_path)
    if not profile_path.is_absolute():
        profile_path = path.parent / profile_path
    profile = RobotProfile.model_validate(json.loads(profile_path.read_text()))
    if set(profile.tools) != {"observe_scene", "emergency_stop"}:
        raise ValueError("default ROS RGB-D factory requires a sensor-only profile")
    source = RosRgbdSource(profile, config)
    try:
        source.wait_for_frame()
        return create_rgbd_backend(profile, source, RgbdPerception(lambda frame: []),
                                    stop=source.stop, disconnect=source.close)
    except Exception:
        source.close()
        raise


def main():
    # This CLI preserves the same TLS, persistent journal and process ownership
    # arguments as every other plugin gateway. No second server stack is used.
    from tangying_robot_gateway.run_plugin import main as plugin_main

    plugin_main()


if __name__ == "__main__":
    main()
