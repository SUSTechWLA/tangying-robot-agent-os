"""Actual gRPC -> ROS DDS image/cloud/odom/TF probe; run inside a Jazzy image."""

import itertools
import os
import time
from concurrent.futures import ThreadPoolExecutor

import grpc
import numpy as np
import rclpy
from nav_msgs.msg import Odometry
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, PointCloud2
from sensor_msgs_py.point_cloud2 import read_points
from tangying_navigation.rgbd_bridge import RuntimeRgbdBridge
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc
from tf2_ros import Buffer, TransformListener


def camera_masked(source):
    return "base" in source or os.environ.get("TANGYING_PROBE_HEAD_SELF_MASK") == "1"


class Runtime(robot_pb2_grpc.RobotRuntimeServicer):
    def __init__(self):
        self.requests = {f"navigation-probe/{name}-rgbd": [] for name in ("base", "head")}
        self.last_stamps = {}

    def GetRuntimeInfo(self, request, context):
        return robot_pb2.RuntimeInfo(
            robot_id="navigation-probe",
            robot_profile={
                "sensors": [
                    {"sourceId": f"navigation-probe/{name}-rgbd", "sourceType": "rgbd_camera"}
                    for name in ("base", "head")
                ]
            },
        )

    def Observe(self, request, context):
        assert list(request.streams) == ["rgbd_raw", "sensor_only"]
        assert request.source_id in {"navigation-probe/base-rgbd", "navigation-probe/head-rgbd"}
        self.requests[request.source_id].append(time.monotonic())
        sequence = len(self.requests[request.source_id]) * 1000
        while context.is_active():
            sequence += 1
            stamp = int(time.time() * 1000)
            if os.environ.get("TANGYING_PROBE_DUPLICATE") == "1" and len(self.requests[request.source_id]) == 2:
                stamp = self.last_stamps[request.source_id]
            else:
                self.last_stamps[request.source_id] = stamp
            yield robot_pb2.Observation(
                observation_id=f"{request.source_id}/{sequence}",
                wall_time_unix_ms=stamp,
                reconstruction={"sourceId": request.source_id},
                robot_state={"base_pose": [0, 0, 0, 2**-0.5, 0, 0, 2**-0.5]},
                rgbd_frame=robot_pb2.RGBDFrame(
                    width=2,
                    height=2,
                    rgb=bytes([255, 0, 17] * 4),
                    depth_metres_f32=np.array([1, np.nan, 0.5, 2], dtype="<f4").tobytes(),
                    intrinsics=[100, 0, 1, 0, 100, 1, 0, 0, 1],
                    base_from_camera=[0, 0, 1, 0.1, -1, 0, 0, 0, 0, -1, 0, 0.2, 0, 0, 0, 1],
                    robot_self_mask=bytes([1, 0, 0, 0])
                    if camera_masked(request.source_id)
                    else b"",
                    self_filter_model_revision="test-robot-cad-v1"
                    if camera_masked(request.source_id)
                    else "",
                ),
            )
            if os.environ.get("TANGYING_PROBE_SNAPSHOT") == "1":
                return  # Real Native Runtime sends one snapshot and ends the stream.
            time.sleep(0.2)


def main():
    server = grpc.server(ThreadPoolExecutor(max_workers=4))
    runtime = Runtime()
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(runtime, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    os.environ.update(
        TANGYING_RUNTIME_ADDRESS=f"127.0.0.1:{port}",
        TANGYING_RUNTIME_INSECURE="1",
        TANGYING_RUNTIME_ROBOT_ID="navigation-probe",
    )
    rclpy.init()
    listener = Node("rgbd_probe_listener")
    buffer = Buffer()
    _tf_listener = TransformListener(buffer, listener)
    received = {}
    for name in ("base", "head"):
        for kind, message, suffix in [
            ("rgb", Image, "rgb/image_raw"),
            ("rgb_unfiltered", Image, "rgb/image_unfiltered"),
            ("depth", Image, "depth/image_raw"),
            ("points", PointCloud2, "points"),
        ]:
            listener.create_subscription(
                message,
                f"/camera/{name}/{suffix}",
                lambda value, key=f"{name}-{kind}": received.__setitem__(key, value),
                qos_profile_sensor_data,
            )
    listener.create_subscription(
        Odometry, "/odom", lambda value: received.__setitem__("odom", value), 10
    )
    bridge = RuntimeRgbdBridge()
    executor = SingleThreadedExecutor()
    executor.add_node(listener)
    executor.add_node(bridge)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (
            len(received) < 9
            or not buffer.can_transform("odom", "base_camera_optical", rclpy.time.Time())
            or (
                os.environ.get("TANGYING_PROBE_SNAPSHOT") == "1"
                and min(map(len, runtime.requests.values())) < 6
            )
        ):
            executor.spin_once(timeout_sec=0.1)
        assert len(received) == 9, sorted(received)
        if os.environ.get("TANGYING_PROBE_SNAPSHOT") == "1":
            for source, requests in runtime.requests.items():
                assert len(requests) >= 6, source
                assert min(b - a for a, b in itertools.pairwise(requests)) >= 0.19, source
                if os.environ.get("TANGYING_PROBE_DUPLICATE") == "1":
                    # A repeated capture with a new sequence is discarded;
                    # it must not force a one-second source outage/backoff.
                    assert max(b - a for a, b in itertools.pairwise(requests[:6])) < 0.8, source
        for name in ("base", "head"):
            assert bytes(received[f"{name}-rgb_unfiltered"].data) == bytes([255, 0, 17] * 4)
            expected_rgb = (
                bytes([0, 0, 0] + [255, 0, 17] * 3)
                if camera_masked(name)
                else bytes([255, 0, 17] * 4)
            )
            assert bytes(received[f"{name}-rgb"].data) == expected_rgb
            metric = np.frombuffer(received[f"{name}-depth"].data, dtype="<f4")
            assert np.isnan(metric[0]) if camera_masked(name) else metric[0] == 1
            assert np.isnan(metric[1]) and metric[2] == 0.5
            points = read_points(received[f"{name}-points"], skip_nans=True)
            assert len(points) == (2 if camera_masked(name) else 3)
        q = received["odom"].pose.pose.orientation
        assert abs(q.w - 2**-0.5) < 1e-7 and abs(q.z - 2**-0.5) < 1e-7
        transform = buffer.lookup_transform("base_link", "base_camera_optical", rclpy.time.Time())
        assert abs(transform.transform.translation.x - 0.1) < 1e-7
        assert abs(transform.transform.translation.z - 0.2) < 1e-7
        print(
            "PASS: actual gRPC raw RGB-D -> both ROS DDS RGB/depth/PointCloud2 + FLU wheel odometry + TF"
        )
    finally:
        executor.remove_node(bridge)
        executor.remove_node(listener)
        executor.shutdown()
        _tf_listener.unregister()
        bridge.destroy_node()
        listener.destroy_node()
        server.stop(0).wait()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
