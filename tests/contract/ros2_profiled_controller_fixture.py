"""Recorded ROS2 controller for protocol integration; never connects hardware.

Run only in a test ROS_DOMAIN_ID/namespace. It updates an in-memory pose and
publishes evidence, allowing the real action/lease/stop transport to be tested.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from tangying_robot_gateway.contracts import RobotProfile, validate_tool_parameters
from tangying_robot_gateway.ros2_profile import profile_revision
from tangying_robot_gateway.spatial_localization import SpatialNavigationDeployment
from tangying_robot_msgs.action import ProfiledExecute
from tangying_robot_msgs.msg import CommandLease, ProfiledDriverState, ProfiledObservation
from tangying_robot_msgs.srv import EnterSafeStop


class RecordedController(Node):
    def __init__(self, profile, commissioning_id, *, namespace="", journal_path=None, evidence_burst=1,
                 spatial_deployment=None):
        super().__init__("recorded_profiled_controller", namespace=namespace)
        self.profile = RobotProfile.model_validate(profile)
        self.revision, self.commissioning_id = profile_revision(self.profile), commissioning_id
        self.journal_path = journal_path
        if not 1 <= evidence_burst <= 16:
            raise ValueError("evidence burst must be 1..16 captures")
        self._evidence_burst = evidence_burst
        self._lock = threading.RLock()
        self._state_seq = self._obs_seq = 0
        self._stopped = False
        self._pose = [0., 0., 0., 1., 0., 0., 0.]
        self.spatial = SpatialNavigationDeployment.from_file(spatial_deployment, self.profile) if spatial_deployment else None
        if self.spatial:
            # Distinguish the observed start from the named living_room goal;
            # otherwise return-to-start can validly coalesce with the last visit.
            self._pose[0] = -.5
            self._pose[2] = .5 if self.profile.mobility.domain == "air" else 0.
        self._leases = {}
        self._executed = {}
        group = ReentrantCallbackGroup()
        self._state = self.create_publisher(ProfiledDriverState, "profiled_driver_state", 1)
        self._obs = self.create_publisher(ProfiledObservation, "profiled_observation", 64)
        self.create_subscription(CommandLease, "profiled_command_lease", self._lease, 10, callback_group=group)
        self.create_service(EnterSafeStop, "enter_safe_stop", self._stop, callback_group=group)
        self._server = ActionServer(self, ProfiledExecute, "profiled_execute", self._execute,
            goal_callback=self._goal, cancel_callback=lambda goal: CancelResponse.ACCEPT, callback_group=group)
        self.create_timer(.05, self._publish, callback_group=group)

    def record(self, kind, **data):
        wire = json.dumps({"type": kind, "atUnixMs": int(time.time() * 1000), **data}, sort_keys=True)
        print(wire, flush=True)
        if self.journal_path:
            with self._lock, Path(self.journal_path).open("a") as stream:
                stream.write(wire + "\n")

    def _goal(self, goal):
        try:
            if (self._stopped or goal.schema_version != "ros2.execution.v1"
                    or goal.robot_id != self.profile.robot_id or goal.adapter_id != self.profile.adapter_id
                    or goal.profile_revision != self.revision or goal.commissioning_id != self.commissioning_id
                    or not goal.command_id or not goal.idempotency_key or not goal.catalog_revision
                    or goal.deadline_unix_ms <= int(time.time() * 1000) or goal.lease_ms <= 0
                    or goal.capability not in {"navigation.navigate", "verify_arrival"}
                    or (goal.capability == "navigation.navigate" and not goal.approval_id)):
                return GoalResponse.REJECT
            validate_tool_parameters(goal.capability, json.loads(goal.parameters_json), self.profile,
                                     target_ref=goal.target_ref)
            if goal.capability == "navigation.navigate" and self.spatial:
                self.spatial.verify_plan(json.loads(goal.navigation_plan_json), self._pose,
                    json.loads(goal.parameters_json)["goalPose"], goal.command_id)
            elif goal.navigation_plan_json:
                return GoalResponse.REJECT
            return GoalResponse.ACCEPT
        except (ValueError, KeyError):
            return GoalResponse.REJECT

    def _lease(self, message):
        if message.robot_id != self.profile.robot_id or message.profile_revision != self.revision:
            return
        if not 0 <= int(time.time() * 1000) - message.observed_at_unix_ms <= message.lease_ms:
            return
        with self._lock:
            self._leases[message.command_id] = (time.monotonic(), message.deadline_unix_ms, message.lease_ms)
        self.record("LEASE", commandId=message.command_id)

    def _capture(self):
        with self._lock:
            self._obs_seq += 1
            sensor = self.profile.sensors[0]
            observed = {"schemaVersion": "scene.reconstruction.v1", "robotId": self.profile.robot_id,
                "observationId": f"recorded-ros-{self._obs_seq}", "sourceId": sensor.source_id,
                "sourceType": sensor.source_type, "sourceFrameId": sensor.frame_id,
                "frameId": "world", "transformRevision": sensor.transform_revision,
                "observedAtUnixMs": int(time.time() * 1000), "sequence": self._obs_seq, "units": "m",
                "entities": [{"entityId": "robot-base", "category": "robot", "attributes": {"fixture": "true"},
                              "pose": list(self._pose), "confidence": 1.0}], "points": []}
            message = ProfiledObservation(robot_id=self.profile.robot_id, profile_revision=self.revision,
                                          reconstruction_json=json.dumps(observed),
                                          robot_state_json=json.dumps(self._navigation_state(observed)))
            self._obs.publish(message)
            return observed["observationId"]

    def _navigation_state(self, observed):
        active = {"mapId": "recorded-ros2-test-map", "mapRevision": "a" * 64,
                  "calibrationRevision": self.profile.sensors[0].transform_revision}
        state = {"base_pose": list(self._pose), "base_pose_frame": "world", "active_map": active,
                "semantic_navigation": {"schemaVersion": "semantic.navigation.v1", "frameId": "world",
                    "robotId": self.profile.robot_id, **active,
                    "goals": {"living_room": [0., 0., 0., 1., 0., 0., 0.],
                              "kitchen": [1., 0., 0., 1., 0., 0., 0.],
                              "bedroom": [1., 1., 0., 1., 0., 0., 0.]},
                    "aliases": {"客厅": "living_room", "厨房": "kitchen", "卧室": "bedroom"},
                    "routeEdges": {"living_room": ["kitchen"], "kitchen": ["living_room", "bedroom"],
                                   "bedroom": ["kitchen"]}}}
        if self.spatial:
            volume, source = self.spatial.volume, self.spatial.localization_config
            active.update({key: volume[key] for key in active})
            state["semantic_navigation"].update(active)
            heights = [.5, .5, 1.5] if self.profile.mobility.domain == "air" else [0., 0., 0.]
            for pose, height in zip(state["semantic_navigation"]["goals"].values(), heights, strict=True):
                pose[2] = height
            state["spatial_localization"] = {"schemaVersion": "spatial.localization.v1",
                "robotId": self.profile.robot_id, "sourceId": source["sourceId"], "sourceType": source["sourceType"],
                "sourceFrameId": source["sourceFrameId"], "frameId": "world",
                "mapId": volume["mapId"], "mapRevision": volume["mapRevision"],
                "worldFrameRevision": volume["worldFrameRevision"],
                "transformRevision": volume["calibrationRevision"], "observationId": observed["observationId"],
                "observedAtUnixMs": observed["observedAtUnixMs"], "sequence": observed["sequence"],
                "poseModel": self.profile.mobility.pose_model, "pose": list(self._pose),
                "linearUnit": "m", "angularUnit": "rad", "quality": {"state": "localized",
                    "positionVarianceM2": [.001] * 3, "orientationVarianceRad2": [.001] * 3}}
        return state

    def _publish(self):
        with self._lock:
            self._state_seq += 1
            mobility = self.profile.mobility
            message = ProfiledDriverState(robot_id=self.profile.robot_id, profile_revision=self.revision,
                commissioning_id=self.commissioning_id, sequence=self._state_seq,
                observed_at_unix_ms=int(time.time() * 1000), commissioned=True, armed=not self._stopped,
                healthy=True, watchdog_active=True, stop_procedure_id=mobility.safe_stop.procedure_id,
                command_loss_timeout_ms=mobility.safe_stop.command_loss_timeout_ms,
                max_linear_speed_mps=mobility.max_linear_speed_mps,
                max_angular_speed_radps=mobility.max_angular_speed_radps,
                supported_tools=[t for t in self.profile.tools if t not in {"observe_scene", "emergency_stop"}], faults=[])
            message.spatial_navigation_revision = self.spatial.revision if self.spatial else ""
            self._state.publish(message)
            self._capture()

    def _execute(self, handle):
        goal = handle.request
        result = ProfiledExecute.Result(robot_id=self.profile.robot_id, profile_revision=self.revision,
                                        command_id=goal.command_id, success=False, code="CANCELLED",
                                        verification_confidence=0.)
        self.record("GOAL", commandId=goal.command_id, capability=goal.capability,
                    catalogRevision=goal.catalog_revision, profileRevision=goal.profile_revision,
                    approvalId=goal.approval_id, fencingToken=goal.fencing_token)
        # Wait for the first command-bound lease instead of treating discovery or
        # an unbound heartbeat as permission to run.
        deadline = time.monotonic() + min(.4, goal.lease_ms / 1000)
        while time.monotonic() < deadline:
            with self._lock:
                lease = self._leases.get(goal.command_id)
            if lease is not None:
                break
            if handle.is_cancel_requested or self._stopped:
                handle.canceled()
                return result
            time.sleep(.005)
        with self._lock:
            lease = self._leases.get(goal.command_id)
            if (lease is None or lease[1] != goal.deadline_unix_ms or lease[2] != goal.lease_ms
                    or (time.monotonic() - lease[0]) * 1000 > min(goal.lease_ms, self.profile.mobility.safe_stop.command_loss_timeout_ms)
                    or goal.deadline_unix_ms <= int(time.time() * 1000) or self._stopped):
                handle.abort()
                result.code = "FIXTURE_LEASE_EXPIRED"
                return result
            parameters = json.loads(goal.parameters_json)
            desired = parameters.get("goalPose", parameters.get("goal_pose"))
            if goal.capability == "navigation.navigate":
                if goal.idempotency_key in self._executed:
                    handle.abort()
                    result.code = "FIXTURE_DUPLICATE_MOTION"
                    return result
                if self.spatial:
                    plan = json.loads(goal.navigation_plan_json)
                    self.spatial.verify_plan(plan, self._pose, desired, goal.command_id)
                    for waypoint in plan["waypoints"]:
                        self._pose = list(waypoint)
                        self.record("RECORDED_WAYPOINT", commandId=goal.command_id,
                                    planId=plan["planId"], pose=list(waypoint))
                    result.navigation_plan_id = plan["planId"]
                    self.record("SPATIAL_PLAN", commandId=goal.command_id, plan=plan)
                else:
                    self._pose = list(desired)
                self._executed[goal.idempotency_key] = list(desired)
                self.record("RECORDED_MOVE", commandId=goal.command_id, goalPose=desired)
            result.success = desired == self._pose
            result.code = "RECORDED_ARRIVAL" if result.success else "RECORDED_NOT_AT_GOAL"
            result.observation_id = self._capture()
            # Model a fast sensor burst after the exact terminal evidence frame.
            # Consumers must retain that ID, not substitute the newest sample.
            for _ in range(self._evidence_burst - 1):
                self._capture()
            result.verification_confidence = 1. if result.success else 0.
        handle.succeed()
        return result

    def _stop(self, request, response):
        response.robot_id, response.profile_revision = self.profile.robot_id, self.revision
        response.procedure_id = self.profile.mobility.safe_stop.procedure_id
        response.accepted = (request.robot_id == self.profile.robot_id and request.profile_revision == self.revision
            and request.commissioning_id == self.commissioning_id and request.procedure_id == response.procedure_id)
        if response.accepted:
            self._stopped = True
            self.record("SAFE_STOP", procedureId=request.procedure_id, reason=request.reason)
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--namespace", default="")
    parser.add_argument("--commissioning-id", default="fixture-commission-v1")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--evidence-burst", type=int, default=1)
    parser.add_argument("--spatial-deployment", type=Path)
    args = parser.parse_args()
    rclpy.init(args=[])
    node = RecordedController(json.loads(args.profile.read_text()), args.commissioning_id,
                              namespace=args.namespace, journal_path=args.journal, evidence_burst=args.evidence_burst,
                              spatial_deployment=args.spatial_deployment)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
