"""Camera-grounded motion with explicitly simulated suction and physics checks.

The common service still owns approval, leases, cancellation and durable receipts.
The plugin only attaches near the actual end link. This module plans bounded
joint targets; it never writes robot/object poses or accepts an ACK as proof.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from dataclasses import replace

import numpy as np
from scipy.optimize import least_squares

from .arm_kinematics import arm_links, chain_poses
from .gazebo_actuation import execute_chunk, joint_ramp_step, validate_chunk
from .gazebo_perception import DESTINATION_MODELS, OBJECT_MODELS
from .home_commissioning import HOUSEHOLD_DIMENSIONS, HOUSEHOLD_TRAY_RIM_HEIGHT_M
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


def plan_empty_arm_approach(position, joints, base, *, upright, side, tool):
    """Unfold above the measured target without crossing an IK branch mid-path.

    A folded five-axis arm cannot follow every Cartesian chord to a reachable
    endpoint. Plan the bounded joint path first, then check its FK along the
    actual per-joint 25 mrad actuator ramp before sending any waypoint.
    This clearance check covers the empty tool's overhead approach; it is not a
    substitute for whole-body collision commissioning or grasp verification.
    """
    from .gazebo_tools import tool_pose
    links = arm_links(side)
    overhead = np.asarray(position, dtype=float)+[0., 0., .16]
    current = tool_pose(chain_poses(links, joints, base=base), tool)[:3, 3]
    # Only unfold from a posture already above the overhead plane. Never make
    # a low, possibly obstructed posture safe by inventing a lower clearance.
    if current[2] < overhead[2]-.008:
        raise ValueError("GRASP_APPROACH_CLEARANCE")
    target = solve_tip(overhead, joints, base, upright=upright, side=side,
                       fixed_gripper=tool["gripperOpenRad"], tool=tool)
    count = max(1, math.ceil(max(abs(target[name]-joints[name]) for name in target)/.1))
    if count > 64 or np.linalg.norm(overhead-current) > .96:
        raise ValueError("WORKCELL_MOTION_LIMIT")
    chunk = [{name+".pos": float(joints[name]+fraction*(value-joints[name]))
              for name, value in target.items()}
             for fraction in np.linspace(0., 1., count+1)[1:]]
    targets = validate_chunk(chunk)
    commanded = {name: float(joints[name]) for name in target}
    for waypoint in targets:
        while max(abs(waypoint[name]-commanded[name]) for name in waypoint) > 1e-9:
            commanded = {name: value+max(-.025, min(.025, waypoint[name]-value))
                         for name, value in commanded.items()}
            tip = tool_pose(chain_poses(links, commanded, base=base), tool)[:3, 3]
            # Preserve the full 16 cm approach plane (minus the existing IK
            # residual tolerance), including intermediate actuator setpoints.
            if not np.isfinite(tip).all() or tip[2] < overhead[2]-.008:
                raise ValueError("GRASP_APPROACH_CLEARANCE")
    return chunk, target


def plan_tip_chunk(target, joints, base, *, upright=None, side="left", tool=None, fixed_gripper=None):
    """Pure version of the Cartesian planner shared by preflight and motion."""
    from .gazebo_tools import tool_pose
    current = tool_pose(chain_poses(arm_links(side), joints, base=base), tool)[:3, 3]
    count = max(1, math.ceil(float(np.linalg.norm(target-current))/.04))
    if count > 24:
        raise ValueError("WORKCELL_MOTION_LIMIT")
    chunk = []
    for point in np.linspace(current, target, count+1)[1:]:
        joints = solve_tip(point, joints, base, upright=upright, side=side,
                           fixed_gripper=fixed_gripper, tool=tool)
        chunk.append({name+".pos": value for name, value in joints.items()})
    validate_chunk(chunk)
    return chunk, joints


def grasp_view_positions(position, base, camera_origin=None):
    """Four bounded empty-arm views perpendicular to the calibrated view ray.

    These are proposed observation poses, never proof that the target is visible.
    Each must pass the existing trajectory planner and a new measured capture.
    """
    origin = base[:3, 3] if camera_origin is None else np.asarray(camera_origin)
    ray = np.asarray(position)[:2]-origin[:2]
    length = float(np.linalg.norm(ray))
    if not np.isfinite(length) or length < .05:
        raise ValueError("GRASP_VIEW_GEOMETRY_INVALID")
    lateral = np.array([-ray[1], ray[0], 0.])/length
    return [np.asarray(position)+offset*lateral for offset in (-.12, -.08, .08, .12)]


def payload_vertical_extent(rotation, dimensions):
    """Conservative world-Z half extent of a measured rigid payload."""
    rotation, dimensions = np.asarray(rotation), np.asarray(dimensions)
    if (rotation.shape != (3, 3) or dimensions.shape != (3,)
            or not np.isfinite(rotation).all() or not np.isfinite(dimensions).all()
            or np.any(dimensions <= 0)
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(rotation), 1., atol=1e-6)):
        raise ValueError("PAYLOAD_GEOMETRY_INVALID")
    return float(.5*np.abs(rotation[2]) @ dimensions)


def loaded_lift_waypoints(tip, payload_position, base, *, rim_z, dimensions):
    """Clear the complete payload vertically before the bounded XY retract.

    The rim height is a camera measurement plus commissioned rim geometry;
    payload dimensions are shape priors, never a simulator destination pose.
    """
    tip, payload_position, base, dimensions = map(np.asarray, (tip, payload_position, base, dimensions))
    if (tip.shape != (3,) or payload_position.shape != (3,) or base.shape != (4, 4)
            or not all(np.isfinite(x).all() for x in (tip, payload_position, base))
            or not np.isfinite(rim_z)):
        raise ValueError("PAYLOAD_GEOMETRY_INVALID")
    payload_vertical_extent(np.eye(3), dimensions)
    # Maximum bounding-box half height within the existing 0.15 rad upright
    # cone; actual measured orientation is independently checked before XY.
    half_height = .5*(dimensions[2]*math.cos(.15)+np.linalg.norm(dimensions[:2])*math.sin(.15))
    # Reserve 12 mm in the commanded height for the existing 8 mm IK residual
    # and 4 mm stable-position window. Focus005 measured a 6--12 mm tip deficit
    # despite joint ACK. This reserve changes the planned pose, never the
    # independently measured 25 mm gate; larger deficits still stop safely.
    tracking_allowance = .008+.004
    # Require a measured 20 mm transport reserve before the XY segment.
    # Synchronized ideal FK removes the old path dip but cannot bound real
    # servo transients. The moving payload still has the original 25 mm gate.
    transport_allowance = .020
    rise = max(.13, float(rim_z)+.025+transport_allowance+half_height+tracking_allowance-float(payload_position[2]))
    if rise > .20:
        raise ValueError("PAYLOAD_CLEARANCE_UNREACHABLE")
    vertical = tip+[0., 0., rise]
    retract = base[:3, :3] @ [-.04, 0., 0.]
    retract[2] = 0.  # Raw IMU tilt must not turn a horizontal retract downward.
    return [vertical, vertical+retract]


def loaded_retract_target(measured_tip, lifts):
    """Keep the vertical request when replanning from measured feedback.

    A joint ACK can leave the tool below its Cartesian request. Using that
    measured Z as the next request compounds the residual on each segment.
    Higher measured positions must also never be commanded downward.
    """
    measured_tip, lifts = np.asarray(measured_tip), np.asarray(lifts)
    if (measured_tip.shape != (3,) or lifts.shape != (2, 3)
            or not np.isfinite(measured_tip).all() or not np.isfinite(lifts).all()
            or abs(lifts[1, 2]-lifts[0, 2]) > 1e-9):
        raise ValueError("PAYLOAD_GEOMETRY_INVALID")
    target = measured_tip+(lifts[1]-lifts[0])
    target[2] = max(float(measured_tip[2]), float(lifts[0, 2]))
    return target


def plan_loaded_retract_chunk(target, joints, base, *, upright, side, tool):
    """Check the exact synchronized joint setpoints before loaded retraction.

    FK is a path check, not physical payload evidence. The independent physics
    guard still checks the whole payload at every execution tick.
    """
    from .gazebo_tools import tool_pose
    links = arm_links(side)
    initial = tool_pose(chain_poses(links, joints, base=base), tool)
    chunk, end = plan_tip_chunk(target, joints, base, upright=upright,
        side=side, tool=tool, fixed_gripper=tool["gripperOpenRad"])
    floor = min(float(initial[2, 3]), float(target[2]))-.008
    commanded = {name: float(joints[name]) for name in end}
    for waypoint in validate_chunk(chunk):
        while max(abs(waypoint[name]-commanded[name]) for name in waypoint) > 1e-9:
            commanded = joint_ramp_step(commanded, waypoint, coordinated=True)
            tip = tool_pose(chain_poses(links, commanded, base=base), tool)
            if (not np.isfinite(tip).all() or tip[2, 3] < floor
                    or np.linalg.norm(tip[:3, :3] @ upright-[0., 0., 1.]) > .12):
                raise ValueError("PAYLOAD_RETRACT_PATH_UNREACHABLE")
    return chunk, end


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
        self._evidence = threading.local()
        self._plan_lock = threading.Lock()

    def capture(self):
        frame = self.backend.observe(ObservationRequest(source_id=self.node.runtime.robot_id+"/head-rgbd"))
        if not 0 <= int(time.time()*1000)-frame.wall_time_unix_ms <= 1000:
            raise ValueError("SENSOR_STALE")
        entities = {entity.entity_id: entity for entity in frame.entities}
        if len(entities) != len(frame.entities):
            raise ValueError("OBJECT_IDENTITY_AMBIGUOUS")
        return frame, entities

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
        tracked = self.backend.home and command.capability in {"plan_grasp", "manipulation.pick"}
        self._evidence.value = ({"schemaVersion": "gazebo.grasp-execution.v1",
            "commandId": command.command_id, "taskId": command.task_id,
            "capability": command.capability, "physicalStarted": False, "phases": []}
            if tracked else None)
        acquired = not tracked or self._plan_lock.acquire(blocking=False)
        try:
            if not acquired:
                result = Result(False, "ROBOT_BUSY")
            else:
                if tracked and command.capability == "plan_grasp":
                    # Once a new planning attempt owns the lock, failure must
                    # not leave an older same-task plan authorized for motion.
                    self.plan = None
                if tracked and command.capability == "manipulation.pick" and self.plan is not None and "sourceJSON" in self.plan:
                    self._bind_source(self.plan)
                result = self._execute(command)
        except (ValueError, KeyError) as error:
            result = Result(False, str(error).strip("'"), confidence=0.)
        finally:
            if tracked and acquired:
                self._plan_lock.release()
        evidence = self._evidence.value
        if evidence is not None:
            reasons = {"GRASP_PLAN_STALE": "The original target could not be freshly associated within the unchanged grasp safety limits.",
                       "GRASP_PLAN_REQUIRED": "A matching original grasp plan is required before motion.",
                       "POSTCONDITION_OBSERVATION_TIMEOUT": "No valid new capture arrived after the completed arm motion."}
            evidence["outcome"] = {"success": result.success, "code": result.code,
                                   "reason": result.message or reasons.get(result.code, result.code)}
            # The shared service durably journals terminal message bytes even
            # for failures. Its postcondition observation_id is a different
            # capture and must never replace this original plan source.
            result = replace(result, message=json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        return result

    def _phase(self, phase, **values):
        evidence = getattr(self._evidence, "value", None)
        if evidence is not None:
            evidence["phases"].append({"phase": phase, "atMonotonicNs": str(time.monotonic_ns()), **values})

    def _bind_source(self, plan):
        evidence = getattr(self._evidence, "value", None)
        if evidence is not None:
            evidence.update(planSource=json.loads(plan["sourceJSON"]), sourceToken=plan["sourceToken"])

    def _plan_source(self, command, frame, entity, base, created_ns, destination=None):
        state = frame.robot_state
        perception = state.get("perception", {})
        stamp = str(perception.get("sensor_stamp_ns", ""))
        calibration = perception.get("calibration_revision", "")
        if (not frame.observation_id or not calibration or not stamp.isdigit() or int(stamp) <= 0
                or not entity.category or not .9 <= entity.confidence <= 1
                or frame.reconstruction.get("robotId") != self.node.runtime.robot_id):
            raise ValueError("GRASP_PLAN_SOURCE_INVALID")
        bbox = next((e.get("bbox", e.get("boundingBox")) for e in frame.reconstruction.get("entities", [])
                     if e.get("entityId") == entity.entity_id), None)
        source = {"captureId": frame.observation_id, "capturedAtUnixMs": str(frame.wall_time_unix_ms),
                "sensorStampNs": stamp, "calibrationRevision": calibration,
                "taskId": command.task_id, "commandId": command.command_id,
                "robotId": self.node.runtime.robot_id, "catalogRevision": command.catalog_revision,
                "objectId": entity.entity_id, "category": entity.category,
                "recognition": entity.attributes.get("recognition", ""), "confidence": entity.confidence,
                "position": list(map(float, entity.pose_xyz_quat[:3])),
                "base": pose_matrix(state["base_pose"]).tolist(), "motionBase": base.tolist(),
                "createdMonotonicNs": str(created_ns), "toolRevision": self.tool_revision,
                "sourceOriginalBBox": bbox, "sourceOriginalBBoxAvailable": bbox is not None}
        if destination is not None:
            position = np.asarray(destination.pose_xyz_quat[:3])
            if (position.shape != (3,) or not np.isfinite(position).all()
                    or destination.category != "storage_bin" or not .9 <= destination.confidence <= 1):
                raise ValueError("GRASP_DESTINATION_SOURCE_INVALID")
            source.update(destinationId=destination.entity_id, destinationPosition=position.tolist(),
                          destinationRecognition=destination.attributes.get("recognition", ""))
        return source

    def _pick_guard(self, command, plan, frame=None):
        """Rechecked before every physical phase; no budget can reset on a view."""
        if self.backend.cancel_event.is_set():
            return "CANCELLED"
        if command.deadline_unix_ms and int(time.time()*1000) >= command.deadline_unix_ms:
            return "COMMAND_DEADLINE_EXCEEDED"
        if self.plan is not plan:
            return "GRASP_PLAN_REPLACED"
        age = time.monotonic()-plan["created"]
        if not 0 <= age <= 30:
            return "GRASP_PLAN_EXPIRED"
        base = self.node.runtime.base_pose
        if base is None or not np.isfinite(base).all() or np.linalg.norm(base-plan["base"]) > .03:
            return "GRASP_BASE_CHANGED"
        source = json.loads(plan["sourceJSON"])
        if (self.node.runtime.robot_id != source["robotId"]
                or (command.robot_id and command.robot_id != source["robotId"])
                or command.task_id != source["taskId"]
                or command.catalog_revision != source["catalogRevision"]):
            return "GRASP_IDENTITY_CHANGED"
        if self.node.runtime.calibration_revision != source["calibrationRevision"]:
            return "GRASP_CALIBRATION_CHANGED"
        if self.tool_revision != source["toolRevision"]:
            return "GRASP_TOOL_CHANGED"
        if frame is not None:
            if frame.robot_state.get("perception", {}).get("calibration_revision") != source["calibrationRevision"]:
                return "GRASP_CALIBRATION_CHANGED"
            if frame.reconstruction.get("robotId") != source["robotId"]:
                return "GRASP_ROBOT_CHANGED"
            capture_base = pose_matrix(frame.robot_state["base_pose"])
            if not np.isfinite(capture_base).all() or np.linalg.norm(capture_base-np.asarray(source["base"])) > .03:
                return "GRASP_BASE_CHANGED"
        return ""

    def _pick_cancel(self, command, plan, *, clearance=None):
        controller = self
        class GuardedCancellation:
            reported = False

            def is_set(self):
                reason = controller._pick_guard(command, plan)
                reading = {}
                if not reason and clearance is not None:
                    try:
                        state = controller.state()
                        reason, _ = controller._payload_clearance_error(plan, state, *clearance)
                        if reason:
                            reading = controller._clearance_reading(plan, state, *clearance)
                    except (ValueError, KeyError):
                        reason = "PAYLOAD_GEOMETRY_INVALID"
                if reason and not self.reported:
                    controller._phase("motion_guard", rejectReason=reason, **reading)
                    self.reported = True
                return bool(reason)
        return GuardedCancellation()

    def _clearance_reading(self, plan, state, rim_z, dimensions):
        """Diagnostic only: retain the actual rejecting tick, never authorize."""
        reading = {"sequence": str(state.get("sequence", "")),
                   "simTimeNs": str(state.get("simTimeNs", ""))}
        if np.isfinite(rim_z):
            reading["requiredBottomZ"] = rim_z+.025
        try:
            transform = self.node.runtime.base_pose @ np.linalg.inv(pose_matrix(state["robotPose"]))
            payload = transform @ pose_matrix(state["objects"][OBJECT_MODELS[plan["object"]]])
            if not np.isfinite(transform).all() or not np.isfinite(payload).all():
                raise ValueError("PAYLOAD_GEOMETRY_INVALID")
            reading.update(payloadPosition=payload[:3, 3].tolist(), uprightRzz=float(payload[2, 2]),
                           bottomZ=float(payload[2, 3]-payload_vertical_extent(payload[:3, :3], dimensions)))
        except (ValueError, TypeError, KeyError, np.linalg.LinAlgError):
            reading["geometryUnavailable"] = True
        return reading

    def _payload_clearance_error(self, plan, state, rim_z, dimensions, *, required_clearance=.025):
        if not np.isfinite(rim_z) or not np.isfinite(required_clearance) or required_clearance < .025:
            return "PAYLOAD_GEOMETRY_INVALID", None
        if (not state.get("attached") or state.get("held") != OBJECT_MODELS[plan["object"]]
                or state.get("side") != self.side):
            return "PAYLOAD_IDENTITY_CHANGED", None
        try:
            transform = self.node.runtime.base_pose @ np.linalg.inv(pose_matrix(state["robotPose"]))
            payload = transform @ pose_matrix(state["objects"][OBJECT_MODELS[plan["object"]]])
        except (ValueError, TypeError, KeyError, np.linalg.LinAlgError):
            return "PAYLOAD_GEOMETRY_INVALID", None
        if not np.isfinite(transform).all() or not np.isfinite(payload).all():
            return "PAYLOAD_GEOMETRY_INVALID", None
        if payload[2, 2] < math.cos(.15):
            return "PAYLOAD_ORIENTATION_NOT_SAFE", None
        bottom = payload[2, 3]-payload_vertical_extent(payload[:3, :3], dimensions)
        if bottom < rim_z+required_clearance:
            return "PAYLOAD_CLEARANCE_NOT_VERIFIED", None
        return "", payload[:3, 3]

    def _verify_payload_clearance(self, command, plan, rim_z, dimensions, *, required_clearance=.025):
        if not np.isfinite(rim_z) or not np.isfinite(required_clearance) or required_clearance < .025:
            self._phase("payload_clearance_stopped", rejectReason="PAYLOAD_GEOMETRY_INVALID")
            return Result(False, "PAYLOAD_GEOMETRY_INVALID")
        deadline, last_sequence, last_stamp, samples = time.monotonic()+3., 0, 0, []
        readings, reasons, last_reading = [], {}, None
        while time.monotonic() < deadline:
            reason = self._pick_guard(command, plan)
            if reason:
                self._phase("payload_clearance_stopped", rejectReason=reason)
                return Result(False, reason)
            state = self.state()
            if time.monotonic() >= deadline:
                self._phase("payload_clearance_stopped", rejectReason="PAYLOAD_CLEARANCE_WAIT_EXPIRED")
                return Result(False, "PAYLOAD_CLEARANCE_NOT_VERIFIED")
            reason = self._pick_guard(command, plan)
            if reason:
                self._phase("payload_clearance_stopped", rejectReason=reason)
                return Result(False, reason)
            sequence, stamp = state["sequence"], state["simTimeNs"]
            if sequence > last_sequence and stamp > last_stamp:
                last_sequence, last_stamp = sequence, stamp
                reason, position = self._payload_clearance_error(plan, state, rim_z, dimensions,
                                                                required_clearance=required_clearance)
                last_reading = {"sequence": str(sequence), "simTimeNs": str(stamp),
                                "rejectReason": reason}
                try:
                    transform = self.node.runtime.base_pose @ np.linalg.inv(pose_matrix(state["robotPose"]))
                    payload = transform @ pose_matrix(state["objects"][OBJECT_MODELS[plan["object"]]])
                    if not np.isfinite(transform).all() or not np.isfinite(payload).all():
                        raise ValueError("PAYLOAD_GEOMETRY_INVALID")
                    last_reading.update(payloadPosition=payload[:3, 3].tolist(),
                        uprightRzz=float(payload[2, 2]), motionBase=self.node.runtime.base_pose.tolist(),
                        robotPose=state["robotPose"], worldPayloadPose=state["objects"][OBJECT_MODELS[plan["object"]]],
                        bottomZ=float(payload[2, 3]-payload_vertical_extent(payload[:3, :3], dimensions)))
                except (ValueError, TypeError, KeyError, np.linalg.LinAlgError):
                    last_reading["geometryUnavailable"] = True
                reasons[reason or "accepted"] = reasons.get(reason or "accepted", 0)+1
                if len(readings) < 12:
                    readings.append(last_reading)
                if reason:
                    samples = []
                elif samples and np.linalg.norm(position-samples[-1][1]) > .004:
                    samples = [(stamp, position, sequence)]
                else:
                    samples.append((stamp, position, sequence))
                if len(samples) >= 3 and samples[-1][0]-samples[0][0] >= 200_000_000:
                    self._phase("payload_clearance_verified", rimZ=rim_z,
                                requiredBottomZ=rim_z+required_clearance, sampleCount=len(samples),
                                stableDurationSeconds=(samples[-1][0]-samples[0][0])/1e9,
                                evidenceSource="gazebo_physics",
                                samples=[{"simTimeNs": str(ns), "sequence": str(seq),
                                          "payloadPosition": value.tolist()} for ns, value, seq in samples])
                    return Result(True)
            elif sequence != last_sequence or stamp != last_stamp:
                # Repeated identical feedback is no new proof. An inconsistent
                # or backward sample invalidates the accumulated window.
                samples = []
            time.sleep(.025)
        self._phase("payload_clearance_stopped", rejectReason="PAYLOAD_CLEARANCE_NOT_VERIFIED",
                    rimZ=rim_z, requiredBottomZ=rim_z+required_clearance, reasonCounts=reasons,
                    initialReadings=readings, lastReading=last_reading, remainingStableSamples=len(samples))
        return Result(False, "PAYLOAD_CLEARANCE_NOT_VERIFIED")

    def _associate_target(self, plan, frame, entities):
        source = json.loads(plan["sourceJSON"])
        entity = entities.get(plan["object"])
        detail = {"captureId": frame.observation_id,
                  "capturedAtUnixMs": str(frame.wall_time_unix_ms),
                  "sensorStampNs": str(frame.robot_state.get("perception", {}).get("sensor_stamp_ns", "")),
                  "planAgeSeconds": time.monotonic()-plan["created"]}
        if entity is None:
            return None, {**detail, "rejectReason": "ORIGINAL_TARGET_NOT_VISIBLE"}
        position = np.asarray(entity.pose_xyz_quat[:3], dtype=float)
        drift = float(np.linalg.norm(position-plan["position"]))
        detail.update(position=position.tolist(), category=entity.category, anchorDriftM=drift)
        # Household metric-shape detections have an existing 0.90 floor.
        # A previously higher confidence is not a new relative threshold.
        if (entity.category != source["category"]
                or entity.attributes.get("recognition", "") != source["recognition"]
                or not np.isfinite(entity.confidence) or entity.confidence < .9):
            return None, {**detail, "rejectReason": "ORIGINAL_TARGET_KIND_MISMATCH"}
        if position.shape != (3,) or not np.isfinite(position).all() or not np.isfinite(drift) or drift > .04:
            return None, {**detail, "rejectReason": "ORIGINAL_TARGET_POSITION_MISMATCH"}
        return position, {**detail, "associated": True}

    def _observe_grasp_views(self, command, plan):
        self._bind_source(plan)
        for ordinal, view in enumerate(plan["views"]):
            index = plan["viewIndices"][ordinal]
            reason = self._pick_guard(command, plan)
            if reason:
                self._phase("view_guard", candidate=index, rejectReason=reason)
                return None, Result(False, "GRASP_PLAN_STALE" if reason.startswith("GRASP_") else reason)
            if self.state()["attached"]:
                return None, Result(False, "GRIPPER_OCCUPIED")
            joints, age, _ = self.node.joint_snapshot()
            if not 0 <= age <= .5:
                return None, Result(False, "JOINT_FEEDBACK_STALE")
            chunk, _ = plan_empty_arm_approach(view, joints, self.node.runtime.base_pose.copy(),
                upright=plan["upright"], side=plan["side"], tool=self.tool)
            # Planning itself may consume the remaining budget.
            reason = self._pick_guard(command, plan)
            if reason:
                self._phase("dispatch_guard", candidate=index, rejectReason=reason)
                return None, Result(False, "GRASP_PLAN_STALE" if reason.startswith("GRASP_") else reason)
            self._evidence.value["physicalStarted"] = True
            self._phase("empty_arm_view", candidate=index, overheadPosition=(view+[0., 0., .16]).tolist())
            motion = execute_chunk(self.node, chunk, self._pick_cancel(command, plan))
            if not motion.success:
                self._phase("view_stopped", candidate=index,
                            rejectReason=self._pick_guard(command, plan) or motion.code)
                return None, motion
            completed_ns, completed_ms = time.monotonic_ns(), int(time.time()*1000)
            if not self.backend.wait_command_capture(command, completed_ns, completed_ms):
                return None, Result(False, "POSTCONDITION_OBSERVATION_TIMEOUT")
            frame, visible = self.capture()
            reason = self._pick_guard(command, plan, frame)
            if reason:
                self._phase("capture_guard", candidate=index, rejectReason=reason)
                return None, Result(False, "GRASP_PLAN_STALE" if reason.startswith("GRASP_") else reason)
            stamp = str(frame.robot_state.get("perception", {}).get("sensor_stamp_ns", ""))
            source = json.loads(plan["sourceJSON"])
            if (frame.wall_time_unix_ms <= completed_ms or not stamp.isdigit()
                    or int(stamp) <= int(source["sensorStampNs"])):
                return None, Result(False, "POSTCONDITION_OBSERVATION_TIMEOUT")
            position, detail = self._associate_target(plan, frame, visible)
            self._phase("view_capture", candidate=index, **detail)
            if position is not None:
                return position, None
        return None, Result(False, "GRASP_PLAN_STALE")

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
                created_ns = time.monotonic_ns()
                source_json = source_token = None
                if self.backend.home:
                    source = self._plan_source(command, frame, entities[obj], base, created_ns, entities[dest])
                    source_json = json.dumps(source, sort_keys=True, separators=(",", ":"))
                    source_token = hashlib.sha256(source_json.encode()).hexdigest()
                    self._bind_source({"sourceJSON": source_json, "sourceToken": source_token})
                    self._phase("preflight_started")
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
                    except ValueError as error:
                        self._phase("arm_rejected", side=side, stage="contact_endpoint", rejectReason=str(error))
                        continue
                    views, view_indices = [], []
                    if self.tool is not None:
                        camera_origin = None
                        if isinstance(self.backend.home, dict):
                            camera = frame.robot_state.get("perception", {}).get("camera", "head-rgbd")
                            mount = np.asarray(self.backend.home["cameras"][camera]["baseFromLink"])
                            camera_origin = (base @ mount)[:3, 3]
                        proposed_views = grasp_view_positions(np.array(entities[obj].pose_xyz_quat[:3]), base, camera_origin)
                        approach_joints = dict(joints)
                        for index, view in enumerate(proposed_views):
                            stage = "empty_arm_approach"
                            try:
                                _, approach_end = plan_empty_arm_approach(view, approach_joints,
                                    base, upright=upright, side=side, tool=self.tool)
                                # A rejected candidate never changes the next
                                # candidate's starting posture. Only accepted
                                # observation poses form the executable chain.
                                view_joints = {**approach_joints, **approach_end}
                                destination = np.array(entities[dest].pose_xyz_quat[:3])
                                offset = np.array([0., 0., self.tool["contactHeightM"]])
                                stage = "descent"
                                _, preflight = plan_tip_chunk(goal, view_joints, base,
                                    upright=upright, side=side, fixed_gripper=self.tool["gripperOpenRad"], tool=self.tool)
                                loaded_views = [(base @ np.r_[point, 1.])[:3]
                                                for point in self.tool['observationWaypointsBaseM'][side]]
                                lifts = loaded_lift_waypoints(goal, np.array(entities[obj].pose_xyz_quat[:3]), base,
                                    rim_z=destination[2]+HOUSEHOLD_TRAY_RIM_HEIGHT_M,
                                    dimensions=HOUSEHOLD_DIMENSIONS[obj])
                                for segment, point in enumerate([*lifts,
                                              *loaded_views, destination+offset+[0., 0., .185],
                                              destination+offset+[0., 0., .075]]):
                                    stage = f"loaded_segment_{segment}"
                                    if segment == 1:
                                        _, preflight = plan_loaded_retract_chunk(point, preflight, base,
                                            upright=upright, side=side, tool=self.tool)
                                    else:
                                        _, preflight = plan_tip_chunk(point, preflight, base,
                                            upright=upright, side=side, fixed_gripper=self.tool["gripperOpenRad"], tool=self.tool)
                            except ValueError as error:
                                self._phase("view_rejected", side=side, candidate=index,
                                            stage=stage, rejectReason=str(error), position=view.tolist())
                                continue
                            views.append(view)
                            view_indices.append(index)
                            approach_joints = view_joints
                            self._phase("view_preflight_complete", side=side, candidate=index, position=view.tolist())
                        if not views:
                            self._phase("arm_rejected", side=side, stage="observation_views",
                                        rejectReason="NO_REACHABLE_GRASP_VIEW")
                            continue
                    cost = sum((value-joints[motor])**2 for motor,value in target_joints.items())
                    candidates.append((cost,side,upright,views,view_indices))
                if not candidates:
                    raise ValueError("GRASP_TARGET_UNREACHABLE")
                _,self.side,upright,views,view_indices = min(candidates,key=lambda candidate:candidate[0])
                plan = {"task": command.task_id, "object": obj, "destination": dest,
                             "position": np.array(entities[obj].pose_xyz_quat[:3]), "base": base,
                             "created": created_ns/1e9, "observation": frame.observation_id,
                             "upright": upright, "side": self.side, "views": views,
                             "viewIndices": view_indices}
                if self.backend.home:
                    plan["sourceJSON"] = source_json
                    plan["sourceToken"] = source_token
                    plan["position"].setflags(write=False)
                    plan["base"].setflags(write=False)
                    for view in views:
                        view.setflags(write=False)
                    self._bind_source(plan)
                    self._phase("plan_created", candidateCount=len(views), retainedViewIndices=view_indices)
                self.plan = plan
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
            position = np.array(entities[obj].pose_xyz_quat[:3]) if obj in entities else None
            if not self.backend.home:
                if time.monotonic()-self.plan["created"] > 30 or position is None:
                    return Result(False, "GRASP_PLAN_STALE")
                if np.linalg.norm(position-self.plan["position"]) > .04 or np.linalg.norm(
                        self.node.runtime.base_pose-self.plan["base"]) > .03:
                    return Result(False, "GRASP_PLAN_STALE")
            if self.state()["attached"]:
                return Result(False, "GRIPPER_OCCUPIED")
            if self.backend.home:
                plan = self.plan
                self._bind_source(plan)
                reason = self._pick_guard(command, plan, frame)
                if reason:
                    self._phase("initial_guard", rejectReason=reason)
                    return Result(False, "GRASP_PLAN_STALE" if reason.startswith("GRASP_") else reason)
                _initial, detail = self._associate_target(plan, frame, entities)
                self._phase("initial_capture", **detail)
                if _initial is None:
                    return Result(False, "GRASP_PLAN_STALE")
                refreshed, failure = self._observe_grasp_views(command, plan)
                if failure is not None:
                    return failure
            else:
                # Preserve the prototype workcell's rise-before-traverse path.
                current = self.tip_in_odom(self.state())
                overhead = position+[0.,0.,.16]
                approach = []
                if current[2] < overhead[2]:
                    raised = current.copy()
                    raised[2] = overhead[2]
                    approach.append((raised,None))
                approach.append((overhead,None))
                approach.append((overhead,self.plan["upright"]))
                for point, upright in approach:
                    motion = self.move_tip(point, upright=upright)
                    if not motion.success:
                        return motion
            if not self.backend.home:
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
            else:
                reason = self._pick_guard(command, plan)
                if reason:
                    self._phase("descent_guard", rejectReason=reason)
                    return Result(False, "GRASP_PLAN_STALE" if reason.startswith("GRASP_") else reason)
                self._phase("descent", associatedPosition=refreshed.tolist())
            # One bounded final descent from this fresh measured target. The
            # acquired pose deliberately occludes the mug, so demanding a
            # second visual detection there prevents any grasp. The near-tool
            # interlock and independent lift verification still gate success.
            motion = self.move_tip(refreshed+[0., 0., self.tool["contactHeightM"] if self.tool else .082],
                                   upright=self.plan["upright"],
                                   cancel=self._pick_cancel(command, plan) if self.backend.home else None)
            if not motion.success:
                return motion
            if self.backend.home:
                reason = self._pick_guard(command, plan)
                if reason:
                    self._phase("attach_guard", rejectReason=reason)
                    return Result(False, "GRASP_PLAN_STALE" if reason.startswith("GRASP_") else reason)
                self._phase("attach", associatedPosition=refreshed.tolist())
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
            # A held object inside a tray must clear its complete rim before
            # any lateral retract. Preflight and execution share this path.
            if self.backend.home:
                source = json.loads(plan["sourceJSON"])
                rim_z = source["destinationPosition"][2]+HOUSEHOLD_TRAY_RIM_HEIGHT_M
                dimensions = HOUSEHOLD_DIMENSIONS[obj]
                transform = self.node.runtime.base_pose @ np.linalg.inv(pose_matrix(state["robotPose"]))
                payload_position = (transform @ np.r_[state["objects"][OBJECT_MODELS[obj]][:3], 1.])[:3]
                lifts = loaded_lift_waypoints(current, payload_position, self.node.runtime.base_pose,
                                             rim_z=rim_z, dimensions=dimensions)
                self._phase("lift_vertical", targetPosition=lifts[0].tolist(), rimZ=rim_z)
                result = self.move_tip(lifts[0], upright=self.acquisition["upright"],
                                       cancel=self._pick_cancel(command, plan))
                if not result.success:
                    return result
                result = self._verify_payload_clearance(command, plan, rim_z, dimensions, required_clearance=.045)
                if not result.success:
                    return result
                # Replan XY from current feedback without compounding a
                # Cartesian height deficit or lowering a higher actual tip.
                retracted = loaded_retract_target(self.tip_in_odom(self.state()), lifts)
                self._phase("lift_retract", targetPosition=retracted.tolist())
                result = self.move_tip(retracted, upright=self.acquisition["upright"],
                    cancel=self._pick_cancel(command, plan, clearance=(rim_z, dimensions)),
                    loaded_retract=True)
                if not result.success:
                    return result
            else:
                lift = self.node.runtime.base_pose[:3, :3] @ [-.04, 0., .10]
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

    def move_tip(self, target, *, upright=None, relative=False, tool_frame=None, cancel=None, loaded_retract=False):
        cancel = self.backend.cancel_event if cancel is None else cancel
        if cancel.is_set():
            return Result(False, "CANCELLED")
        joints, age, _ = self.node.joint_snapshot()
        if age > .5:
            return Result(False, "JOINT_FEEDBACK_STALE")
        base = self.node.runtime.base_pose.copy()
        if relative:
            target = (base @ np.r_[target, 1.])[:3]
        active_tool = self.tool if tool_frame is None else tool_frame
        try:
            if loaded_retract:
                chunk, _ = plan_loaded_retract_chunk(target, joints, base,
                    upright=upright, side=self.side, tool=active_tool)
            else:
                chunk, _ = plan_tip_chunk(target, joints, base, upright=upright, side=self.side,
                    fixed_gripper=self.tool["gripperOpenRad"] if self.tool else None, tool=active_tool)
        except ValueError as error:
            if str(error) != "WORKCELL_MOTION_LIMIT":
                raise
            return Result(False, "WORKCELL_MOTION_LIMIT")
        return execute_chunk(self.node, chunk, cancel, coordinated=True) if loaded_retract else execute_chunk(self.node, chunk, cancel)

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
