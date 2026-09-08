"""Publish only native Runtime camera measurements and wheel odometry to ROS."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import grpc
import numpy as np
import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, PointCloud2
from sensor_msgs_py.point_cloud2 import create_cloud_xyz32
from std_msgs.msg import Header, String
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc
from tf2_ros import TransformBroadcaster

from .acquisition import RequestPacer
from .contracts import ContractError, decode_capture, matrix_quaternion, quaternion_matrix


def stamp_ms(value):
    from builtin_interfaces.msg import Time

    return Time(sec=value // 1000, nanosec=(value % 1000) * 1000000)


def runtime_channel():
    address = os.environ.get("TANGYING_RUNTIME_ADDRESS", "host.docker.internal:50051")
    options = [("grpc.max_receive_message_length", 16 * 1024 * 1024)]
    if os.environ.get("TANGYING_RUNTIME_INSECURE", "0") == "1":
        host = address.rsplit(":", 1)[0]
        if host not in {"host.docker.internal", "127.0.0.1", "localhost", "[::1]"}:
            raise ValueError("development plaintext runtime must use local host")
        return grpc.insecure_channel(address, options=options)
    ca = Path(os.environ["TANGYING_RUNTIME_CA_FILE"]).read_bytes()
    cert = Path(os.environ["TANGYING_RUNTIME_CERT_FILE"]).read_bytes()
    key = Path(os.environ["TANGYING_RUNTIME_KEY_FILE"]).read_bytes()
    return grpc.secure_channel(
        address, grpc.ssl_channel_credentials(ca, key, cert), options=options
    )


class RuntimeRgbdBridge(Node):
    def __init__(self):
        super().__init__("runtime_rgbd_bridge")
        self.stop_event = threading.Event()
        self.channel = runtime_channel()
        self.stub = robot_pb2_grpc.RobotRuntimeStub(self.channel)
        self.robot_id = os.environ.get("TANGYING_RUNTIME_ROBOT_ID", "xlerobot-mujoco-tabletop")
        self.tf = TransformBroadcaster(self)
        self.odom = self.create_publisher(Odometry, "/odom", 10)
        self.odom_lock = threading.Lock()
        self.last_odom_ms = 0
        self.last_odom_pose = None
        self.camera_publishers = {}
        self.threads = []
        for name in ("base", "head"):
            source = os.environ.get(
                f"TANGYING_RUNTIME_{name.upper()}_SOURCE", f"{self.robot_id}/{name}-rgbd"
            )
            self.camera_publishers[name] = {
                "rgb": self.create_publisher(
                    Image, f"/camera/{name}/rgb/image_raw", qos_profile_sensor_data
                ),
                "rgb_unfiltered": self.create_publisher(
                    Image, f"/camera/{name}/rgb/image_unfiltered", qos_profile_sensor_data
                ),
                "self_filter": self.create_publisher(
                    String, f"/camera/{name}/self_filter_status", 1
                ),
                "depth": self.create_publisher(
                    Image, f"/camera/{name}/depth/image_raw", qos_profile_sensor_data
                ),
                "info": self.create_publisher(
                    CameraInfo, f"/camera/{name}/rgb/camera_info", qos_profile_sensor_data
                ),
                "points": self.create_publisher(
                    PointCloud2, f"/camera/{name}/points", qos_profile_sensor_data
                ),
            }
            thread = threading.Thread(target=self.observe, args=(name, source), daemon=True)
            thread.start()
            self.threads.append(thread)

    def observe(self, name, source):
        last_id = ""
        last_ms = 0
        requests = RequestPacer()
        while not self.stop_event.is_set():
            if not requests.wait(self.stop_event.wait):
                break
            try:
                info = self.stub.GetRuntimeInfo(robot_pb2.GetRuntimeInfoRequest(), timeout=3)
                if info.robot_id != self.robot_id:
                    raise ContractError("runtime identity mismatch")
                sensors = info.robot_profile.fields.get("sensors")
                allowed = sensors and any(
                    sensor.struct_value.fields.get("sourceId").string_value == source
                    and sensor.struct_value.fields.get("sourceType").string_value == "rgbd_camera"
                    for sensor in sensors.list_value.values
                )
                if not allowed:
                    raise ContractError("RGB-D source is not declared by runtime")
                stream = self.stub.Observe(
                    robot_pb2.ObserveRequest(source_id=source, streams=["rgbd_raw", "sensor_only"], max_rate_hz=5),
                    timeout=300,
                )
                for observation in stream:
                    if self.stop_event.is_set():
                        stream.cancel()
                        break
                    reconstruction = observation.reconstruction.fields
                    if (
                        reconstruction.get("sourceId") is None
                        or reconstruction["sourceId"].string_value != source
                    ):
                        raise ContractError("capture source mismatch")
                    capture = decode_capture(observation, int(time.time() * 1000))
                    if capture.observation_id == last_id:
                        continue
                    if capture.observed_at_ms <= last_ms:
                        # A snapshot reconnect may see the same frozen sensor
                        # sample with a new observation sequence. Discard it;
                        # treating it as an outage would add a one-second gap
                        # to an otherwise healthy source. Never republish or
                        # change its timestamp, and retain the monotonic cursor.
                        continue
                    self.publish_capture(name, capture)
                    last_id, last_ms = capture.observation_id, capture.observed_at_ms
            except (
                grpc.RpcError,
                ContractError,
                RuntimeError,
                ValueError,
                TypeError,
                AttributeError,
                OSError,
            ) as error:
                if self.stop_event.is_set():
                    break
                if (
                    isinstance(error, grpc.RpcError)
                    and error.code() == grpc.StatusCode.DEADLINE_EXCEEDED
                ):
                    continue
                reason = type(error).__name__
                if isinstance(error, ContractError):
                    reason = str(error)  # Locally defined messages contain no upstream data.
                elif isinstance(error, grpc.RpcError):
                    reason = error.code().name  # Never print transport details or credentials.
                self.get_logger().warning(
                    f"{name} RGB-D source unavailable ({reason}); no replacement measurements published"
                )
                self.stop_event.wait(1)

    def publish_capture(self, name, capture):
        frame = f"{name}_camera_optical"
        header = Header(stamp=stamp_ms(capture.observed_at_ms), frame_id=frame)
        image = Image(
            header=header,
            height=capture.height,
            width=capture.width,
            encoding="rgb8",
            is_bigendian=False,
            step=capture.width * 3,
            data=capture.rgb,
        )
        depth = Image(
            header=header,
            height=capture.height,
            width=capture.width,
            encoding="32FC1",
            is_bigendian=False,
            step=capture.width * 4,
            data=capture.depth,
        )
        info = CameraInfo(
            header=header,
            height=capture.height,
            width=capture.width,
            distortion_model="plumb_bob",
            d=[0.0] * 5,
        )
        info.k = capture.intrinsics.reshape(-1).tolist()
        info.r = np.eye(3).reshape(-1).tolist()
        info.p = np.column_stack((capture.intrinsics, np.zeros(3))).reshape(-1).tolist()
        transform = TransformStamped(
            header=Header(stamp=header.stamp, frame_id="base_link"), child_frame_id=frame
        )
        xyz = capture.base_from_camera[:3, 3]
        quat = matrix_quaternion(capture.base_from_camera[:3, :3])
        (
            transform.transform.translation.x,
            transform.transform.translation.y,
            transform.transform.translation.z,
        ) = map(float, xyz)
        (
            transform.transform.rotation.w,
            transform.transform.rotation.x,
            transform.transform.rotation.y,
            transform.transform.rotation.z,
        ) = map(float, quat)
        self.tf.sendTransform(transform)
        with self.odom_lock:
            if capture.observed_at_ms > self.last_odom_ms:
                pose = capture.base_pose
                odom = Odometry(
                    header=Header(stamp=header.stamp, frame_id="odom"), child_frame_id="base_link"
                )
                odom.pose.pose.position.x, odom.pose.pose.position.y, odom.pose.pose.position.z = (
                    pose[:3]
                )
                (
                    odom.pose.pose.orientation.w,
                    odom.pose.pose.orientation.x,
                    odom.pose.pose.orientation.y,
                    odom.pose.pose.orientation.z,
                ) = pose[3:]
                # Explicitly simulation wheel odometry, not localization confidence.
                odom.pose.covariance = [0.0] * 36
                for axis in (0, 7, 14):
                    odom.pose.covariance[axis] = 0.0001
                for axis in (21, 28, 35):
                    odom.pose.covariance[axis] = 0.0001
                if self.last_odom_pose is not None:
                    elapsed = (capture.observed_at_ms - self.last_odom_ms) / 1000
                    current_rotation = quaternion_matrix(pose[3:])
                    body_velocity = current_rotation.T @ (
                        (np.asarray(pose[:3]) - np.asarray(self.last_odom_pose[:3])) / elapsed
                    )
                    (
                        odom.twist.twist.linear.x,
                        odom.twist.twist.linear.y,
                        odom.twist.twist.linear.z,
                    ) = map(float, body_velocity)
                    previous_rotation = quaternion_matrix(self.last_odom_pose[3:])
                    delta = previous_rotation.T @ current_rotation
                    odom.twist.twist.angular.z = float(
                        np.arctan2(delta[1, 0], delta[0, 0]) / elapsed
                    )
                self.odom.publish(odom)
                tf = TransformStamped(header=odom.header, child_frame_id="base_link")
                (
                    tf.transform.translation.x,
                    tf.transform.translation.y,
                    tf.transform.translation.z,
                ) = pose[:3]
                tf.transform.rotation = odom.pose.pose.orientation
                self.tf.sendTransform(tf)
                self.last_odom_ms = capture.observed_at_ms
                self.last_odom_pose = pose
        publishers = self.camera_publishers[name]
        publishers["rgb_unfiltered"].publish(image)
        if capture.robot_self_mask:
            rgb = np.frombuffer(capture.rgb, dtype=np.uint8).reshape(-1, 3).copy()
            rgb[np.frombuffer(capture.robot_self_mask, dtype=np.uint8) == 1] = 0
            image.data = rgb.tobytes()
        publishers["self_filter"].publish(
            String(
                data=json.dumps(
                    {
                        "available": bool(capture.robot_self_mask),
                        "modelRevision": capture.self_filter_model_revision,
                        "maskedPixels": capture.robot_self_mask.count(1),
                        "observedAtUnixMs": capture.observed_at_ms,
                    }
                )
            )
        )
        publishers["rgb"].publish(image)
        publishers["depth"].publish(depth)
        publishers["info"].publish(info)
        metric = np.frombuffer(capture.depth, dtype="<f4").reshape(capture.height, capture.width)
        stride = max(1, int(np.ceil(np.sqrt(metric.size / 6000))))
        yy, xx = np.mgrid[0 : capture.height : stride, 0 : capture.width : stride]
        zz = metric[::stride, ::stride]
        valid = np.isfinite(zz) & (zz >= 0.08) & (zz <= 5)
        points = np.column_stack(
            (
                (xx[valid] - info.k[2]) * zz[valid] / info.k[0],
                (yy[valid] - info.k[5]) * zz[valid] / info.k[4],
                zz[valid],
            )
        ).astype(np.float32)
        publishers["points"].publish(create_cloud_xyz32(header, points))

    def destroy_node(self):
        self.stop_event.set()
        self.channel.close()
        for thread in self.threads:
            thread.join(timeout=2)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = RuntimeRgbdBridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
