"""Camera-grounded motion with explicitly simulated suction and physics checks.

The common service still owns approval, leases, cancellation and durable receipts.
The plugin only attaches near the actual end link. This module plans bounded
joint targets; it never writes robot/object poses or accepts an ACK as proof.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
from scipy.optimize import least_squares

from .arm_kinematics import arm_links, chain_poses
from .gazebo_actuation import execute_chunk
from .gazebo_perception import DESTINATION_MODELS, OBJECT_MODELS
from .runtime import ObservationRequest, Result


def pose_matrix(pose):
    x, y, z, w, qx, qy, qz = pose
    from scipy.spatial.transform import Rotation
    matrix = np.eye(4)
    matrix[:3, :3] = Rotation.from_quat([qx, qy, qz, w]).as_matrix()
    matrix[:3, 3] = [x, y, z]
    return matrix


def solve_tip(target, joints, base, *, upright=None):
    links = arm_links("left")
    initial = np.array([joints[link.motor] for link in links])
    low = np.array([link.range_min+.001 for link in links])
    high = np.array([link.range_max-.001 for link in links])
    initial = np.clip(initial, low, high)

    def pose(values):
        return chain_poses(links, dict(zip([link.motor for link in links], values, strict=True)), base=base)[-1]

    def residual(values):
        tip = pose(values)
        errors = [*(tip[:3, 3]-target)]
        if upright is not None:
            errors.extend(.2*(tip[:3, :3] @ upright - [0., 0., 1.]))
        return np.r_[errors, .0001*(values-initial)]

    solution = least_squares(residual, initial, bounds=(low, high), max_nfev=180)
    tip = pose(solution.x)
    if np.linalg.norm(tip[:3, 3]-target) > .008:
        raise ValueError("GRASP_TARGET_UNREACHABLE")
    if upright is not None and np.linalg.norm(tip[:3, :3] @ upright-[0., 0., 1.]) > .12:
        raise ValueError("UPRIGHT_PLAN_UNREACHABLE")
    return {link.motor: float(value) for link, value in zip(links, solution.x, strict=True)}


class GazeboManipulation:
    def __init__(self, backend):
        self.backend = backend
        self.node = backend.node
        self.plan = None
        self.acquisition = None

    def capture(self):
        frame = self.backend.observe(ObservationRequest(source_id=self.node.runtime.robot_id+"/head-rgbd"))
        if not 0 <= int(time.time()*1000)-frame.wall_time_unix_ms <= 1000:
            raise ValueError("SENSOR_STALE")
        return frame, {entity.entity_id: entity for entity in frame.entities}

    def state(self):
        return self.node.suction_snapshot()

    def execute(self, command):
        try:
            return self._execute(command)
        except (ValueError, KeyError) as error:
            return Result(False, str(error).strip("'"), confidence=0.)

    def _execute(self, command):
        name, parameters = command.capability, command.parameters
        frame, entities = self.capture()
        if name in {"resolve_targets", "plan_grasp"}:
            obj, dest = parameters["objectId"], parameters["destinationId"]
            if obj not in OBJECT_MODELS or obj not in entities:
                return Result(False, "OBJECT_NOT_VISIBLE")
            if dest not in DESTINATION_MODELS or dest not in entities:
                return Result(False, "DESTINATION_NOT_VISIBLE")
            if name == "plan_grasp":
                joints, age, _ = self.node.joint_snapshot()
                if age > .5:
                    raise ValueError("JOINT_FEEDBACK_STALE")
                base = self.node.runtime.base_pose.copy()
                goal = np.array(entities[obj].pose_xyz_quat[:3])+[0., 0., .085]
                upright = chain_poses(arm_links("left"), joints, base=base)[-1][:3, :3].T @ [0., 0., 1.]
                solve_tip(goal, joints, base, upright=upright)
                self.plan = {"task": command.task_id, "object": obj, "destination": dest,
                             "position": np.array(entities[obj].pose_xyz_quat[:3]), "base": base,
                             "created": time.monotonic(), "observation": frame.observation_id,
                             "upright": upright}
            return Result(True, observation_id=frame.observation_id,
                          payload={"mode": "sim_suction", "perception": "commissioned_rgbd_colour"})
        if name == "verify_grasp":
            return self.verify_grasp(parameters["objectId"])
        if name == "verify_placement":
            return self.verify_placement(parameters["objectId"], parameters["destinationId"])
        if name == "recover_to_safe_pose":
            # Holding is an observable recovery state: never drop the payload to
            # make a generic home-pose command appear successful.
            if self.state()["attached"]:
                self.node.hold_joints()
                return Result(False, "PAYLOAD_HELD_REQUIRES_PLACE")
            _positions, age, _ = self.node.joint_snapshot()
            if age > .5:
                raise ValueError("JOINT_FEEDBACK_STALE")
            return self.move_tip(np.array([.34, -.15, .96]), relative=True)
        if name == "manipulation.pick":
            obj = command.target_ref
            if self.plan is None or self.plan["task"] != command.task_id or self.plan["object"] != obj:
                return Result(False, "GRASP_PLAN_REQUIRED")
            if time.monotonic()-self.plan["created"] > 30 or obj not in entities:
                return Result(False, "GRASP_PLAN_STALE")
            position = np.array(entities[obj].pose_xyz_quat[:3])
            if np.linalg.norm(position-self.plan["position"]) > .04 or np.linalg.norm(
                    self.node.runtime.base_pose-self.plan["base"]) > .03:
                return Result(False, "GRASP_PLAN_STALE")
            if self.state()["attached"]:
                return Result(False, "GRIPPER_OCCUPIED")
            # Rise before traversing the tabletop. A direct diagonal approach
            # swept the wrist through the cup and pushed it 6 cm out of grasp.
            current = self.tip_in_odom(self.state())
            for point, upright in ((current+[0., 0., .10], None),
                                   (position+[0., 0., .16], self.plan["upright"]),
                                   (position+[0., 0., .085], self.plan["upright"])):
                motion = self.move_tip(point, upright=upright)
                if not motion.success:
                    return motion
            before = self.state()
            original = np.array(before["objects"][OBJECT_MODELS[obj]][:3])
            attach = self.suction("attach", target=OBJECT_MODELS[obj])
            if not attach.success:
                return attach
            state = self.state()
            tip = pose_matrix(state["tips"]["left"])
            # Orientation of the vertical axis in the acquired end-link frame.
            self.acquisition = {"object": obj, "task": command.task_id, "z": float(original[2]),
                                "upright": tip[:3, :3].T @ [0., 0., 1.]}
            current = self.tip_in_odom(state)
            result = self.move_tip(current+[0., 0., .10], upright=self.acquisition["upright"])
            if not result.success:
                return result
            return self.verify_grasp(obj)
        if name == "manipulation.place":
            dest = command.target_ref
            state = self.state()
            if not state["attached"] or self.acquisition is None:
                return Result(False, "NO_HELD_OBJECT")
            if self.acquisition.get("task") != command.task_id:
                return Result(False, "PAYLOAD_OWNED_BY_OTHER_TASK")
            if dest not in DESTINATION_MODELS or dest not in entities:
                return Result(False, "DESTINATION_NOT_VISIBLE")
            obj = self.acquisition["object"]
            # Relative payload position is suction proprioception. Destination
            # remains the measured RGB-D surface, never the simulator registry.
            offset = np.array(state["tips"]["left"][:3])-np.array(state["objects"][OBJECT_MODELS[obj]][:3])
            sim_to_odom = self.node.runtime.base_pose @ np.linalg.inv(pose_matrix(state["robotPose"]))
            offset = sim_to_odom[:3, :3] @ offset
            destination = np.array(entities[dest].pose_xyz_quat[:3])
            upright = self.acquisition["upright"]
            for height in (.16, .075):
                result = self.move_tip(destination+[0., 0., height]+offset, upright=upright)
                if not result.success:
                    return result
            result = self.suction("detach")
            if not result.success:
                return result
            # Retreat makes the released body visible and leaves gravity to
            # settle it onto the surface before independent verification.
            result = self.move_tip(self.tip_in_odom(self.state())+[0., 0., .08])
            if not result.success:
                return result
            return self.verify_placement(obj, dest)
        return Result(False, "CAPABILITY_UNAVAILABLE")

    def tip_in_odom(self, state):
        transform = self.node.runtime.base_pose @ np.linalg.inv(pose_matrix(state["robotPose"]))
        return (transform @ np.r_[state["tips"]["left"][:3], 1.])[:3]

    def move_tip(self, target, *, upright=None, relative=False):
        if self.backend.cancel_event.is_set():
            return Result(False, "CANCELLED")
        joints, age, _ = self.node.joint_snapshot()
        if age > .5:
            return Result(False, "JOINT_FEEDBACK_STALE")
        base = self.node.runtime.base_pose.copy()
        if relative:
            target = (base @ np.r_[target, 1.])[:3]
        current = chain_poses(arm_links("left"), joints, base=base)[-1][:3, 3]
        count = max(1, math.ceil(float(np.linalg.norm(target-current))/.04))
        if count > 24:
            return Result(False, "WORKCELL_MOTION_LIMIT")
        chunk = []
        for point in np.linspace(current, target, count+1)[1:]:
            joints = solve_tip(point, joints, base, upright=upright)
            chunk.append({name+".pos": value for name, value in joints.items()})
        return execute_chunk(self.node, chunk, self.backend.cancel_event)

    def suction(self, operation, *, target=""):
        if self.backend.cancel_event.is_set():
            return Result(False, "CANCELLED")
        identity = self.node.suction_command(operation, side="left", target=target)
        deadline = time.monotonic()+2
        while time.monotonic() < deadline:
            if self.backend.cancel_event.is_set():
                return Result(False, "CANCELLED")
            state = self.state()
            if state["commandId"] == identity:
                return Result(state["code"] == "OK", state["code"])
            time.sleep(.02)
        return Result(False, "SUCTION_OUTCOME_UNKNOWN")

    def stable_samples(self, predicate, *, seconds=3.):
        deadline, previous, accepted = time.monotonic()+seconds, None, []
        while time.monotonic() < deadline:
            if self.backend.cancel_event.is_set():
                raise ValueError("CANCELLED")
            state = self.state()
            if state["sequence"] != previous:
                previous = state["sequence"]
                value = predicate(state)
                if value is None:
                    accepted = []
                elif accepted and np.linalg.norm(value-accepted[-1][1]) > .004:
                    accepted = [(state["simTimeNs"], value)]
                else:
                    accepted.append((state["simTimeNs"], value))
                if len(accepted) >= 3 and accepted[-1][0]-accepted[0][0] >= 200_000_000:
                    return state
            time.sleep(.025)
        return None

    def verify_grasp(self, obj):
        if obj not in OBJECT_MODELS or self.acquisition is None or self.acquisition["object"] != obj:
            return Result(False, "GRASP_NOT_VERIFIED")

        def held(state):
            position = np.array(state["objects"][OBJECT_MODELS[obj]][:3])
            if not state["attached"] or state["held"] != OBJECT_MODELS[obj] or position[2]-self.acquisition["z"] < .055:
                return None
            return position-np.array(state["tips"]["left"][:3])

        state = self.stable_samples(held)
        return Result(bool(state), "OK" if state else "GRASP_NOT_VERIFIED",
            payload={"mode": "sim_suction", "evidenceSource": "gazebo_physics",
                     "physicsJson": json.dumps(state or {}, sort_keys=True)})

    def verify_placement(self, obj, dest):
        if obj not in OBJECT_MODELS or dest not in DESTINATION_MODELS:
            return Result(False, "TARGET_UNKNOWN")

        def placed(state):
            position = np.array(state["objects"][OBJECT_MODELS[obj]][:3])
            destination = np.array(state["objects"][DESTINATION_MODELS[dest]][:3])
            delta = position-destination
            if state["attached"] or np.max(np.abs(delta[:2])) > .043 or abs(delta[2]-.075) > .012:
                return None
            rotation = pose_matrix(state["objects"][OBJECT_MODELS[obj]])[:3, :3]
            if rotation[2, 2] < math.cos(.15):
                return None
            return position

        state = self.stable_samples(placed)
        return Result(bool(state), "OK" if state else "PLACEMENT_NOT_VERIFIED",
            payload={"mode": "sim_suction", "evidenceSource": "gazebo_physics",
                     "physicsJson": json.dumps(state or {}, sort_keys=True)})
