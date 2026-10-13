"""ROS2 typed action/state bridge behind the common durable PluginBackend runtime."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from pydantic import Field
from tangying_robot_gateway.contracts import Contract, Identifier, RobotProfile
from tangying_robot_gateway.ros2_profile import Ros2ProfileBridge


class ProfiledRosConfig(Contract):
    profile_path: str = Field(min_length=1, max_length=4096)
    commissioning_id: Identifier
    namespace: str = Field(default="", max_length=256)
    node_name: Identifier = "tangying_profiled_gateway"
    action_name: Identifier = "profiled_execute"
    state_topic: Identifier = "profiled_driver_state"
    observation_topic: Identifier = "profiled_observation"
    lease_topic: Identifier = "profiled_command_lease"
    stop_service: Identifier = "enter_safe_stop"
    state_max_age_ms: int = Field(default=500, ge=1, le=5000)
    stop_timeout_ms: int = Field(default=1000, ge=1, le=5000)


class Ros2Transport:
    def __init__(self, profile, config):
        import rclpy
        from rclpy.action import ActionClient
        from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
        from rclpy.context import Context
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.node import Node
        from tangying_robot_msgs.action import ProfiledExecute
        from tangying_robot_msgs.msg import CommandLease, ProfiledDriverState, ProfiledObservation
        from tangying_robot_msgs.srv import EnterSafeStop

        self.config, self._closed, self._lock = config, threading.Event(), threading.Lock()
        self._context = self._node = self._executor = self._thread = None
        self._goal = None
        self._action_type, self._stop_type, self._lease_type = ProfiledExecute, EnterSafeStop, CommandLease
        try:
            self._context = Context()
            rclpy.init(args=[], context=self._context)
            self._node = Node(config.node_name, namespace=config.namespace, context=self._context)
            if self._node.get_parameter("use_sim_time").value is not False:
                raise ValueError("profiled hardware gateway requires Unix time, not use_sim_time")
            group = ReentrantCallbackGroup()
            self._action = ActionClient(self._node, ProfiledExecute, config.action_name, callback_group=group)
            self._stop = self._node.create_client(EnterSafeStop, config.stop_service, callback_group=group)
            self._lease = self._node.create_publisher(CommandLease, config.lease_topic, 1)
            self.bridge = Ros2ProfileBridge(profile, self, commissioning_id=config.commissioning_id,
                                           state_max_age_ms=config.state_max_age_ms)
            # Preserve source sequence order within each stream. A reentrant
            # subscription can process N+1 before N in the multi-thread executor.
            self._state_group = MutuallyExclusiveCallbackGroup()
            self._observation_group = MutuallyExclusiveCallbackGroup()
            self._subscriptions = [
                self._node.create_subscription(ProfiledDriverState, config.state_topic, self._state, 1,
                                               callback_group=self._state_group),
                # Action result IDs can refer to a frame immediately followed
                # by periodic telemetry; KEEP_LAST(1) would discard that proof.
                self._node.create_subscription(ProfiledObservation, config.observation_topic, self._observation, 64,
                                               callback_group=self._observation_group),
            ]
            self._executor = MultiThreadedExecutor(num_threads=4, context=self._context)
            self._executor.add_node(self._node)
            self._thread = threading.Thread(target=self._spin, name="profiled-ros2", daemon=True)
            self._thread.start()
        except Exception:
            self.close()
            raise

    def _spin(self):
        try:
            self._executor.spin()
        except Exception as exc:  # noqa: BLE001 - executor loss invalidates all readiness
            self._closed.set()
            self._node.get_logger().error(f"profiled ROS2 executor failed: {exc}")

    def _state(self, message):
        try:
            self.bridge.receive_state({name: getattr(message, name) for name in message.get_fields_and_field_types()})
        except Exception as exc:  # noqa: BLE001 - malformed driver data stays unavailable
            self._node.get_logger().warning(f"driver state rejected: {exc}")

    def _observation(self, message):
        try:
            self.bridge.receive_observation(message.robot_id, message.profile_revision, message.reconstruction_json,
                                            message.robot_state_json)
        except Exception as exc:  # noqa: BLE001 - malformed perception stays unavailable
            self._node.get_logger().warning(f"reconstruction rejected: {exc}")

    def ready(self):
        return not self._closed.is_set() and self._action.server_is_ready() and self._stop.service_is_ready()

    def _wait(self, future, deadline, *, tick=None):
        while not future.done():
            if self._closed.is_set():
                raise RuntimeError("ROS2 transport closed")
            if time.monotonic() >= deadline:
                raise TimeoutError("ROS2 response timed out")
            if tick is not None:
                tick()
            self._closed.wait(.01)
        return future.result()

    def execute(self, values, *, tick):
        from action_msgs.msg import GoalStatus

        goal = self._action_type.Goal()
        for name, value in values.items():
            setattr(goal, name, value)
        deadline = time.monotonic() + max(0, values["deadline_unix_ms"] - int(time.time() * 1000)) / 1000
        # Local DDS discovery never grants admission. The bridge already checked
        # exact state/profile readiness, which is rechecked through every wait.
        pending = self._action.send_goal_async(goal)
        try:
            handle = self._wait(pending, deadline, tick=tick)
        except Exception:
            # Goal acceptance can race timeout/cancellation. Cancel a late ACK;
            # the controller must also enforce the original deadline/watchdog.
            def cancel_late(future):
                try:
                    accepted = future.result()
                    if accepted is not None and accepted.accepted:
                        accepted.cancel_goal_async()
                except Exception as exc:  # noqa: BLE001 - original command already failed closed
                    self._node.get_logger().error(f"late ROS2 goal cancellation failed: {exc}")
            pending.add_done_callback(cancel_late)
            raise
        if not handle.accepted:
            raise RuntimeError("ROS2 controller rejected command")
        with self._lock:
            self._goal = handle
        next_heartbeat = 0.0

        def heartbeat():
            nonlocal next_heartbeat
            tick()
            now = time.monotonic()
            if now >= next_heartbeat:
                lease = self._lease_type()
                for key in ("robot_id", "profile_revision", "command_id", "deadline_unix_ms", "lease_ms"):
                    setattr(lease, key, values[key])
                lease.observed_at_unix_ms = int(time.time() * 1000)
                self._lease.publish(lease)
                next_heartbeat = now + min(.1, values["lease_ms"] / 3000,
                                           self.bridge.profile.mobility.safe_stop.command_loss_timeout_ms / 3000)
        try:
            heartbeat()
            response = self._wait(handle.get_result_async(), deadline, tick=heartbeat)
            if response.status != GoalStatus.STATUS_SUCCEEDED and response.result.success:
                raise ValueError("ROS2 result claims success for a non-successful action status")
            return {key: getattr(response.result, key) for key in response.result.get_fields_and_field_types()}
        finally:
            handle.cancel_goal_async()
            with self._lock:
                self._goal = None

    def stop(self, values):
        with self._lock:
            if self._goal is not None:
                self._goal.cancel_goal_async()
        if not self._stop.service_is_ready():
            raise RuntimeError("ROS2 safe-stop service unavailable; controller watchdog must handle loss")
        request = self._stop_type.Request()
        for name, value in values.items():
            setattr(request, name, value)
        response = self._wait(self._stop.call_async(request), time.monotonic() + self.config.stop_timeout_ms / 1000)
        return {key: getattr(response, key) for key in response.get_fields_and_field_types()}

    def close(self):
        # Idempotent even when executor failure already invalidated readiness.
        with self._lock:
            if getattr(self, "_cleaned", False):
                return
            self._cleaned = True
        self._closed.set()
        try:
            if self._executor is not None:
                self._executor.shutdown(timeout_sec=2.)
        finally:
            if self._thread is not None:
                self._thread.join(timeout=2.)
            if self._node is not None:
                self._node.destroy_node()
            if self._context is not None and self._context.ok():
                self._context.shutdown()
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("ROS2 executor did not shut down")


def create_backend():
    path_text = os.environ.get("TANGYING_ROS_PROFILE_CONFIG", "")
    if not path_text:
        raise ValueError("TANGYING_ROS_PROFILE_CONFIG must identify a trusted local configuration")
    path = Path(path_text).expanduser().resolve()
    config = ProfiledRosConfig.model_validate(json.loads(path.read_text()))
    profile_path = Path(config.profile_path)
    if not profile_path.is_absolute():
        profile_path = path.parent / profile_path
    profile = RobotProfile.model_validate(json.loads(profile_path.read_text()))
    transport = Ros2Transport(profile, config)
    try:
        return transport.bridge.backend()
    except Exception:
        transport.close()
        raise


def main():
    from tangying_robot_gateway.run_plugin import main as plugin_main

    plugin_main()


if __name__ == "__main__":
    main()
