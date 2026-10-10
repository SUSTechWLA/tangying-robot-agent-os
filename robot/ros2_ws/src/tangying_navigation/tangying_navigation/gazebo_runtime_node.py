"""The Gazebo runtime node: ROS in, ``robot.profile.v1`` out.

This is the adapter that makes Gazebo a backend rather than a separate universe.
The agent's side is unchanged - it speaks the same runtime contract it speaks to
MuJoCo or to a physical unit - and this process is what answers it.

Sensor acquisition, mapping services and canonical skills share this node.
Skills always use the journal and safety supervisor; physical evidence verification
is optional. Undeclared manipulation capabilities are refused by admission.

"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from concurrent import futures

import grpc
import numpy as np
import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image, Imu, JointState, PointCloud2
from sensor_msgs_py.point_cloud2 import read_points_numpy
from std_msgs.msg import Float64, String
from tangying_robot_gateway.gazebo_bridge import GazeboBridgeError, base_from_camera
from tangying_robot_gateway.gazebo_runtime import (
    CameraSample,
    GazeboRuntime,
    GazeboRuntimeError,
    observation_message,
)
from tangying_robot_gateway.gazebo_workflow import (
    GAZEBO_STEP_LINEAR_MPS,
    GAZEBO_STEP_TIMEOUT_S,
    bounded_step_command,
    swept_disc_step_is_clear,
    swept_step_is_clear,
    yaw_from_quaternion,
)
from tangying_robot_proto.robot.v1 import robot_pb2, robot_pb2_grpc

#: Runtime camera name -> (image topic, depth topic, info topic).
#:
#: The names on the left are what the rest of the system already calls these
#: cameras. The topics on the right are the **ROS-side** names from
#: `config/gazebo_house_bridge.yaml` - not the Gazebo-side ones in the world file,
#: which look almost the same and have no ROS publisher at all.
#:
#: That distinction cost a debugging round: subscribing to `/camera/base/image`
#: connects to nothing, because the bridge publishes that name *from* Gazebo and
#: onto `/camera/base/rgb/image_raw`. `ros2 topic info` showing zero publishers is
#: what tells the two apart.
CAMERA_TOPICS = {
    "base-rgbd": ("/camera/base/rgb/image_raw", "/camera/base/depth/image_raw",
                  "/camera/base/rgb/camera_info"),
    "head-rgbd": ("/camera/head/rgb/image_raw", "/camera/head/depth/image_raw",
                  "/camera/head/rgb/camera_info"),
}


def survey_hold_seconds(value):
    if isinstance(value, bool):
        raise TypeError("survey observation hold must be a numeric duration")
    seconds = float(value)
    if not math.isfinite(seconds) or not 0 < seconds <= 15:
        raise ValueError("survey observation hold must be finite and in (0, 15] seconds")
    return seconds


def gazebo_runtime_root():
    return os.environ.get("TANGYING_GAZEBO_RUNTIME_ROOT", "/data/maps/gazebo-runtime")

#: Where each camera is bolted, in the robot base frame, as metres.
#:
#: Read from the `<pose>` of each sensor in `worlds/tangying_home.sdf`, and
#: declared here rather than subscribed to TF: the mount is static, so a TF lookup
#: per frame would add a way to lose a capture in exchange for nothing. If the
#: world moves a camera, this has to move with it - which is why it names the file
#: it came from.
#: Camera -> (x, y, z, tilt down in degrees), relative to base_link.
#:
#: The tilt is part of the mount, not a detail: the base camera is the mapping
#: camera and the reference robot aims it 15 degrees down from 0.16 m so it can see
#: the floor. A level camera measures walls, and the SLAM built on it registers
#: nothing - measured on this world: 28 frames, 0 registrations, map refused.
GAZEBO_CAMERA_MOUNTS = {
    "base-rgbd": (0.36, 0.0, 0.16, 15.0),
    "head-rgbd": (-0.1, 0.0, 1.30, 45.0),
}


def camera_mount(camera: str) -> np.ndarray:
    """The camera *link* pose in the base frame, as a 4x4 matrix.

    The rotation matters. The runtime converts a link pose to the optical frame
    itself, and that conversion assumes the link frame - x forward, y left, z up -
    is the one the mount actually describes. Handing it an identity rotation for a
    camera that is aimed 15 degrees down would put every depth return 15 degrees
    above where it belongs, and the resulting map would look plausible.
    """
    from tangying_robot_gateway.gazebo_commissioning import commissioning
    metadata = commissioning()
    if metadata:
        return np.asarray(metadata["cameras"][camera]["baseFromLink"], dtype=float)
    x, y, z, tilt = GAZEBO_CAMERA_MOUNTS[camera]
    angle = math.radians(tilt)
    mount = np.eye(4)
    mount[:3, :3] = np.array([
        [math.cos(angle), 0.0, math.sin(angle)],
        [0.0, 1.0, 0.0],
        [-math.sin(angle), 0.0, math.cos(angle)],
    ])
    mount[:3, 3] = (x, y, z)
    return mount


def _image_to_array(message: Image) -> np.ndarray:
    """An Image message as an array, honouring its row stride.

    The channel count comes from ``step // width``, not from dividing the buffer
    length by the image size: for an unpadded buffer those are the same number and
    the second one silently yields 1, which turns every colour image into a
    single-channel one that then fails validation much further downstream.

    ``step`` is not always ``width * channels`` either, so the row stride is
    honoured: ignoring it shears the image, and a sheared image still looks like
    an image.
    """
    if message.height <= 0 or message.width <= 0 or message.step < message.width:
        raise ValueError(f"impossible image geometry {message.width}x{message.height} step {message.step}")
    channels = message.step // message.width
    if channels not in (1, 3, 4):
        raise ValueError(f"unsupported channel count {channels} from step {message.step} width {message.width}")
    buffer = np.frombuffer(message.data, dtype=np.uint8)
    needed = message.height * message.step
    if buffer.size < needed:
        raise ValueError(f"image buffer holds {buffer.size} bytes, needs {needed}")
    rows = buffer[:needed].reshape(message.height, message.step)
    return rows[:, : message.width * channels].reshape(message.height, message.width, channels)


def _depth_to_metres(message: Image) -> np.ndarray:
    """Gazebo publishes depth as float32 metres or as uint16 millimetres.

    Both are in use and they are not interchangeable: reading millimetres as
    metres scales every distance by a thousand, which reads as a robot in a
    doll's house.
    """
    stride = message.step
    if message.encoding in {"32FC1", "32FC"}:
        rows = np.frombuffer(message.data, dtype="<f4").reshape(message.height, stride // 4)
        return rows[:, : message.width].astype(np.float64)
    if message.encoding in {"16UC1", "16UC"}:
        rows = np.frombuffer(message.data, dtype="<u2").reshape(message.height, stride // 2)
        return rows[:, : message.width].astype(np.float64) * 0.001
    raise ValueError(f"unsupported depth encoding {message.encoding!r}")


class GazeboRuntimeNode(Node):
    """Keeps one runtime's newest samples up to date from the ROS bridge."""
    def __init__(self) -> None:
        super().__init__("tangying_gazebo_runtime")
        revision = os.environ.get("TANGYING_GAZEBO_CALIBRATION_REVISION", "")
        if not revision:
            raise SystemExit(
                "TANGYING_GAZEBO_CALIBRATION_REVISION is required: a capture is only "
                "comparable against the calibration it was taken with")
        self.runtime = GazeboRuntime(
            robot_id=os.environ.get("TANGYING_RUNTIME_ROBOT_ID", "gazebo-house-rgbd"),
            adapter="gazebo",
            cameras={name:name.split("-")[0]+"_camera_optical_frame" for name in CAMERA_TOPICS},
            calibration_revision=revision,
            software_version=os.environ.get("TANGYING_SOFTWARE_VERSION", "0.7.0"),
            runtime_version="gazebo-bridge-0.1.0",
        )
        self.scene = os.environ.get("TANGYING_GAZEBO_SCENE", "home")
        self._lock = threading.Lock()
        self._pending: dict[str, dict[str, object]] = {name: {} for name in CAMERA_TOPICS}
        self._rgb_encoding: dict[str, str] = {}
        self.joint_positions = {}
        self._joint_history = deque(maxlen=1024)
        self._sensor_clock_history = deque(maxlen=1024)
        self.joint_received_ns = 0
        self._feedback_group = MutuallyExclusiveCallbackGroup()
        self._suction_state = None
        self._suction_received_ns = 0
        self._suction_sequence = 0
        self._suction_command_id = time.time_ns() // 1000
        self._suction_pub = self.create_publisher(String, "/tangying/suction/command", 10)
        self.create_subscription(String, "/tangying/suction/state", self._on_suction,
                                 qos_profile_sensor_data, callback_group=self._feedback_group)
        from tangying_robot_gateway.arm_kinematics import all_links
        self.joint_publishers = {link.motor: self.create_publisher(Float64, "/joint/"+link.motor+"/cmd_pos", 10)
                                 for link in all_links()}
        self.navigation = None
        self._navigation_active = False
        self._actuator_lock = threading.Lock()
        self.bindings = None
        self.ownership_lock = threading.Lock()
        #: Bounded-step state. Empty until `enable_bounded_motion` is called: this
        #: node serves observation by default and must not hold a velocity
        #: publisher, or a point cloud it will never look at.
        self.cmd_vel_topic = os.environ.get("TANGYING_CMD_VEL_TOPIC", "/cmd_vel")
        self._motion_lock = threading.Lock()
        # Navigation owns the actuator transaction while its same-thread
        # preparation executes a joint chunk; other threads remain excluded.
        self._command_lock = threading.RLock()
        self._odom_stamp_ns = 0
        self._odom_received_ns = 0
        self._odom_history = deque(maxlen=120)
        self._imu = None
        self._obstacles: dict[str, np.ndarray] = {}
        self._obstacle_received_ns = {}
        self._cmd_vel = None
        self.motion_allowed = lambda: True
        self.trace_steps = os.environ.get("TANGYING_TRACE_STEPS") == "1"
        self.survey_observation_hold_seconds = survey_hold_seconds(
            os.environ.get("TANGYING_SURVEY_OBSERVATION_HOLD_SECONDS", "2"))
        self._assembly_timing = {}
        self._bounded_evidence_path = os.path.join(
            gazebo_runtime_root(), "bounded-steps.jsonl")
        self._last_bounded_evidence = None

        self.create_subscription(Twist, "/navigation/cmd_vel",
                                 self._on_navigation_velocity, 10)
        self.create_subscription(JointState, "/joint_states", self._on_joints,
                                 qos_profile_sensor_data, callback_group=self._feedback_group)
        self.create_subscription(Odometry, "/odom", self._on_odometry,
                                 qos_profile_sensor_data, callback_group=self._feedback_group)
        self.create_subscription(Imu, "/imu", self._on_imu,
                                 qos_profile_sensor_data, callback_group=self._feedback_group)
        for name, (rgb_topic, depth_topic, info_topic) in CAMERA_TOPICS.items():
            self.create_subscription(Image, rgb_topic,
                                     lambda message, camera=name: self._on_image(camera, message),
                                     qos_profile_sensor_data)
            self.create_subscription(Image, depth_topic,
                                     lambda message, camera=name: self._on_depth(camera, message),
                                     qos_profile_sensor_data)
            self.create_subscription(CameraInfo, info_topic,
                                     lambda message, camera=name: self._on_info(camera, message),
                                     qos_profile_sensor_data)
        # This timer shares the image/default callback group, not the safety
        # feedback group. Only the latest pending pair per camera is retained.
        self._assembly_timer = self.create_timer(.02, self._assemble_pending)
        self.get_logger().info(
            f"gazebo runtime ready for {sorted(CAMERA_TOPICS)} against calibration {revision[:12]}")

    # -- bounded-step motion ------------------------------------------------
    #
    # The survey's motion primitive, and the ROS half of it. The decisions live in
    # `tangying_robot_gateway.gazebo_workflow` as pure functions, where they are
    # tested without a simulator; what is left here is publishing a velocity and
    # keeping the newest obstacle points.

    def enable_bounded_motion(self) -> None:
        """Start accepting bounded steps, and start guarding them.

        Called only when a mapping catalogue is being hosted. A node that never
        drives anything must not subscribe to a point cloud it will not use, and
        must certainly not hold a velocity publisher nobody owns.
        """
        if self._cmd_vel is not None:
            return
        self._cmd_vel = self.create_publisher(Twist, self.cmd_vel_topic, 10)
        # `nav_points` is the rtabmap_util cloud built from the depth image, so it
        # is in the camera's optical frame - unlike the raw bridged cloud from
        # Gazebo's rgbd_camera, which carries the optical frame *name* but the
        # sensor link's coordinates. The guard needs the convention it was told.
        for camera in GAZEBO_CAMERA_MOUNTS:
            topic = f"/camera/{camera.split('-')[0]}/nav_points"
            self.create_subscription(
                PointCloud2, topic,
                lambda message, name=camera: self._on_obstacles(name, message),
                qos_profile_sensor_data)
        self.get_logger().info(
            f"bounded-step motion enabled on {self.cmd_vel_topic} with a depth guard")

    def _on_obstacles(self, camera: str, message: PointCloud2) -> None:
        """Keep the newest obstacle points, in the robot base frame.

        Transformed here rather than by the consumer because the transform is fixed
        and the cost of doing it per point per control tick would be paid inside the
        control loop.
        """
        try:
            points = read_points_numpy(message, field_names=("x", "y", "z"), skip_nans=True).reshape(-1, 3)
        except Exception as error:  # noqa: BLE001 — a bad cloud must not stop the guard.
            self.get_logger().warn(f"{camera}: obstacle cloud unusable ({error})")
            return
        if not len(points):
            return
        base = self.runtime.base_pose
        if base is None:
            return
        base_from_optical = base_from_camera(base, base @ camera_mount(camera))
        local = (base_from_optical[:3, :3] @ points[:, :3].T).T + base_from_optical[:3, 3]
        from tangying_robot_gateway.gazebo_workflow import remove_chassis_returns
        with self._motion_lock:
            self._obstacles[camera] = remove_chassis_returns(local)
            self._obstacle_received_ns[camera] = time.monotonic_ns()

    def _obstacle_points(self) -> np.ndarray:
        with self._motion_lock:
            clouds = [value for value in self._obstacles.values() if len(value)]
        return np.vstack(clouds) if clouds else np.zeros((0, 3))

    def _log_step(self, text: str) -> None:
        """Per-step trace of the bounded driver, off unless asked for.

        It is off by default because a survey takes hundreds of steps and this is
        noise in a healthy run; it is here at all because "the robot did not move"
        has four different causes and no other signal distinguishes them.
        """
        if self.trace_steps:
            self.get_logger().info(f"bounded step: {text}")

    def _on_navigation_velocity(self, message):
        # A cancelled HTTP action may still emit a queued Nav2 velocity. Closing
        # this gate is synchronous with stop(), before waiting for ROS cancellation.
        with self._actuator_lock:
            if self._navigation_active:
                self._publish_velocity(message.linear.x, message.angular.z, message.linear.y)

    def stop_navigation(self):
        with self._actuator_lock:
            self._navigation_active = False
            self._publish_velocity(0.0, 0.0)

    def _publish_velocity(self, linear_x: float, angular_z: float, linear_y: float = 0.,
                          *, deadline_monotonic=None, cancel=None) -> dict:
        message = Twist()
        # A zero command must not wait for the sensor lock before stopping.
        requested_motion = any(value != 0 for value in (linear_x, linear_y, angular_z))
        allowed = not requested_motion or (self.motion_allowed() and not self.readiness_blockers())
        if requested_motion:
            # The readiness lock itself can block. Recheck authority afterwards,
            # immediately before forming the actual driver publication.
            allowed = (allowed and self.motion_allowed()
                       and (deadline_monotonic is None or time.monotonic() < deadline_monotonic)
                       and (cancel is None or not cancel.is_set()))
        message.linear.x = float(linear_x) if allowed else 0.0
        message.linear.y = float(linear_y) if allowed else 0.0
        message.angular.z = float(angular_z) if allowed else 0.0
        if self._cmd_vel is not None:
            self._cmd_vel.publish(message)
        return {"published": self._cmd_vel is not None,
                "nonzero": bool(message.linear.x or message.linear.y or message.angular.z)}

    def joint_snapshot(self):
        with self._lock:
            return dict(self.joint_positions), (time.monotonic_ns()-self.joint_received_ns)/1e9, self.joint_received_ns

    def readiness_blockers(self):
        return self.readiness_snapshot(detailed=False)["blockers"]

    def readiness_snapshot(self, *, detailed=True):
        waiting = time.monotonic_ns()
        with self._lock:
            # Sample the clock with the sensor snapshot. A callback may refresh
            # timestamps while this reader waits for the lock; an earlier clock
            # would misclassify those fresh samples as having negative ages.
            now = time.monotonic_ns()
            blockers = []
            cameras = {}
            for name in self.runtime.cameras:
                sample = self.runtime._samples.get(name)
                capture_ns = (getattr(sample, "capture_monotonic_ns", 0)
                              or getattr(sample, "received_monotonic_ns", 0))
                cameras[name] = {"ready": sample is not None and 0 <= now-capture_ns <= 1_000_000_000,
                                 "captureAgeMs": (now-capture_ns)/1e6 if sample is not None else None}
                if detailed:
                    parts = getattr(self, "_pending", {}).get(name, {})
                    cameras[name].update({
                        "sourceId": getattr(self.runtime, "robot_id", "")+"/"+name,
                        "captureMonotonicNs": str(capture_ns),
                        "receivedMonotonicNs": str(getattr(sample, "received_monotonic_ns", 0)),
                        "sensorStampNs": str(getattr(sample, "sensor_stamp_ns", 0)),
                        "captureClockSource": getattr(sample, "capture_clock_source", "unavailable"),
                        "callbackReceiptAgeMs": (now-sample.received_monotonic_ns)/1e6 if sample else None,
                        "pendingRGBSensorStampNs": str(parts.get("rgb_stamp", 0)),
                        "pendingDepthSensorStampNs": str(parts.get("depth_stamp", 0)),
                        "pendingRGBCallbackMonotonicNs": str(parts.get("rgb_received_ns", 0)),
                        "pendingDepthCallbackMonotonicNs": str(parts.get("depth_received_ns", 0)),
                        "lastAssembledSensorStampNs": str(parts.get("last_stamp", 0)),
                        "assemblyTiming": dict(getattr(self, "_assembly_timing", {}).get(name, {})),
                    })
            if not all(value["ready"] for value in cameras.values()):
                blockers.append("RGBD_NOT_READY")
            if not self.joint_positions or not 0 <= now-self.joint_received_ns <= 500_000_000:
                blockers.append("JOINT_FEEDBACK_STALE")
            if self._suction_state is None or not 0 <= now-self._suction_received_ns <= 500_000_000:
                blockers.append("SUCTION_FEEDBACK_STALE")
            if self._imu is None or not 0 <= now-self._imu[3] <= 500_000_000:
                blockers.append("IMU_NOT_READY")
            odom_received = getattr(self, "_odom_received_ns", 0)
            if not odom_received or not 0 <= now-odom_received <= 1_000_000_000:
                blockers.append("ODOMETRY_STALE")
            result = {"schemaVersion": "gazebo.readiness.v1", "ready": not blockers,
                      "blockers": blockers, "clockSource": "process_monotonic_ns",
                      "checkedAtMonotonicNs": str(now), "lockWaitMs": (now-waiting)/1e6,
                      "cameras": cameras}
            if detailed:
                result.update({
                    "robotId": getattr(self.runtime, "robot_id", ""),
                    "feedbackAgeMs": {"joint": (now-self.joint_received_ns)/1e6,
                                      "suction": (now-self._suction_received_ns)/1e6,
                                      "imu": (now-self._imu[3])/1e6 if self._imu else None,
                                      "odometry": (now-odom_received)/1e6 if odom_received else None},
                    "odometryReceivedMonotonicNs": str(odom_received),
                    "odometrySensorStampNs": str(getattr(self, "_odom_stamp_ns", 0)),
                    "feedbackReceiptMonotonicNs": {
                        "joint": str(self.joint_received_ns), "suction": str(self._suction_received_ns),
                        "imu": str(self._imu[3]) if self._imu else "0"},
                    "imuSensorStampNs": str(self._imu[2]) if self._imu else "0",
                    "sensorClockBridge": {
                        "jointFirstSensorStampNs": str(self._joint_history[0][0]) if getattr(self, "_joint_history", []) else "0",
                        "jointLastSensorStampNs": str(self._joint_history[-1][0]) if getattr(self, "_joint_history", []) else "0",
                        "odomFirstSensorStampNs": str(self._odom_history[0][0]) if getattr(self, "_odom_history", []) else "0",
                        "odomLastSensorStampNs": str(self._odom_history[-1][0]) if getattr(self, "_odom_history", []) else "0",
                    },
                })
            return result

    def runtime_diagnostics(self):
        # Keep current state and historical causes explicitly separate. Neither
        # a later fresh sample nor an empty post-restart cache resolves UNKNOWN.
        return {**self.readiness_snapshot(), "scope": "current_sensor_state",
                "phase": "read_only_diagnostics",
                "historicalBoundedStep": getattr(self, "_last_bounded_evidence", None),
                "evidenceIndex": {
                    "boundedStepJournal": getattr(self, "_bounded_evidence_path", ""),
                    "format": "append-only-jsonl",
                    "cachedTransitionScope": "current_process_only",
                    "emptyCacheDoesNotMeanEmptyHistory": True,
                }}

    def _record_bounded_evidence(self, record):
        encoded = json.dumps(record, allow_nan=False, separators=(",", ":"))
        path = getattr(self, "_bounded_evidence_path", None)
        if path:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND
                                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(encoded+"\n")
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(os.path.dirname(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        # Publish an immutable copy only after the append succeeds. Failure
        # propagates through the existing stop/failure path, never fake durability.
        self._last_bounded_evidence = json.loads(encoded)
        self.get_logger().info("bounded step evidence " + encoded)

    def _on_suction(self, message):
        try:
            state = json.loads(message.data)
            if state.get("schemaVersion") != "gazebo.suction.v1":
                return
            sequence = int(state["sequence"])
            with self._lock:
                if sequence <= self._suction_sequence:
                    return
                self._suction_sequence = sequence
                self._suction_state = state
                self._suction_received_ns = time.monotonic_ns()
        except (ValueError, KeyError, TypeError):
            return

    def suction_snapshot(self):
        return self.suction_evidence_snapshot()[0]

    def suction_evidence_snapshot(self):
        with self._lock:
            if self._suction_state is None or time.monotonic_ns()-self._suction_received_ns > 500_000_000:
                raise ValueError("SUCTION_FEEDBACK_STALE")
            return dict(self._suction_state), self._suction_received_ns

    def suction_command(self, operation, *, side="", target=""):
        with self._actuator_lock:
            if operation != "hold" and not self.motion_allowed():
                raise ValueError("COMMAND_STOPPED")
            self._suction_command_id += 1
            request = {"id": self._suction_command_id, "op": operation,
                       "side": side, "object": target, "expiresMs": int(time.time()*1000)+500}
            self._suction_pub.publish(String(data=json.dumps(request)))
            return request["id"]

    def send_joint_targets(self, targets):
        if not self.motion_allowed():
            return
        for name, value in targets.items():
            self.joint_publishers[name].publish(Float64(data=float(value)))

    def hold_joints(self):
        positions, age, _ = self.joint_snapshot()
        if age <= .5:
            for name, value in positions.items():
                if name in self.joint_publishers:
                    self.joint_publishers[name].publish(Float64(data=float(value)))

    def _on_joints(self, message):
        values = dict(zip(message.name, message.position, strict=True))
        if not all(math.isfinite(v) for v in values.values()):
            return
        with self._lock:
            self.joint_positions = values
            self.joint_received_ns = time.monotonic_ns()
            self._joint_stamp_ns = message.header.stamp.sec*1_000_000_000+message.header.stamp.nanosec
            if self._joint_stamp_ns > 0 and (not self._joint_history or self._joint_stamp_ns > self._joint_history[-1][0]):
                self._joint_history.append((self._joint_stamp_ns, dict(values)))
                self._sensor_clock_history.append((self._joint_stamp_ns,
                    (int(time.time()*1000), self.joint_received_ns)))

    def navigate(self, goal, command_id, cancel, deadline_s=300.):
        if self.navigation is None:
            return {"ok": False, "code": "NAVIGATION_UNAVAILABLE"}
        if not self._command_lock.acquire(blocking=False):
            return {"ok": False, "code": "ROBOT_BUSY"}
        try:
            prepared = self.prepare_navigation(cancel)
            if not prepared.success:
                return {"ok": False, "code": prepared.code, "message": prepared.message}
            with self._actuator_lock:
                self._navigation_active = True
            return self.navigation.navigate(goal, command_id=command_id, cancel=cancel, deadline_s=deadline_s)
        finally:
            self.stop_navigation()
            self._command_lock.release()

    def bounded_step(self, goal, cancel) -> dict:
        if not self._command_lock.acquire(blocking=False):
            return {"ok": False, "code": "ROBOT_BUSY", "message": "另一个移动动作正在执行。"}
        try:
            prepared = self.prepare_navigation(cancel)
            if not prepared.success:
                return {"ok": False, "code": prepared.code, "message": prepared.message}
            return self._bounded_step(goal, cancel)
        finally:
            self._command_lock.release()

    def _bounded_step(self, goal, cancel) -> dict:
        """Drive one bounded step under a depth guard, and say how it ended.

        The guard is checked **before** the step and on every control tick, because
        the survey plans on the map it has built while this guard uses the raw
        measurement: where the two disagree the measurement wins, and the step is
        refused with the code the survey's refusal rule is written for.
        """
        target = np.asarray([float(value) for value in goal], dtype=float)
        if target.shape != (7,) or not np.isfinite(target).all():
            return {"ok": False, "code": "INVALID_GOAL", "message": "目标位姿不合法。"}
        deadline = time.monotonic() + GAZEBO_STEP_TIMEOUT_S
        hold_budget = getattr(self, "survey_observation_hold_seconds", 2.0)
        workflow = getattr(self, "workflow", None)
        evidence = {"schemaVersion": "gazebo.bounded-step.v1",
                    "stepStartedMonotonicNs": str(time.monotonic_ns()),
                    "clockSource": "process_monotonic_ns", "processId": os.getpid(),
                    "operationId": getattr(workflow, "operation_id", ""),
                    "operationOwnerRequestId": getattr(workflow, "_operation_owner", ""),
                    "goalPose": target.tolist(), "holdBudgetSeconds": hold_budget,
                    "nonzeroPublishedPulseCount": 0, "zeroPublishedCount": 0,
                    "finalZeroPublished": False, "observationHoldCount": 0,
                    "observationHolds": []}

        def emit(kind, **fields):
            self._record_bounded_evidence(
                {**evidence, "event": kind, "scope": "historical_bounded_step_transition",
                 "checkedAtMonotonicNs": str(time.monotonic_ns()), **fields})

        def zero():
            receipt = self._publish_velocity(0.0, 0.0)
            published = bool(receipt and receipt.get("published") and not receipt.get("nonzero"))
            evidence["zeroPublishedCount"] += int(published)
            return published

        def ended(code, message=""):
            evidence["outcomeCode"] = code
            return {"ok": code == "STEP_COMPLETE", "code": code, "message": message,
                    "motionEvidence": evidence}

        stale_since = None
        try:
            while True:
                if not self.motion_allowed():
                    return ended("EMERGENCY_STOP_LATCHED")
                if cancel is not None and cancel.is_set():
                    return ended("CANCELLED", "扫描移动已停止。")
                if time.monotonic() >= deadline:
                    return ended("STEP_TIMEOUT", f"这一步未在 {GAZEBO_STEP_TIMEOUT_S:.0f} 秒内完成。")
                snapshot = self.readiness_snapshot()
                blockers = snapshot["blockers"]
                evidence["lastReadiness"] = snapshot
                with self._motion_lock:
                    now = time.monotonic_ns()
                    clouds_fresh = all(0 <= now-self._obstacle_received_ns.get(camera,0) <= 1_000_000_000
                                       for camera in GAZEBO_CAMERA_MOUNTS)
                    cloud_ages = {camera: (now-self._obstacle_received_ns.get(camera, 0))/1e6
                                  for camera in GAZEBO_CAMERA_MOUNTS}
                if time.monotonic() >= deadline:
                    return ended("STEP_TIMEOUT", f"这一步未在 {GAZEBO_STEP_TIMEOUT_S:.0f} 秒内完成。")
                # Expiry wins even if this delayed check finally sees fresh data.
                if stale_since is not None and time.monotonic()-stale_since >= hold_budget:
                    return ended("SENSOR_STALE", ",".join(blockers) or "observation hold expired")
                if blockers or not clouds_fresh:
                    if not zero():
                        return ended("MOTION_PUBLISHER_UNAVAILABLE")
                    # Only visual input/cloud freshness may wait. Missing joint,
                    # IMU, suction or odometry feedback is an immediate refusal.
                    if set(blockers)-{"RGBD_NOT_READY"}:
                        emit("unsafe_feedback", readiness=snapshot, cloudAgeMs=cloud_ages)
                        return ended("SENSOR_STALE", ",".join(blockers))
                    if stale_since is None:
                        stale_since = time.monotonic()
                        evidence["observationHoldCount"] += 1
                        evidence["observationHolds"].append({
                            "stoppedAtMonotonicNs": str(time.monotonic_ns()),
                            "maxDurationSeconds": hold_budget, "readiness": snapshot,
                            "cloudAgeMs": cloud_ages})
                        del evidence["observationHolds"][:-16]
                        emit("observation_hold")
                    time.sleep(.05)
                    continue
                if stale_since is not None:
                    evidence["observationHolds"][-1]["resumedAtMonotonicNs"] = str(time.monotonic_ns())
                    emit("observation_resumed", readiness=snapshot)
                stale_since = None
                pose = self.runtime.base_pose
                if pose is None:
                    return ended("NO_BASE_POSE", "尚未收到里程计位姿。")
                current = np.array([pose[0, 3], pose[1, 3],
                                    math.atan2(pose[1, 0], pose[0, 0])], dtype=float)
                from tangying_robot_gateway.gazebo_commissioning import commissioning
                home = bool(commissioning())
                from tangying_robot_gateway.home_commissioning import HOME_DRIVE_LIMITS
                command = bounded_step_command(current,target,
                    linear_mps=HOME_DRIVE_LIMITS["surveyLinearMps"] if home else GAZEBO_STEP_LINEAR_MPS,
                    angular_rps=HOME_DRIVE_LIMITS["maxAngularRps"] if home else .20)
                if command is None:
                    return ended("STEP_COMPLETE")
                linear_x, angular_z = command
                # What the body is about to sweep, checked against the newest
                # measurement before a single pulse is sent.
                ahead = float(np.dot(target[:2] - current[:2],
                                     [math.cos(current[2]), math.sin(current[2])]))
                guard = swept_disc_step_is_clear if home else swept_step_is_clear
                clear = guard(
                    self._obstacle_points(),
                    forward_m=max(0.0, min(ahead, abs(linear_x) * 0.4)) if linear_x else 0.0,
                    turn_rad=angular_z * 0.4 if angular_z else 0.0,
                    **({"radius":.355,"height_band":(.02,.40)} if home else {}))
                if not clear:
                    zero()
                    self._log_step(
                        f"REFUSED target=({target[0]:.2f},{target[1]:.2f}) "
                        f"at=({current[0]:.2f},{current[1]:.2f},{current[2]:+.2f}) "
                        f"goal_yaw={yaw_from_quaternion(target):+.2f} "
                        f"cmd=({linear_x:.3f},{angular_z:.3f}) points={len(self._obstacle_points())}")
                    return ended("NAV_ENVELOPE", "安全包络内有障碍，这一步被拒绝。")
                # Geometry work and callback scheduling can cross the deadline.
                # Never emit one final pulse after the authority has expired.
                if time.monotonic() >= deadline:
                    return ended("STEP_TIMEOUT", f"这一步未在 {GAZEBO_STEP_TIMEOUT_S:.0f} 秒内完成。")
                if cancel is not None and cancel.is_set():
                    return ended("CANCELLED", "扫描移动已停止。")
                receipt = self._publish_velocity(linear_x, angular_z,
                                                 deadline_monotonic=deadline, cancel=cancel)
                if not receipt or not receipt.get("published"):
                    return ended("MOTION_PUBLISHER_UNAVAILABLE")
                evidence["nonzeroPublishedPulseCount"] += int(receipt["nonzero"])
                self._log_step(
                    f"DRIVE target=({target[0]:.2f},{target[1]:.2f}) "
                    f"at=({current[0]:.2f},{current[1]:.2f},{current[2]:+.2f}) "
                    f"goal_yaw={yaw_from_quaternion(target):+.2f} "
                    f"cmd=({linear_x:.3f},{angular_z:.3f})")
                time.sleep(0.05)
        finally:
            # Always stop. A bounded step that returns while still commanding a
            # velocity is an unbounded one, and the caller has no way to know.
            try:
                evidence["finalZeroPublished"] = zero()
            except Exception as exc:  # noqa: BLE001 — stopping remains best effort.
                self.get_logger().warning(f"Could not publish final stop command: {exc}")
            emit("ended")

    # -- ROS callbacks ------------------------------------------------------

    def _on_imu(self, message: Imu) -> None:
        from scipy.spatial.transform import Rotation
        q = message.orientation
        values = [q.x, q.y, q.z, q.w]
        if not np.isfinite(values).all() or abs(np.linalg.norm(values)-1.) > 1e-3:
            return
        roll, pitch, _ = Rotation.from_quat(values).as_euler("xyz")
        stamp = message.header.stamp.sec*1_000_000_000+message.header.stamp.nanosec
        with self._lock:
            self._imu = (float(roll), float(pitch), stamp, time.monotonic_ns())

    def _on_odometry(self, message: Odometry) -> None:
        pose = message.pose.pose
        translation = np.array([pose.position.x, pose.position.y, pose.position.z])
        quaternion = (pose.orientation.w, pose.orientation.x, pose.orientation.y, pose.orientation.z)
        if abs(np.linalg.norm(quaternion) - 1.0) > 1e-3:
            return      # an unnormalised quaternion is not a pose
        w, x, y, z = quaternion
        rotation = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ])
        matrix = np.eye(4)
        matrix[:3, :3], matrix[:3, 3] = rotation, translation
        with self._lock:
            stamp = message.header.stamp.sec * 1_000_000_000 + message.header.stamp.nanosec
            if self._imu is None or abs(stamp-self._imu[2]) > 200_000_000 or time.monotonic_ns()-self._imu[3] > 500_000_000:
                return  # A planar odometer cannot authorize tilted-camera geometry.
            from scipy.spatial.transform import Rotation
            yaw = math.atan2(rotation[1, 0], rotation[0, 0])
            matrix[:3, :3] = Rotation.from_euler("xyz", [self._imu[0], self._imu[1], yaw]).as_matrix()
            self.runtime.record_base_pose(matrix)
            self._odom_stamp_ns = stamp
            self._odom_received_ns = time.monotonic_ns()
            if not self._odom_history or stamp > self._odom_history[-1][0]:
                self._odom_history.append((stamp,matrix.copy()))

    def _on_image(self, camera: str, message: Image) -> None:
        try:
            rgb = _image_to_array(message)
        except (ValueError, IndexError) as error:
            self.get_logger().warn(f"{camera}: unusable image ({error})")
            return
        with self._lock:
            self._pending[camera]["rgb"] = rgb
            self._pending[camera]["rgb_wall_ms"] = int(time.time()*1000)
            self._pending[camera]["rgb_received_ns"] = time.monotonic_ns()
            self._pending[camera]["rgb_stamp"] = message.header.stamp.sec * 1_000_000_000 + message.header.stamp.nanosec
            self._rgb_encoding[camera] = message.encoding

    def _on_depth(self, camera: str, message: Image) -> None:
        try:
            depth = _depth_to_metres(message)
        except (ValueError, IndexError) as error:
            self.get_logger().warn(f"{camera}: unusable depth ({error})")
            return
        with self._lock:
            self._pending[camera]["depth"] = depth
            self._pending[camera]["depth_wall_ms"] = int(time.time()*1000)
            self._pending[camera]["depth_received_ns"] = time.monotonic_ns()
            self._pending[camera]["depth_stamp"] = message.header.stamp.sec * 1_000_000_000 + message.header.stamp.nanosec

    def _on_info(self, camera: str, message: CameraInfo) -> None:
        """The field of view, read back out of the intrinsics Gazebo publishes.

        The SDF declares an FOV and Gazebo turns it into `k`; this inverts that
        the same way, rather than hard-coding the number on both sides where the
        two could disagree.
        """
        focal = float(message.k[0]) if len(message.k) >= 1 else 0.0
        if focal <= 0 or message.width <= 1:
            return
        with self._lock:
            self._pending[camera]["fov"] = 2.0 * math.atan((message.width / 2.0) / focal)
            self._pending[camera]["intrinsics"] = np.asarray(message.k,dtype=float).reshape(3,3).copy()
            self._pending[camera]["info_size"] = (int(message.width),int(message.height))

    def _assemble_pending(self) -> None:
        for camera in CAMERA_TOPICS:
            self._try_assemble(camera)

    def _try_assemble(self, camera: str) -> None:
        """Snapshot briefly, validate off-lock, then commit a newer exact pair."""
        started = time.monotonic_ns()
        with self._lock:
            locked_at = time.monotonic_ns()
            pending = self._pending[camera]
            if (pending.get("rgb_stamp") != pending.get("depth_stamp")
                    or pending.get("rgb_stamp", 0) <= pending.get("last_stamp", -1)
                    or pending.get("rgb") is None or pending.get("depth") is None):
                return
            # Callbacks replace arrays/history entries; they never mutate them.
            # Keep bounded references to this exact pair and its interpolation
            # basis, while newer callback data is free to arrive.
            parts = dict(pending)
            joints = list(self._joint_history)
            odometry = list(self._odom_history)
            clocks = list(self._sensor_clock_history)
            suction = dict(self._suction_state or {})
            snapshot_lock_ms = (time.monotonic_ns()-locked_at)/1e6
        validation_started = time.monotonic_ns()
        validated = None
        try:
            sample = self._assemble_snapshot(camera, parts, joints, odometry, clocks, suction)
            if sample is not None:
                validated = self.runtime.validate_sample(camera, sample)
        except (GazeboRuntimeError, GazeboBridgeError, ValueError) as error:
            # Refuse malformed captures without killing the acquisition timer.
            self.get_logger().warn(f"{camera}: capture refused ({getattr(error, 'code', 'INVALID')}: {error})")
        validation_ms = (time.monotonic_ns()-validation_started)/1e6
        with self._lock:
            commit_started = time.monotonic_ns()
            current = self._pending[camera]
            if validated is not None and validated.sensor_stamp_ns > current.get("last_stamp", -1):
                # Do not discard an admissible older job merely because the next
                # pair arrived during validation. Preserve that newer pending job.
                self.runtime._samples[camera] = validated
                current["last_stamp"] = validated.sensor_stamp_ns
                for channel in ("rgb", "depth"):
                    if current.get(channel+"_stamp") == validated.sensor_stamp_ns:
                        current.pop(channel, None)
            timings = getattr(self, "_assembly_timing", None)
            if timings is None:
                self._assembly_timing = timings = {}
            previous = timings.get(camera, {})
            timings[camera] = {
                "executionScope": "timer_snapshot_then_offlock_validation_then_commit",
                "lastCallMonotonicNs": str(started),
                "lastSnapshotLockDurationMs": snapshot_lock_ms,
                "maxSnapshotLockDurationMs": max(snapshot_lock_ms, previous.get("maxSnapshotLockDurationMs", 0)),
                "lastValidationDurationMs": validation_ms,
                "maxValidationDurationMs": max(validation_ms, previous.get("maxValidationDurationMs", 0)),
                "lastCommitLockDurationMs": (time.monotonic_ns()-commit_started)/1e6,
            }

    def _assemble_snapshot(self, camera, parts, joint_history, odom_history, clock_history, suction):
        """Build from an immutable exact-pair basis, without holding _lock."""
        rgb, depth = parts.get("rgb"), parts.get("depth")
        if parts.get("rgb_stamp") != parts.get("depth_stamp"):
            return
        stamp_ns = parts.get("rgb_stamp", 0)
        if stamp_ns <= parts.get("last_stamp", -1):
            return
        if rgb is None or depth is None or rgb.shape[:2] != depth.shape[:2]:
            return
        if rgb.shape[2] < 1:
            return
        fov = parts.get("fov")
        if not fov:
            # Without an intrinsics message there is no field of view, and a
            # capture attached to a guessed focal length is a map at the wrong
            # scale - worse than a missing capture, because it looks fine.
            return
        from tangying_robot_gateway.gazebo_bridge import (
            interpolate_timed_clock,
            interpolate_timed_joints,
            interpolate_timed_pose,
        )
        joints = interpolate_timed_joints(joint_history, stamp_ns)
        base_pose = interpolate_timed_pose(odom_history, stamp_ns)
        clock = interpolate_timed_clock(clock_history, stamp_ns)
        if joints is None or base_pose is None or clock is None or parts.get("info_size") != (rgb.shape[1],rgb.shape[0]):
            return  # Wait for bracketed odometry and matching calibrated image size.
        transform = np.asarray(base_pose, dtype=float) @ camera_mount(camera)
        return CameraSample(
            width=int(rgb.shape[1]), height=int(rgb.shape[0]),
            rgb=np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8),
            depth_metres=np.ascontiguousarray(depth, dtype=np.float64),
            horizontal_fov_rad=float(fov),
            world_from_camera_link=transform,
            captured_at_unix_ms=clock[0],
            capture_monotonic_ns=clock[1],
            capture_clock_source='sensor_clock_wall_bridge',
            base_pose_at_capture=base_pose,
            camera_intrinsics=parts["intrinsics"],
            sensor_stamp_ns=stamp_ns,
            joint_positions_at_capture=joints,
            joint_stamp_ns=stamp_ns,
            tool_side_at_capture=suction.get("side", ""),
            tool_attached_at_capture=bool(suction.get("attached", False)),
            odometry_stamp_ns=stamp_ns,
            received_monotonic_ns=min(parts["rgb_received_ns"],parts["depth_received_ns"]),
            pose_fusion_source="imu_roll_pitch_odom_yaw",
        )


class RuntimeServicer(robot_pb2_grpc.RobotRuntimeServicer):
    """Serves what is implemented and refuses the rest by name."""

    def __init__(self, node: GazeboRuntimeNode, services=None) -> None:
        self._node = node
        #: The mapping service catalogue, when this process was configured to host
        #: one. ``None`` is not the same as "empty": it means this runtime was
        #: started observation-only, and the refusals below say exactly that.
        self._services = services
        self._skills = None
        # Execution and stopping exist independently of the optional verifier.
        from tangying_robot_gateway.gazebo_backend import GazeboSkillBackend
        from tangying_robot_gateway.journal import RuntimeJournal
        from tangying_robot_gateway.service import RobotRuntimeService
        root = gazebo_runtime_root()
        backend = GazeboSkillBackend(node)
        self._skills = RobotRuntimeService(
            backend,
            journal=RuntimeJournal(os.path.join(root, "commands.json")),
            max_lease_ms=600_000 if backend.home else 60_000,
        )
        if backend.home and getattr(node,"workflow",None) is not None:
            from tangying_robot_gateway.runtime import ObservationRequest
            node.workflow.entity_source = lambda: backend.observe(ObservationRequest(streams=("reconstruction","robot_state")))
        node.motion_allowed = lambda: not self._skills.safety.estop_latched

    def GetRuntimeInfo(self, request, context):
        if self._skills is not None:
            result = self._skills.GetRuntimeInfo(request, context)
            # Keep the original camera/reconstruction declaration for existing readers.
            original = self._node.runtime.runtime_info(skills=[])
            result.cameras.extend(original["cameras"])
            fresh = "RGBD_NOT_READY" not in self._node.readiness_blockers()
            if not fresh:
                result.manipulation_ready = False
                if "RGBD_NOT_READY" not in result.blockers:
                    result.blockers.append("RGBD_NOT_READY")
            return result
        runtime = self._node.runtime
        info = runtime.runtime_info(skills=[])
        return robot_pb2.RuntimeInfo(
            robot_id=info["robot_id"], adapter=info["adapter"], skills=info["skills"],
            cameras=info["cameras"], manipulation_ready=info["manipulation_ready"],
            blockers=info["blockers"], software_version=info["software_version"],
            protocol_version=info["protocol_version"], runtime_version=info["runtime_version"],
            catalog_revision=info["catalog_revision"])

    def Observe(self, request, context):
        # One-frame clients cancel immediately after Recv. Do not retain a gRPC
        # worker for the entire rate interval and starve Info/Cancel/EStop RPCs.
        cancelled = threading.Event()
        context.add_callback(cancelled.set)
        if self._skills is not None and "rgbd_raw" not in request.streams:
            while context.is_active():
                if not self._node.runtime._samples.get("base-rgbd"):
                    cancelled.wait(0.1)
                    continue
                for observation in self._skills.Observe(request, context):
                    observation.semantic_state.mode = "SIMULATION"
                    yield observation
                cancelled.wait(1.0 / max(1, min(30, request.max_rate_hz or 5)))
            return
        runtime = self._node.runtime
        # `cameras` is a mapping of runtime name to source, so it has to be
        # iterated rather than indexed. Asking it for [0] raised a KeyError that
        # gRPC reported as an opaque UNKNOWN with no message - which is how a
        # one-line mistake becomes an afternoon.
        camera = min(runtime.cameras)
        if request.source_id:
            matches = [name for name in runtime.cameras
                       if request.source_id == f"{runtime.robot_id}/{name}"]
            if not matches:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "UNKNOWN_CAMERA_SOURCE")
            camera = matches[0]
        counter = 0
        while context.is_active():
            counter += 1
            try:
                payload = runtime.observation(camera, observation_id=f"gz-{counter}",
                                              streams=list(request.streams),
                                              include_raw="rgbd_raw" in request.streams)
            except GazeboRuntimeError as error:
                if error.code == "NO_CAPTURE":
                    # A client that connects before the cameras have published is
                    # early, not wrong. Waiting is the honest answer; aborting
                    # would tell it the runtime is broken.
                    if not context.is_active():
                        return
                    cancelled.wait(0.2)
                    counter -= 1
                    continue
                context.abort(grpc.StatusCode.UNAVAILABLE, f"{error.code}: {error.message}")
                return
            # The payload -> wire conversion lives in the gateway, next to the
            # payload it converts. Building it here as well is how the base pose
            # went missing from every observation for two rounds: the node made a
            # well-formed message without it, and nothing downstream could tell.
            yield observation_message(payload)
            cancelled.wait(1.0 / max(1, min(30, request.max_rate_hz or 5)))

    # Skills are still refused, and refused by name. A runtime that took a skill
    # command and did nothing would be indistinguishable from a slow robot, which
    # is the worst answer a robot can give. The *mapping services* are a different
    # matter: they are answered by the gateway's own RobotWorkflow, which owns the
    # survey loop, so serving them here is not a second implementation - it is the
    # same one, wired to this backend's capture and motion.
    def ListServices(self, request, context):
        if self._services is None:
            context.abort(grpc.StatusCode.UNIMPLEMENTED,
                          "this Gazebo runtime was started observation-only, so it hosts no "
                          "service catalogue; set the navigation URL and token to host one")
            return robot_pb2.ServiceCatalog()
        result = self._services.catalogue()
        if self._skills.grounded is not None:
            from tangying_robot_gateway.gazebo_backend import grounded_service_requires_contract
            for service in result.services:
                if grounded_service_requires_contract(service.name, service.mutates_world):
                    service.available = False
                    service.description += "（GVF：尚无服务动作合约，禁止派发）"
        return result

    def CallService(self, request, context):
        if self._services is None:
            context.abort(grpc.StatusCode.UNIMPLEMENTED,
                          "this Gazebo runtime was started observation-only, so it hosts no "
                          "service catalogue; set the navigation URL and token to host one")
            return robot_pb2.ServiceResponse(ok=False, code="UNIMPLEMENTED")
        if self._skills.grounded is not None:
            from tangying_robot_gateway.gazebo_backend import grounded_service_requires_contract
            service = self._services.services.get(request.name)
            if service is not None and grounded_service_requires_contract(request.name, service.mutates_world):
                return robot_pb2.ServiceResponse(ok=False, code="GVF_CONTRACT_REQUIRED",
                    message="此变更服务尚无物理接地合约；不能绕过技能验证与未知结果屏障。")
        service = self._services.services.get(request.name)
        stopping = request.name in {"mapping.cancel", "mapping.stop_motion", "mapping.finish"}
        if service is not None and service.mutates_world and not stopping:
            if self._skills.safety.estop_latched:
                return robot_pb2.ServiceResponse(ok=False, code="EMERGENCY_STOP_LATCHED")
            if self._skills.safety.active_command_id:
                return robot_pb2.ServiceResponse(ok=False, code="ROBOT_BUSY")
        if stopping or service is None or not service.mutates_world:
            return self._services.call(request)
        if not self._node.ownership_lock.acquire(blocking=False):
            return robot_pb2.ServiceResponse(ok=False, code="ROBOT_BUSY")
        try:
            if self._skills.safety.estop_latched:
                return robot_pb2.ServiceResponse(ok=False, code="EMERGENCY_STOP_LATCHED")
            return self._services.call(request)
        finally:
            self._node.ownership_lock.release()

    def ExecuteSkill(self, request, context):
        if request.skill == "emergency_stop":
            yield from self._skills.ExecuteSkill(request, context)
            return
        if not self._node.ownership_lock.acquire(blocking=False):
            yield robot_pb2.SkillEvent(command_id=request.command_id, sequence=1,
                                       type=robot_pb2.SKILL_EVENT_FAILED, code="ROBOT_BUSY")
            return
        try:
            if self._node.bindings is not None and self._node.bindings._reservation is not None:
                yield robot_pb2.SkillEvent(command_id=request.command_id, sequence=1,
                                           type=robot_pb2.SKILL_EVENT_FAILED, code="ROBOT_BUSY")
                return
            # Reset the previous command's cooperative token before admission;
            # execute() must never clear a cancellation arriving during dispatch.
            self._skills.backend.cancel_event.clear()
            yield from self._skills.ExecuteSkill(request, context)
        finally:
            self._node.ownership_lock.release()

    def Cancel(self, request, context):
        return self._skills.Cancel(request, context)

    def EmergencyStop(self, request, context):
        return self._skills.EmergencyStop(request, context)


def register_runtime_diagnostics(registry, node):
    from tangying_robot_gateway.service_registry import RegisteredService, object_schema
    registry.register(RegisteredService(
        "runtime.readiness", "只读采集与反馈新鲜度诊断；不授权运动", object_schema(),
        lambda _: node.runtime_diagnostics(), False,
        {"version": "1", "effects": ["READ"], "resources": [],
         "outputSchema": {"type": "object", "additionalProperties": True},
         "planningFields": ["ready", "blockers", "robotId", "clockSource"]},
    ))


def host_mapping_services(node: GazeboRuntimeNode):
    """Build the gateway's mapping catalogue against this runtime, or nothing.

    Hosting is opt-in through the environment because observation-only is a
    legitimate way to run this node, and it is the safe default: a runtime that
    silently grew a service catalogue would let a client start a survey against a
    stack nobody configured for one.

    Everything that decides *where the robot goes* comes from the gateway, unchanged.
    What this function supplies is only the two things Gazebo knows and the gateway
    cannot: how to see (the runtime's own captures) and how to move (the navigation
    sidecar's goal API).
    """
    base_url = os.environ.get("TANGYING_NAVIGATION_URL", "").strip()
    token = os.environ.get("TANGYING_NAVIGATION_TOKEN", "").strip()
    if not base_url:
        node.get_logger().info(
            "no TANGYING_NAVIGATION_URL: starting observation-only, mapping services refused")
        return None
    if not token:
        # Fail loudly rather than half-start: a catalogue that exists but cannot
        # reach a robot is worse than no catalogue, because a client will plan
        # against it.
        raise SystemExit(
            "TANGYING_NAVIGATION_URL is set but TANGYING_NAVIGATION_TOKEN is not; "
            "the navigation sidecar authenticates every request")

    from tangying_robot_gateway.gazebo_workflow import (
        GazeboNavigationClient,
        GazeboWorkflowBindings,
    )
    from tangying_robot_gateway.service_registry import ServiceRegistry

    navigation = GazeboNavigationClient(base_url, token)
    node.navigation = navigation
    # The survey's bounded steps go through the guarded `/cmd_vel` driver, not
    # nav2's global planner; commissioned goals still go through the sidecar. The
    # split and its reasoning are in `GazeboWorkflowBindings.move`.
    node.enable_bounded_motion()
    bindings = GazeboWorkflowBindings(
        runtime=node.runtime, navigation=navigation, robot_id=node.runtime.robot_id,
        root=os.environ.get("TANGYING_MAP_ROOT", "/data/maps/gazebo_house"),
        bounded_driver=node.bounded_step,
        # A map is only valid in the world it was surveyed in, so the world's own
        # revision is part of its identity rather than a fact kept beside it.
        world_revision=os.environ.get("TANGYING_GAZEBO_WORLD_REVISION", ""))
    node.runtime.calibration_revision = bindings.calibration["revision"]
    node.bindings = bindings
    workflow = bindings.build_workflow()
    node.workflow = workflow
    registry = ServiceRegistry(node.runtime.robot_id)
    workflow.register(registry)
    register_runtime_diagnostics(registry, node)
    node.get_logger().info(
        f"mapping services hosted ({len(registry.services)}): {sorted(registry.services)}")
    return registry


def main() -> int:
    rclpy.init()
    node = GazeboRuntimeNode()
    port = int(os.environ.get("TANGYING_RUNTIME_PORT", "50051"))
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    # CameraInfo may change derived intrinsics by the renderer's precision.
    # Enroll only after both calibrated captures exist; a profile is immutable
    # throughout the service lifetime and must never start with guessed lenses.
    deadline = time.monotonic()+120.
    while rclpy.ok() and time.monotonic() < deadline:
        executor.spin_once(timeout_sec=.1)
        with node._lock:
            calibrated = all(name in node.runtime._samples for name in node.runtime.cameras)
        if calibrated:
            break
    else:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
        raise RuntimeError("CALIBRATED_RGBD_STARTUP_TIMEOUT")
    services = host_mapping_services(node)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    robot_pb2_grpc.add_RobotRuntimeServicer_to_server(RuntimeServicer(node, services), server)
    server.add_insecure_port(f"0.0.0.0:{port}")
    server.start()
    node.get_logger().info(f"runtime listening on :{port}")
    try:
        # Sensor image/cloud processing must not starve joint, odometry and
        # suction feedback while a physical command is awaiting fresh samples.
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        server.stop(0)
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
