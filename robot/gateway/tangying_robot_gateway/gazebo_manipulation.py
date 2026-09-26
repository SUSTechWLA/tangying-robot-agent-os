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


def solve_tip(target, joints, base, *, upright=None, side="left", fixed_gripper=None, tool=None):
    links = arm_links(side)
    commanded_links = links[:-1] if fixed_gripper is not None else links
    initial = np.array([joints[link.motor] for link in commanded_links])
    low = np.array([link.range_min+.001 for link in commanded_links])
    high = np.array([link.range_max-.001 for link in commanded_links])
    initial = np.clip(initial, low, high)

    def targets(values):
        result = dict(zip([link.motor for link in commanded_links], values, strict=True))
        if fixed_gripper is not None:
            if not links[-1].range_min <= fixed_gripper <= links[-1].range_max:
                raise ValueError("GRIPPER_TARGET_OUTSIDE_LIMITS")
            result[links[-1].motor] = float(fixed_gripper)
        return result

    def pose(values):
        from .gazebo_tools import tool_pose
        return tool_pose(chain_poses(links, targets(values), base=base), tool)

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
    return {name: float(value) for name, value in targets(solution.x).items()}


class GazeboManipulation:
    def __init__(self, backend):
        self.backend = backend
        self.node = backend.node
        self.side = "left"
        # Frozen FK identifiers are driver IDs, not anatomical side inference.
        # plan_grasp selects a reachable chain from current metric geometry.
        self.tool = None
        if backend.home:
            from .gazebo_tools import tool_commissioning
            self.tool, self.tool_revision = tool_commissioning()
        self.plan = None
        self.acquisition = None
        self.verification = None

    def capture(self):
        frame = self.backend.observe(ObservationRequest(source_id=self.node.runtime.robot_id+"/head-rgbd"))
        if not 0 <= int(time.time()*1000)-frame.wall_time_unix_ms <= 1000:
            raise ValueError("SENSOR_STALE")
        return frame, {entity.entity_id: entity for entity in frame.entities}

    def state(self):
        state = self.node.suction_snapshot()
        if self.tool is not None and state.get("toolCommissioning") != self.tool:
            raise ValueError("TOOL_COMMISSIONING_MISMATCH")
        return state

    def stow_for_navigation(self, cancel):
        """Fold both empty arms inside the declared mobile-base footprint."""
        if self.state()["attached"]:
            return Result(False, "PAYLOAD_HELD_REQUIRES_PLACE")
        joints, age, _ = self.node.joint_snapshot()
        if age > .5:
            return Result(False, "JOINT_FEEDBACK_STALE")
        targets = {link.motor: value for side in ("left", "right")
                   for link, value in zip(arm_links(side), (0., 3.1, 1., 0., 0., 0.) if self.backend.home else (0., 2.5, 1.5, 1., 0., 0.), strict=True)}
        error = max(abs(joints[name]-value) for name, value in targets.items())
        if error <= .04:
            return Result(True)
        count = max(1, math.ceil(error/.2))
        chunk = [{name+".pos": float(joints[name]+(value-joints[name])*fraction)
                  for name, value in targets.items()} for fraction in np.linspace(0., 1., count+1)[1:]]
        return execute_chunk(self.node, chunk, cancel)

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
                goal = np.array(entities[obj].pose_xyz_quat[:3])+[0., 0., self.tool["contactHeightM"] if self.tool else .082]
                # A commissioned tool axis, independent of the starting joint
                # posture. Deriving it from a folded navigation arm changes the
                # grasp orientation and can make the release pose unreachable.
                candidates = []
                for side in (("left","right") if self.backend.home else ("left",)):
                    links = arm_links(side)
                    from .gazebo_tools import tool_pose
                    upright = tool_pose(chain_poses(links, {link.motor: 0. for link in links},
                                          base=np.eye(4)), self.tool)[:3,:3].T @ [0.,0.,1.]
                    try:
                        target_joints = solve_tip(goal,joints,base,upright=upright,side=side,
                                                  fixed_gripper=self.tool["gripperOpenRad"] if self.tool else None, tool=self.tool)
                        if self.tool is not None:
                            # Check all loaded-arm workspace milestones before
                            # acquisition. Both locations came from this capture;
                            # no registry pose supplies a missing destination.
                            destination = np.array(entities[dest].pose_xyz_quat[:3])
                            offset = np.array([0., 0., self.tool["contactHeightM"]])
                            preflight = target_joints
                            views = [(base @ np.r_[point, 1.])[:3]
                                     for point in self.tool['observationWaypointsBaseM'][side]]
                            for point in [goal+[0., 0., .093],
                                          goal+base[:3, :3] @ [-.04, 0., .13],
                                          *views, destination+offset+[0., 0., .185],
                                          destination+offset+[0., 0., .075]]:
                                preflight = solve_tip(point,preflight,base,upright=upright,side=side,
                                    fixed_gripper=self.tool["gripperOpenRad"],tool=self.tool)
                    except ValueError:
                        continue
                    cost = sum((value-joints[motor])**2 for motor,value in target_joints.items())
                    candidates.append((cost,side,upright))
                if not candidates:
                    raise ValueError("GRASP_TARGET_UNREACHABLE")
                _,self.side,upright = min(candidates,key=lambda candidate:candidate[0])
                self.plan = {"task": command.task_id, "object": obj, "destination": dest,
                             "position": np.array(entities[obj].pose_xyz_quat[:3]), "base": base,
                             "created": time.monotonic(), "observation": frame.observation_id,
                             "upright": upright}
            return Result(True, observation_id=frame.observation_id,
                          payload={"mode": "sim_suction", "perception": "rgbd-household-metric-shape-v1" if self.backend.home else "commissioned_rgbd_colour"})
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
            return self.stow_for_navigation(self.backend.cancel_event)
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
            overhead = position+[0.,0.,.16]
            approach = []
            if current[2] < overhead[2]:
                raised = current.copy()
                raised[2] = overhead[2]
                approach.append((raised,None))
            # Unfold the empty arm before constraining tool orientation. The
            # folded navigation tip has no upright reachable neighbourhood with
            # five arm axes; a jaw must not manufacture a sixth arm axis.
            approach.append((overhead,None))
            approach.append((overhead,self.plan["upright"]))
            for point, upright in approach:
                motion = self.move_tip(point, upright=upright)
                if not motion.success:
                    return motion
            # Re-observe after the long approach: arm loading can change base
            # suspension height even when planar odometry has not moved. Close
            # the last centimetres from RGB-D, never from the object oracle.
            completed_ns, completed_ms = time.monotonic_ns(), int(time.time()*1000)
            if not self.backend.wait_command_capture(command, completed_ns, completed_ms):
                return Result(False, "POSTCONDITION_OBSERVATION_TIMEOUT")
            _frame, visible = self.capture()
            if obj not in visible:
                return Result(False, "OBJECT_NOT_VISIBLE")
            refreshed = np.array(visible[obj].pose_xyz_quat[:3])
            if np.linalg.norm(refreshed-position) > .04:
                return Result(False, "GRASP_PLAN_STALE")
            # One bounded final descent from this fresh measured target. The
            # acquired pose deliberately occludes the mug, so demanding a
            # second visual detection there prevents any grasp. The near-tool
            # interlock and independent lift verification still gate success.
            motion = self.move_tip(refreshed+[0., 0., self.tool["contactHeightM"] if self.tool else .082], upright=self.plan["upright"])
            if not motion.success:
                return motion
            before = self.state()
            original = np.array(before["objects"][OBJECT_MODELS[obj]][:3])
            attach = self.suction("attach", target=OBJECT_MODELS[obj])
            if not attach.success:
                return attach
            state = self.state()
            tip = pose_matrix(state["tips"][self.side])
            # Orientation of the vertical axis in the acquired end-link frame.
            self.acquisition = {"object": obj, "task": command.task_id, "z": float(original[2]),
                                "upright": tip[:3, :3].T @ [0., 0., 1.]}
            current = self.tip_in_odom(state)
            # Retract slightly while lifting: a vertical-only lift at the far
            # edge can exceed the arm workspace even though the grasp is
            # reachable. Keep the displacement in the current base frame.
            lift = self.node.runtime.base_pose[:3, :3] @ np.array([-.04, 0., .13 if self.backend.home else .10])
            result = self.move_tip(current+lift, upright=self.acquisition["upright"])
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
            if dest not in DESTINATION_MODELS:
                return Result(False, "DESTINATION_NOT_VISIBLE")
            obj = self.acquisition["object"]
            if state.get("held") != OBJECT_MODELS[obj]:
                return Result(False, "GRASP_NOT_VERIFIED")
            if dest not in entities and self.backend.home:
                # One bounded active-perception motion clears the held arm
                # from the tabletop view. It needs no destination pose and
                # keeps the acquired vertical axis and payload ownership.
                views = self.tool['observationWaypointsBaseM'][self.side]
                for point in views:
                    result = self.move_tip(point, relative=True, upright=self.acquisition["upright"])
                    if not result.success:
                        return result
                if not self.backend.wait_command_capture(command, time.monotonic_ns(), int(time.time()*1000)):
                    return Result(False, "POSTCONDITION_OBSERVATION_TIMEOUT")
                frame, entities = self.capture()
                state = self.state()
            if dest not in entities:
                return Result(False, "DESTINATION_NOT_VISIBLE")
            # Relative payload position is suction proprioception. Destination
            # remains the measured RGB-D surface, never the simulator registry.
            payload_frame = self.payload_frame(state, obj)
            destination = np.array(entities[dest].pose_xyz_quat[:3])
            upright = self.acquisition["upright"]
            # In the home, transit above the 10 cm rim with the 6 cm payload
            # half-height plus 25 mm clearance. The five-axis arm can reach it;
            # a low payload-centre waypoint would sweep the mug into the rim.
            transit_height = .185 if self.backend.home else .12
            joints, age, _ = self.node.joint_snapshot()
            if age > .5:
                return Result(False, 'JOINT_FEEDBACK_STALE')
            from .gazebo_tools import tool_pose
            current = tool_pose(chain_poses(arm_links(self.side), joints,
                base=self.node.runtime.base_pose), payload_frame)[:3, 3]
            overhead = destination+[0., 0., transit_height]
            if current[2] < overhead[2]:
                raised = current.copy()
                raised[2] = overhead[2]
                result = self.move_tip(raised, upright=upright, tool_frame=payload_frame)
                if not result.success:
                    return result
            for height in (transit_height, .075):
                result = self.move_tip(destination+[0., 0., height], upright=upright, tool_frame=payload_frame)
                if not result.success:
                    return result
            result = self.suction("detach")
            if not result.success:
                return result
            # Retreat makes the released body visible and leaves gravity to
            # settle it onto the surface before independent verification.
            retreat = self.node.runtime.base_pose[:3, :3] @ np.array([-.04, 0., .08])
            result = self.move_tip(self.tip_in_odom(self.state())+retreat)
            if not result.success:
                return result
            # The next subtask grounds its object before requesting navigation.
            # Clear the camera here; waiting until the next navigation command
            # leaves a successfully released payload hiding the remaining target.
            result = self.stow_for_navigation(self.backend.cancel_event)
            if not result.success:
                return result
            return self.verify_placement(obj, dest)
        return Result(False, "CAPABILITY_UNAVAILABLE")

    def tip_in_odom(self, state):
        transform = self.node.runtime.base_pose @ np.linalg.inv(pose_matrix(state["robotPose"]))
        return (transform @ np.r_[state["tips"][self.side][:3], 1.])[:3]

    def payload_frame(self, state, obj):
        """Rigid payload offset in the tool frame, not a fixed world vector."""
        tip = pose_matrix(state['tips'][self.side])
        local_offset = tip[:3, :3].T @ (tip[:3, 3]-state['objects'][OBJECT_MODELS[obj]][:3])
        tool = self.tool or {'parentLinkIndex': 6, 'offsetM': [0., 0., 0.]}
        return {**tool, 'offsetM': (np.asarray(tool['offsetM'])-local_offset).tolist()}

    def move_tip(self, target, *, upright=None, relative=False, tool_frame=None):
        if self.backend.cancel_event.is_set():
            return Result(False, "CANCELLED")
        joints, age, _ = self.node.joint_snapshot()
        if age > .5:
            return Result(False, "JOINT_FEEDBACK_STALE")
        base = self.node.runtime.base_pose.copy()
        if relative:
            target = (base @ np.r_[target, 1.])[:3]
        from .gazebo_tools import tool_pose
        active_tool = self.tool if tool_frame is None else tool_frame
        current = tool_pose(chain_poses(arm_links(self.side), joints, base=base), active_tool)[:3, 3]
        count = max(1, math.ceil(float(np.linalg.norm(target-current))/.04))
        if count > 24:
            return Result(False, "WORKCELL_MOTION_LIMIT")
        chunk = []
        for point in np.linspace(current, target, count+1)[1:]:
            joints = solve_tip(point, joints, base, upright=upright, side=self.side,
                               fixed_gripper=self.tool["gripperOpenRad"] if self.tool else None, tool=active_tool)
            chunk.append({name+".pos": value for name, value in joints.items()})
        return execute_chunk(self.node, chunk, self.backend.cancel_event)

    def suction(self, operation, *, target=""):
        if self.backend.cancel_event.is_set():
            return Result(False, "CANCELLED")
        identity = self.node.suction_command(operation, side=self.side, target=target)
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
                    accepted = [(state["simTimeNs"], value, state["sequence"])]
                else:
                    accepted.append((state["simTimeNs"], value, state["sequence"]))
                if len(accepted) >= 3 and accepted[-1][0]-accepted[0][0] >= 200_000_000:
                    return state, accepted
            time.sleep(.025)
        return None, accepted

    def verify_grasp(self, obj):
        if obj not in OBJECT_MODELS or self.acquisition is None or self.acquisition["object"] != obj:
            return Result(False, "GRASP_NOT_VERIFIED")

        def held(state):
            position = np.array(state["objects"][OBJECT_MODELS[obj]][:3])
            if not state["attached"] or state["held"] != OBJECT_MODELS[obj] or position[2]-self.acquisition["z"] < .055:
                return None
            return position-np.array(state["tips"][self.side][:3])

        state, samples = self.stable_samples(held)
        self._record_verification("verify_grasp",obj,"",state,samples)
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
            if state["attached"] or np.any(np.abs(delta[:2]) > (np.array([.015,.020]) if self.backend.home else .043)) or abs(delta[2]-.075) > .012:
                return None
            rotation = pose_matrix(state["objects"][OBJECT_MODELS[obj]])[:3, :3]
            if rotation[2, 2] < math.cos(.15):
                return None
            return position

        state, samples = self.stable_samples(placed)
        self._record_verification("verify_placement",obj,dest,state,samples)
        return Result(bool(state), "OK" if state else "PLACEMENT_NOT_VERIFIED",
            payload={"mode": "sim_suction", "evidenceSource": "gazebo_physics",
                     "physicsJson": json.dumps(state or {}, sort_keys=True)})

    def _record_verification(self, kind, obj, dest, state, samples):
        relation = f"inside:{dest}" if dest else f"held_by:{self.node.runtime.robot_id}"
        self.verification = {"kind":kind,"object_id":obj,"destination_id":dest,"passed":bool(state),
            "sample_count":len(samples),"stable_duration_s":(samples[-1][0]-samples[0][0])/1e9 if len(samples)>1 else 0.,
            "max_displacement_m":max((float(np.linalg.norm(value-samples[0][1])) for _,value,_ in samples),default=0.),
            "expected_relation":relation,"observed_relation":relation if state else "",
            "evidence_source":"gazebo_physics","mode":"sim_suction",
            "samples":[{"sequence":sequence,"sim_time_ns":stamp,"tracked_position_m":value.tolist()} for stamp,value,sequence in samples],
            "sim_time_ns":state["simTimeNs"] if state else 0}
