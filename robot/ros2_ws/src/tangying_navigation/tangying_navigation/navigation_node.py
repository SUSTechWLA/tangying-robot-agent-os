"""Nav2 action adapter and measured RTAB-Map health, exposed on a local HTTP port."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path

import numpy as np
import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist, TwistStamped
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.action import ActionClient
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.duration import Duration
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from rtabmap_msgs.msg import Info
from sensor_msgs.msg import Image
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener

from .contracts import (
    VelocityGate,
    localized_goal_error,
    matrix_quaternion,
    quaternion_matrix,
    visual_quality,
)
from .http_api import GoalRegistry, create_http_server


def now_ms():
    return int(time.time() * 1000)


def message_ms(stamp):
    return stamp.sec * 1000 + stamp.nanosec // 1000000


def pose_list(transform):
    t, q = transform.translation, transform.rotation
    return [t.x, t.y, t.z, q.w, q.x, q.y, q.z]


class NavigationNode(Node):
    def __init__(self):
        super().__init__("navigation_http")
        self.declare_parameter("mode", "mapping")
        self.mode = self.get_parameter("mode").value
        # ROS 2 may auto-declare use_sim_time from the launch parameter file;
        # avoid declaring the same parameter twice while keeping a default for
        # direct unit-test/CLI construction.
        if not self.has_parameter("use_sim_time"):
            self.declare_parameter("use_sim_time", False)
        self.use_sim_time = bool(self.get_parameter("use_sim_time").value)
        if not self.has_parameter("scene"):
            self.declare_parameter("scene", "tabletop")
        self.scene = str(self.get_parameter("scene").value)
        self.declare_parameter("actuation_mode", "native_http")
        self.actuation_mode = self.get_parameter("actuation_mode").value
        for key, default in {
            "base_depth_topic": "/camera/base/depth/image_raw",
            "head_depth_topic": "/camera/head/depth/image_raw",
            "odom_topic": "/odom",
            "cmd_vel_topic": "/tangying/navigation/cmd_vel",
        }.items():
            self.declare_parameter(key, default)
        self.robot_id = os.environ.get("TANGYING_RUNTIME_ROBOT_ID", "xlerobot-mujoco-tabletop")
        self.lock = threading.RLock()
        self.buffer = Buffer(cache_time=Duration(seconds=10))
        self.listener = TransformListener(self.buffer, self)
        self.action = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self.goal_handles = {}
        self.goal_map_poses = {}
        self.cancelled = set()
        self.map_cells = None
        self.map_data = {}
        self.map_received_ms = 0
        self.info_ms = 0
        self.info_ref = 0
        self.visual_quality = visual_quality({})
        self.last_readiness_blockers = None
        self.localization_ms = 0
        self.localization_covariance_ok = False
        self.sensor_ms = {"base": 0, "head": 0}
        self.self_filter = {}
        self.odom_ms = 0
        self.velocity_gate = VelocityGate()
        # MultiThreadedExecutor alone still serializes the default group.
        # Sensor/map callbacks and SQLite goal transitions must not starve
        # receipt of fresh velocity samples or the independent stop watchdog.
        self.velocity_callbacks = MutuallyExclusiveCallbackGroup()
        self.watchdog_callbacks = MutuallyExclusiveCallbackGroup()
        self.latest_velocity = {"linearX": 0.0, "linearY": 0.0, "angularZ": 0.0, "stampUnixMs": 0}
        map_qos = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            reliability=ReliabilityPolicy.RELIABLE,
        )
        self.create_subscription(OccupancyGrid, "/map", self.on_map, map_qos)
        self.create_subscription(Info, "/rtabmap/info", self.on_info, 10)
        self.create_subscription(
            PoseWithCovarianceStamped, "/rtabmap/localization_pose", self.on_localization, 10
        )
        self.create_subscription(Odometry, self.get_parameter("odom_topic").value, self.on_odom, 10)
        self.create_subscription(
            TwistStamped if self.actuation_mode == "native_http" else Twist,
            self.get_parameter("cmd_vel_topic").value,
            self.on_velocity,
            1,
            callback_group=self.velocity_callbacks,
        )
        for name in ("base", "head"):
            self.create_subscription(
                String,
                f"/camera/{name}/self_filter_status",
                lambda msg, source=name: self.on_self_filter(source, msg),
                1,
            )
            self.create_subscription(
                Image,
                self.get_parameter(f"{name}_depth_topic").value,
                lambda msg, source=name: self.on_sensor(source, msg),
                qos_profile_sensor_data,
            )
        db = os.environ.get("TANGYING_NAVIGATION_GOAL_DATABASE", "/data/maps/navigation.sqlite")
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        self.registry = GoalRegistry(self, db)
        self.http = create_http_server(
            os.environ.get("TANGYING_NAVIGATION_HTTP_HOST", "127.0.0.1"),
            int(os.environ.get("TANGYING_NAVIGATION_PORT", "18790")),
            os.environ.get("TANGYING_NAVIGATION_TOKEN", ""),
            self.registry,
        )
        self.http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.http_thread.start()
        self.create_timer(0.1, self.registry.watchdog, callback_group=self.watchdog_callbacks)

    def clock_ms(self):
        """Return the active ROS clock in the same domain as sensor messages.

        Gazebo publishes simulated timestamps and enables ``use_sim_time``.  A
        wall-clock freshness comparison would mark every bridged frame stale;
        real-robot runs keep the default wall clock and retain the existing
        Unix-millisecond contract.
        """
        if self.use_sim_time:
            return self.get_clock().now().nanoseconds // 1_000_000
        return now_ms()

    def on_map(self, message):
        values = np.asarray(message.data, dtype=np.int8)
        if (
            message.header.frame_id != "map"
            or message.info.width * message.info.height != len(values)
            or not len(values)
            or not np.isfinite(message.info.resolution)
            or message.info.resolution <= 0
            or ((values < -1) | (values > 100)).any()
        ):
            return
        with self.lock:
            self.map_received_ms = message_ms(message.header.stamp)
            p, q = message.info.origin.position, message.info.origin.orientation
            origin = [p.x, p.y, p.z, q.w, q.x, q.y, q.z]
            if not all(math.isfinite(value) for value in origin):
                return
            self.map_cells = values.tolist() if len(values) <= 262144 else None
            self.map_data = {
                "origin": origin,
                "width": message.info.width,
                "height": message.info.height,
                "resolution": message.info.resolution,
                "knownCells": int((values >= 0).sum()),
                "observedAtUnixMs": self.map_received_ms,
                "mapRevision": hashlib.sha256(
                    values.tobytes()
                    + json.dumps(
                        [message.info.width, message.info.height, message.info.resolution, origin]
                    ).encode()
                ).hexdigest(),
            }

    def on_info(self, message):
        with self.lock:
            self.info_ms = message_ms(message.header.stamp)
            self.info_ref = message.ref_id
            self.visual_quality = visual_quality(
                dict(zip(message.stats_keys, message.stats_values))
            )

    def on_localization(self, message):
        covariance = [message.pose.covariance[index] for index in (0, 7, 35)]
        with self.lock:
            self.localization_ms = message_ms(message.header.stamp)
            self.localization_covariance_ok = all(
                math.isfinite(value) and 0 <= value <= 0.25 for value in covariance
            )

    def on_sensor(self, source, message):
        with self.lock:
            self.sensor_ms[source] = message_ms(message.header.stamp)

    def on_odom(self, message):
        with self.lock:
            self.odom_ms = message_ms(message.header.stamp)

    def on_self_filter(self, source, message):
        try:
            status = json.loads(message.data)
            if not isinstance(status, dict) or type(status.get("available")) is not bool:
                return
            with self.lock:
                self.self_filter[source] = {
                    "available": status["available"],
                    "modelRevision": str(status.get("modelRevision", ""))[:256],
                    "observedAtUnixMs": int(status.get("observedAtUnixMs", 0)),
                    "maskedPixels": max(0, int(status.get("maskedPixels", 0))),
                }
        except (ValueError, TypeError, OverflowError):
            return

    def on_velocity(self, message):
        received_ms = self.clock_ms()
        stamp = (
            message_ms(message.header.stamp) if self.actuation_mode == "native_http" else self.clock_ms()
        )
        twist = message.twist if self.actuation_mode == "native_http" else message
        values = [twist.linear.x, twist.linear.y, twist.angular.z]
        with self.lock:
            active = self.registry.active_id
            accepted = active in self.goal_handles and active not in self.cancelled
            valid = accepted and self.velocity_gate.observe(active, values, stamp, self.clock_ms())
        if active and os.environ.get("TANGYING_NAVIGATION_TRACE_VELOCITY") == "1":
            self.get_logger().info(
                "navigation velocity " + json.dumps({"stampUnixMs": stamp,
                    "receivedAtUnixMs": received_ms, "checkedAtUnixMs": self.clock_ms(),
                    "acceptedGoal": accepted, "valid": valid})
            )
        if valid:
            self.registry.update(active, "RUNNING")

    def velocity(self):
        with self.lock:
            return self.velocity_gate.read(self.registry.active_id)

    def map_status(self, include_grid=False):
        with self.lock:
            data = dict(self.map_data)
            sensors = dict(self.sensor_ms)
            self_filter = {name: dict(value) for name, value in self.self_filter.items()}
            if include_grid:
                if self.map_cells is None:
                    data["gridUnavailable"] = "MAP_TOO_LARGE_OR_NOT_YET_RECEIVED"
                else:
                    data["cells"] = list(self.map_cells)
            cancellation_pending = bool(self.cancelled)
            info_ms, info_ref, odom_ms = self.info_ms, self.info_ref, self.odom_ms
            quality = dict(self.visual_quality)
            localization_ms, covariance_ok = self.localization_ms, self.localization_covariance_ok
        current = self.clock_ms()
        fresh = lambda stamp, limit: -250 <= current - stamp <= limit
        pose, pose_stamp = None, 0
        try:
            transform = self.buffer.lookup_transform("map", "base_link", Time())
            pose, pose_stamp = pose_list(transform.transform), message_ms(transform.header.stamp)
        except TransformException:
            pose, pose_stamp = None, 0
        localization_ready = self.mode == "mapping" or (
            fresh(localization_ms, 2500) and covariance_ok
        )
        checks = {
            "MAP_EMPTY": bool(data.get("knownCells", 0)),
            "RTABMAP_PROCESSING_STALE": fresh(info_ms, 2500),
            "RTABMAP_REFERENCE_MISSING": info_ref > 0,
            "VISUAL_QUALITY_LOW": quality["ready"],
            "MAP_POSE_STALE": fresh(pose_stamp, 1000),
            "ODOMETRY_STALE": fresh(odom_ms, 1000),
            "BASE_RGBD_STALE": fresh(sensors["base"], 1000),
            "HEAD_RGBD_STALE": fresh(sensors["head"], 1000),
            "LOCALIZATION_UNAVAILABLE": localization_ready,
            "NAV2_ACTION_UNAVAILABLE": self.action.server_is_ready(),
            "CANCELLATION_PENDING": not cancellation_pending,
        }
        blockers = [name for name, valid in checks.items() if not valid]
        ready = not blockers
        ages = {
            "rtabmap": current - info_ms,
            "mapPose": current - pose_stamp,
            "odometry": current - odom_ms,
            **{name: current - stamp for name, stamp in sensors.items()},
        }
        if blockers and tuple(blockers) != self.last_readiness_blockers and rclpy.ok():
            self.get_logger().warning(
                "navigation readiness "
                + json.dumps(
                    {
                        "checkedAtUnixMs": current,
                        "blockers": blockers,
                        "ageMs": ages,
                        "visualQuality": quality,
                    }
                )
            )
        self.last_readiness_blockers = tuple(blockers)
        return {
            **data,
            "ready": ready,
            "mode": self.mode,
            "scene": self.scene,
            "frameId": "map",
            "robotId": self.robot_id,
            "mapPose": pose,
            "poseSource": "rtabmap_tf",
            "poseObservedAtUnixMs": pose_stamp,
            "actuationMode": self.actuation_mode,
            "sensorObservedAtUnixMs": sensors,
            "selfFilter": self_filter,
            "visualQuality": quality,
            "readinessBlockers": blockers,
            "inputAgeMs": ages,
            "checkedAtUnixMs": current,
            "odomObservedAtUnixMs": odom_ms,
            "localizationState": "MAPPING_ODOMETRY"
            if self.mode == "mapping" and ready
            else "LOCALIZED"
            if localization_ready and ready
            else "UNAVAILABLE",
        }

    def start(self, goal_id, request):
        pose = np.asarray(request["goalPose"], dtype=float)
        if request["frameId"] == "odom":
            tf = self.buffer.lookup_transform("map", "odom", Time())
            if not -250 <= self.clock_ms() - message_ms(tf.header.stamp) <= 1000:
                raise ValueError("map transform stale")
            offset = pose_list(tf.transform)
            rotation = quaternion_matrix(offset[3:])
            pose[:3] = rotation @ pose[:3] + offset[:3]
            pose[3:] = matrix_quaternion(rotation @ quaternion_matrix(pose[3:]))
        with self.lock:
            self.goal_map_poses[goal_id] = pose.tolist()
            self.latest_velocity = {
                "linearX": 0.0,
                "linearY": 0.0,
                "angularZ": 0.0,
                "stampUnixMs": self.clock_ms(),
            }
        # This goal is now bound to one map pose. Confirm an already reached
        # location using the same fresh sensors/TF required for navigation,
        # without issuing an action or accepting any motor velocity lease.
        world = self.map_status()
        distance, angle = localized_goal_error(world, pose.tolist(), self.clock_ms())
        if distance <= .015 and angle <= .04:
            self.registry.update(
                goal_id, "SUCCEEDED", "POSE_ALREADY_CONFIRMED",
                completion_source="pose_confirmation",
                completion_pose_stamp=world["poseObservedAtUnixMs"],
            )
            return
        stamped = PoseStamped()
        stamped.header.frame_id = "map"
        stamped.header.stamp = self.get_clock().now().to_msg()
        stamped.pose.position.x, stamped.pose.position.y, stamped.pose.position.z = map(
            float, pose[:3]
        )
        (
            stamped.pose.orientation.w,
            stamped.pose.orientation.x,
            stamped.pose.orientation.y,
            stamped.pose.orientation.z,
        ) = map(float, pose[3:])
        future = self.action.send_goal_async(NavigateToPose.Goal(pose=stamped))
        future.add_done_callback(lambda result: self.goal_response(goal_id, result))

    def goal_pose_map(self, goal_id):
        with self.lock:
            return self.goal_map_poses.get(goal_id)

    def goal_response(self, goal_id, future):
        try:
            handle = future.result()
            if not handle.accepted:
                with self.lock:
                    self.cancelled.discard(goal_id)
                self.registry.update(goal_id, "FAILED", "NAV2_REJECTED")
                return
            with self.lock:
                self.goal_handles[goal_id] = handle
                self.velocity_gate.accept(goal_id, self.clock_ms())
                cancelled = goal_id in self.cancelled
            if cancelled:
                handle.cancel_goal_async()
            handle.get_result_async().add_done_callback(
                lambda result: self.goal_result(goal_id, result)
            )
        except Exception:  # noqa: BLE001 — failed ROS action futures must terminate the goal.
            with self.lock:
                self.cancelled.discard(goal_id)
            self.registry.update(goal_id, "FAILED", "NAV2_ACTION_UNAVAILABLE")

    def goal_result(self, goal_id, future):
        try:
            status = future.result().status
            state = {
                GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
                GoalStatus.STATUS_CANCELED: "CANCELLED",
            }.get(status, "FAILED")
            self.registry.update(
                goal_id, state, "" if state == "SUCCEEDED" else "NAV2_ACTION_ENDED",
                completion_source="nav2_action" if state == "SUCCEEDED" else "",
                completion_pose_stamp=(
                    self.map_status().get("poseObservedAtUnixMs", 0) if state == "SUCCEEDED" else 0
                ),
            )
        except Exception:  # noqa: BLE001 — failed ROS action futures must terminate the goal.
            self.registry.update(goal_id, "FAILED", "NAV2_RESULT_UNAVAILABLE")
        with self.lock:
            self.velocity_gate.clear(goal_id)
            self.goal_handles.pop(goal_id, None)
            self.cancelled.discard(goal_id)

    def cancel(self, goal_id):
        with self.lock:
            self.cancelled.add(goal_id)
            self.velocity_gate.clear(goal_id)
            handle = self.goal_handles.get(goal_id)
            self.latest_velocity = {
                "linearX": 0.0,
                "linearY": 0.0,
                "angularZ": 0.0,
                "stampUnixMs": self.clock_ms(),
            }
        if handle is not None:
            handle.cancel_goal_async()

    def destroy_node(self):
        self.http.shutdown()
        self.http.server_close()
        self.registry.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = NavigationNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
