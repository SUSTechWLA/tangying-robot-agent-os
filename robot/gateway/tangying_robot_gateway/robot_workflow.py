"""Calibration and mapping lifecycle behind runtime-registered services."""
from __future__ import annotations

import copy
import itertools
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from .dense_slam import DenseSLAM, compose, pose_se2, relative, transform
from .exploration import (
    OCCUPIED,
    Grid,
    clearance_mask,
    coverage_report,
    explore_target,
    frontier_mask,
    next_waypoint,
    plan_route,
    still_open,
    traversable_for,
)
from .map_catalog import MapCatalog
from .map_pipeline import PointCloud, build_map, decode_lod, occupancy_from_points
from .navigation_map import merge_grids, read_nav2_grid
from .object_memory import ObjectMemory
from .service_registry import RegisteredService, ServiceError, object_schema

#: How an automatic survey decides where to look next. These are policy, not
#: safety: every step the loop takes still passes the same admission checks an
#: operator's step does, so a wrong number here costs travel rather than contact.
EXPLORATION = {
    #: How far the base RGB-D camera is assumed to settle the map ahead of it.
    "sensorRadiusM": 3.0,
    #: Frames the survey may skip because too little depth was measured, before
    #: the leg ends and publishes what it has. One blank wall is not news; a run
    #: of them means the robot is staring at something it cannot measure.
    "depthStarvedLimit": 25,
    #: Radius used to ask "is this corner still unseen?" before spending turns.
    "lookRadiusM": 3.0,
    #: Above this local unknown fraction, standing still and turning pays off.
    "lookThreshold": 0.25,
    #: Frontier clusters smaller than this are corners of known rooms, not rooms.
    "minFrontierCells": 8,
    #: One bounded step per re-plan: the drive changes the map it was planned on.
    "lookaheadM": 0.6,
    "maxStepM": 0.5,
    #: Stop a leg before the 400-frame session budget so it can still be saved
    #: and continued instead of failing at the cap with the leg unpublished.
    "frameMargin": 45,
    "legSeconds": 900.0,
    "headingToleranceRad": 0.10,
    #: Close enough to the chosen viewpoint to be standing at the unknown edge.
    "arrivalM": 0.7,
    #: Turning is not free: a quarter turn is three bounded commands and a
    #: dozen keyframes, so look around only after covering some ground.
    "lookSpacingM": 1.2,
    #: Floor this close to where the robot has already driven is inside its own
    #: camera blind spot: it will never be measured, so it is not a frontier.
    "blindRadiusM": 1.0,
    #: How far past the chassis the robot's own drivable footprint is trusted.
    #: It is standing there without contact, so the space is provably free.
    "selfRadiusMarginM": 0.30,
    #: Planning keeps this much more than the driver's own envelope. It has to be
    #: small, and it has to match the radius the travelled trail was certified
    #: with: a planner more cautious than the certification turns the corridor it
    #: just drove down into no-go space. Measured on a finished house map, a 6 cm
    #: margin cut the drivable cells from 6,909 to 4,149 and left the robot's own
    #: starting cell unplannable, which is what "no reachable frontier" meant.
    #:
    #: This margin is the only difference between the planner's clearance and the
    #: robot's own footprint, and both the trail certification and the driver's
    #: guard are measured against it: a plan, a proof and a motor guard are about
    #: one number, or the robot is told to go somewhere nothing certified.
    "planningMarginM": 0.0,
    #: Consecutive refusals before the leg gives up and reports where it stopped.
    "maxConsecutiveRefusals": 6,
    #: Steps that neither moved nor were refused before a leg reports no progress.
    "maxIdleSteps": 12,
    "lookSweepsMax": 4,
    "lookSweepsMin": 2,
    "lookDenseThreshold": 0.45,
    #: Off by default. Turning in place is the one motion the depth ICP has the
    #: least to work with - consecutive frames of the same wall from the same
    #: spot - and a long survey that stops to spin at every waypoint accumulates
    #: yaw error until the published walls are no longer axis aligned. Driving
    #: sweeps the camera through the same angles with parallax to register on.
    "maxLooksPerLeg": 0,
}
#: Turn spread of one look-around, chosen to overlap the base camera's 73 deg FOV.
LOOK_AROUND_STEP_RAD = 1.5707963267948966
DEFAULT_EXPLORE_LEGS = 4
DEFAULT_EXPLORE_TRAVEL_M = 75.0


class RobotWorkflow:
    """The provider supplies acquisition, commissioned motion and calibration.

    Reservation must atomically exclude ordinary robot commands; service calls
    that move use the very same safety supervisor as normal tool execution.
    """
    def __init__(self, *, robot_id, root, calibration_get, calibration_run,
                 calibration_save, capture, move, reserve, release,
                 survey_goals, semantic_workspaces, footprint_radius=.30, world_frame_revision=None,
                 clearance_validator=None, entity_source=None):
        self.robot_id = robot_id
        self.root = Path(root).resolve()
        self.calibration_get, self.calibration_run, self.calibration_save = calibration_get, calibration_run, calibration_save
        self.capture, self.move, self.reserve, self.release = capture, move, reserve, release
        self.survey_goals, self.semantic_workspaces = survey_goals, semantic_workspaces
        self.footprint_radius = footprint_radius
        self.clearance_validator = clearance_validator
        # Where "objects the robot has actually seen" come from. Drivers that can
        # report perceived entities inject them here; a survey then leaves a
        # semantic object layer inside the map instead of only a point cloud.
        self.entity_source = entity_source
        self.object_memory = ObjectMemory()
        self._last_object_poll_ms = 0
        self._object_errors: list[str] = []
        #: Frames skipped because too little depth was measured to register them.
        self._depth_starved = 0
        # Drivers with restart-unstable odometry must use a new frame epoch and
        # register an explicit relocalization service before reusing old maps.
        self.world_frame_revision = world_frame_revision or uuid.uuid4().hex
        self._lock = threading.RLock()
        self._slam_lock = threading.Lock()
        self._cancel = threading.Event()
        self._worker = None
        self._reservation = None
        self._pause_requested = False
        self._user_cancel = False
        self.slam = DenseSLAM()
        self.state = "idle"
        self.message = "开始扫描，或选择已保存地图。"
        self.session_id = ""
        self.map_id = ""
        self.active = None
        self.grid = None
        self.map_from_world = np.zeros(3)
        self._preview = {"points": [], "colors": []}
        self._summary = {"frameCount":0,"pointCount":0,"travelledM":0.,"registrationCount":0,"loopClosures":0,"trajectory":[]}
        # Frontier state of the running automatic survey, reported through
        # `mapping.status` so an operator can see why it is still driving.
        self._exploration = {}
        self._explore_complete = False
        self._leg_anchor = None
        # Set for real by `start`; declared here so every helper can ask about
        # them without depending on which entry point was used.
        self._base_anchor = None
        self._base_map_id = ""
        # The base map is immutable for the length of a leg, and reading it back
        # on every planning step would put a file decode in the control loop.
        self._base_grid_cache = None
        self._restore_active()

    def register(self, registry):
        number = lambda maximum: {"type":"number","minimum":.01,"maximum":maximum}
        entries = [
            ("calibration.get","读取机器人标定与编辑模板",object_schema(),lambda _:self.calibration_get(),False),
            ("calibration.run","运行机器人注册的标定算法",object_schema(),self.run_calibration,True),
            ("calibration.save","验证并应用自行标定结果",object_schema({"document":{"type":"object","additionalProperties":True},"expectedRevision":{"type":"string"},"algorithm":{"type":"string"}},["document","expectedRevision"]),self.save_calibration,True),
            ("mapping.status","读取扫描进度和当前地图",object_schema(),lambda _:self.status(),False),
            ("mapping.start","开始机器人移动与 RGB-D SLAM：手动、按注册路线巡检，或自动探索未知区域；给出 baseMapId 时从该地图的坐标系继续扩展",object_schema({"name":{"type":"string"},"mode":{"type":"string","enum":["manual","survey","explore"]},"baseMapId":{"type":"string"},"maxTravelM":{"type":"number","minimum":2.,"maximum":120.},"maxLegs":{"type":"number","minimum":1,"maximum":6}}),self.start,True),
            ("mapping.move","执行有界扫描移动",object_schema({"action":{"type":"string","enum":["forward","backward","left","right","turn_left","turn_right"]},"distanceM":number(.5),"angleRad":number(.5)},["action"]),self.move_step,True),
            ("mapping.stop_motion","停止当前扫描移动",object_schema(),self.stop_motion,True),
            ("mapping.finish","优化并保存扫描地图",object_schema(),self.finish,True),
            ("mapping.cancel","取消本次扫描",object_schema(),self.cancel,True),
            ("mapping.activate","验证并加载保存的机器人地图",object_schema({"mapId":{"type":"string"}},["mapId"]),self.activate,True),
            ("navigation.map","读取当前导航地图与定位",object_schema(),lambda _:self.navigation_map(),False),
        ]
        for name,description,schema,handler,mutation in entries:
            registry.register(RegisteredService(name,description,schema,handler,mutation))

    def run_calibration(self, _):
        token = self.reserve()
        try:
            result = self.calibration_run()
            self._invalidate_calibration(result["revision"])
            return result
        finally:
            try:self._refresh_calibration()
            finally:self.release(token)

    def save_calibration(self, parameters):
        token = self.reserve()
        try:
            if not parameters["expectedRevision"]:
                raise ServiceError("REVISION_REQUIRED","先读取当前标定，再保存结果。")
            algorithm = parameters.get("algorithm", "user supplied")
            if len(algorithm) > 500:
                raise ServiceError("INVALID_ARGUMENT","算法说明请限制在 500 字以内。")
            result = self.calibration_save(parameters["document"],parameters["expectedRevision"],algorithm)
            self._invalidate_calibration(result["revision"])
            return result
        finally:
            try:self._refresh_calibration()
            finally:self.release(token)

    def _refresh_calibration(self):
        # A provider can commit before a later snapshot/provenance write fails.
        # Reconcile even an error response; unknown calibration invalidates maps.
        try:revision=self.calibration_get()["revision"]
        except Exception:revision=None  # noqa: BLE001 - fail closed if provider state is uncertain.
        self._invalidate_calibration(revision)

    def _invalidate_calibration(self, revision):
        with self._lock:
            if self.active and self.active["calibrationRevision"] != revision:
                self.active,self.grid = None,None
                (self.root/"active-map.json").unlink(missing_ok=True)

    def status(self):
        with self._lock:
            return copy.deepcopy({"state":self.state,"sessionId":self.session_id,"mapId":self.map_id,
                "message":self.message,"activeMap":self.active,"preview":self._preview,
                "exploration":self._exploration,**self._summary})

    def _spawn(self, callback):
        def run():
            try:
                callback()
            except Exception as error:  # noqa: BLE001 - provider fault terminates the owned workflow.
                with self._lock:
                    self.state = ("cancelled" if self._user_cancel else "recording" if self._pause_requested else "failed")
                    self.message = str(error)
                if self.state != "recording":
                    self._release_session()
        self._worker = threading.Thread(target=run,name="robot-mapping",daemon=True)
        try:self._worker.start()
        except Exception:
            self._worker=None
            self.state,self.message="failed","无法启动采集任务，请重试。"
            self._release_session()
            raise

    def start(self, parameters):
        with self._lock:
            if self.state in {"recording","moving","finalizing"}:
                raise ServiceError("MAPPING_ACTIVE","已有扫描进行中，请先完成或取消。")
            self._reservation = self.reserve()
            try:self.calibration_revision = self.calibration_get()["revision"]
            except Exception:
                self._release_session()
                raise
            self._cancel = threading.Event()
            self._pause_requested,self._user_cancel = False,False
            # Resolve the continuation anchor before the robot moves. Doing it here
            # rather than at save time means a wrong base map is reported while the
            # operator is still standing there, not after a survey has been driven.
            self._base_anchor = None
            self._base_map_id = ""
            if parameters.get("baseMapId"):
                self._base_map_id = str(parameters["baseMapId"])
                self._base_anchor = self._continuation_anchor(self._base_map_id)
            self.slam = DenseSLAM()
            self.session_id = uuid.uuid4().hex
            self.map_id = "scan-"+self.session_id[:12]
            self.name = str(parameters.get("name", "家庭地图"))[:120]
            self._exploration = {}
            self._explore_complete = False
            self._leg_anchor = None
            self.state,self.message = "moving","正在采集第一帧。"
            self._preview = {"points":[],"colors":[]}
            self._summary = {"frameCount":0,"pointCount":0,"travelledM":0.,"registrationCount":0,"loopClosures":0,"trajectory":[]}
            self._spawn(lambda:self._begin(parameters.get("mode","manual"),parameters))
        return self.status()

    def _begin(self, mode, parameters=None):
        parameters = parameters or {}
        self._sample()
        if mode == "survey":
            goals = self.survey_goals()
            if not goals:
                raise ServiceError("SURVEY_UNAVAILABLE","机器人未注册自动扫描路线，请选择手动扫描。")
            for i,goal in enumerate(goals):
                self._check_cancel()
                with self._lock: self.message = f"正在扫描路线 {i+1} / {len(goals)}，持续采集 RGB-D。"
                self._move_and_sample(goal, bounded=False)
            with self._lock: self.state = "finalizing"
            self._build()
        elif mode == "explore":
            self._explore_legs(parameters)
        else:
            self._check_cancel()
            with self._lock:
                self._check_cancel()
                self.state,self.message = "recording","已开始采集，可使用方向按钮移动机器人。"

    def _check_cancel(self):
        if self._cancel.is_set():
            raise ServiceError("CANCELLED","扫描移动已停止。")

    #: How often a survey asks the driver for perceived entities. Perception is
    #: heavier than a capture, and an object does not move between two keyframes,
    #: so a one-second cadence costs nothing in map quality.
    OBJECT_POLL_INTERVAL_MS = 1000

    def _observe_objects(self, observation, odometry=None):
        """Fold one capture's perceived entities into the map's object memory.

        A driver that cannot report entities leaves this a no-op rather than an
        error: the semantic object layer is additive evidence, and a survey must
        not fail because a robot has no detector commissioned. A detector fault is
        likewise local news - it is recorded on the session, not raised into the
        motion loop, where it would abandon a survey that is still driving well.
        """
        if self.entity_source is None:
            return
        stamp = int(observation.wall_time_unix_ms)
        if stamp - self._last_object_poll_ms < self.OBJECT_POLL_INTERVAL_MS:
            return
        self._last_object_poll_ms = stamp
        try:
            view = self.entity_source()
        except Exception as error:  # noqa: BLE001 - a detector fault is not a survey fault.
            self._object_errors.append(f"{type(error).__name__}: {error}")
            del self._object_errors[:-4]
            return
        entities = list(getattr(view, "entities", ()) or ())
        if not entities:
            # A poll that saw nothing is still a poll: the published layer has to
            # be able to say "perception looked here and reported no object".
            self.object_memory.polls += 1
            return
        # The vantage comes from the frame this sighting belongs to. The mapping
        # capture is a raw sensor observation and deliberately carries no
        # semantic state, but its odometry is the measured base pose - planar,
        # in the driver's world frame - which is exactly what a later task needs
        # to drive back to where the object was visible.
        base_pose = None
        try:
            base_pose = [float(value) for value in odometry] if odometry is not None else None
        except (TypeError, ValueError):
            base_pose = None
        try:
            self.object_memory.observe(
                entities, map_from_world=self._planning_anchor(), stamp_unix_ms=stamp,
                evidence_frame_id="map",
                source_id=str(getattr(view, "source_id", "") or ""),
                base_pose=base_pose)
        except ValueError as error:
            self._object_errors.append(str(error))
            del self._object_errors[:-4]

    def _sample(self):
        self._check_cancel()
        observation = self.capture()
        self._check_cancel()
        age = int(time.time()*1000)-observation.wall_time_unix_ms
        if not 0 <= age <= 2000:
            raise ServiceError("STALE_CAPTURE","RGB-D 与底盘位姿已过期，停止扫描。")
        if self.calibration_get()["revision"] != self.calibration_revision:
            raise ServiceError("CALIBRATION_CHANGED","标定发生变化，请重新扫描。")
        with self._slam_lock:
            try:
                added = self.slam.add(observation)
            except ValueError as error:
                # A view with too few measured depth points is a local sensor
                # condition - a blank wall at arm's length, a dark corner - not a
                # reason to throw away a survey that is most of the way through a
                # house. Measured: a whole-house leg died at 74% mapped on exactly
                # this, and the map was never published. The frame is skipped, the
                # survey keeps driving (the view changes as it moves), and a leg
                # that keeps hitting it ends gracefully instead.
                if "深度点不足" not in str(error):
                    raise
                self._depth_starved += 1
                return
            if not added: return
            self._observe_objects(observation, self.slam.frames[-1].odometry)
            frames = self.slam.frames
            trail = self.slam.trajectory()
            # Preview is bounded and regenerated only for accepted keyframes.
            points,colors = [],[]
            per_frame = max(1,2000//len(frames))
            for frame in frames:
                stride = max(1,len(frame.points)//per_frame)
                points.extend(transform(frame.points[::stride],frame.pose).tolist())
                colors.extend(frame.colors[::stride].tolist())
            summary = {"frameCount":len(frames),"pointCount":sum(len(f.points) for f in frames),
                       "registrationCount":len(self.slam.registrations),"loopClosures":len(self.slam.loops),
                       "travelledM":float(sum(np.linalg.norm(b.odometry[:2]-a.odometry[:2]) for a,b in itertools.pairwise(frames))),
                       "trajectory":trail}
        with self._lock:
            self._summary = summary
            self._preview = {"points":points[:3000],"colors":colors[:3000]}

    def _move_and_sample(self, goal, *, bounded, fatal=True):
        """Move under the ordinary safety admission while sampling the whole way.

        ``fatal`` decides what a refusal means. For an operator's or a survey's
        move it ends the scan, which is what the caller wants to hear. For
        exploration it is local news - this approach is blocked - and cancelling
        the session would abandon the rest of the house because of one doorway,
        so the explorer handles it and picks another target.
        """
        result = []
        def moving():
            try: result.append(self.move(goal,self._cancel,bounded=bounded))
            except Exception as error: result.append(error)  # noqa: BLE001 - preserve provider fault for controller stop/join.
        thread = threading.Thread(target=moving,name="mapping-motion",daemon=True)
        thread.start()
        try:
            while thread.is_alive():
                self._sample()
                thread.join(.20)
            self._check_cancel()
            if not result or isinstance(result[0],Exception):
                raise result[0] if result else RuntimeError("motion returned no receipt")
            if not result[0].get("ok"):
                raise ServiceError(result[0].get("code","MOTION_FAILED"),result[0].get("message","机器人移动失败。"))
            self._sample()
        except BaseException:
            if fatal:
                self._cancel.set()
            # Reservation remains held until the controller acknowledges stop.
            thread.join()
            raise

    def move_step(self, parameters):
        with self._lock:
            if self.state != "recording":
                raise ServiceError("SCAN_NOT_READY","请先开始手动扫描，并等待当前移动完成。")
            observation = self.capture()
            pose = list(observation.robot_state["base_pose"])
            se2 = pose_se2(pose)
            action = parameters["action"]
            distance = parameters.get("distanceM",.25)
            if action.startswith("turn_"):
                se2[2] += parameters.get("angleRad",.35)*(1 if action=="turn_left" else -1)
            else:
                direction = {"forward":0,"backward":math.pi,"left":math.pi/2,"right":-math.pi/2}[action]+se2[2]
                se2[:2] += distance*np.array([math.cos(direction),math.sin(direction)])
            goal = [float(se2[0]),float(se2[1]),pose[2],math.cos(se2[2]/2),0.,0.,math.sin(se2[2]/2)]
            self._cancel = threading.Event()
            self._pause_requested,self._user_cancel = False,False
            self.state,self.message = "moving","正在执行有界移动并采集深度。"
            self._spawn(lambda:self._manual_move(goal))
        return self.status()

    def _manual_move(self, goal):
        # A manual nudge is the operator's own bounded step: it travels exactly the
        # requested short translation and must never be re-routed through the house.
        self._move_and_sample(goal, bounded=True)
        with self._lock:
            self._check_cancel()
            self.state,self.message = "recording","移动完成，可继续扫描或保存地图。"

    # ------------------------------------------------------------------
    # Automatic exploration: drive at whatever is still unknown.
    # ------------------------------------------------------------------

    def _explore_legs(self, parameters):
        """Survey the house in legs, publishing each before the frame budget ends.

        One session cannot cover a whole home: the 400-frame cap is roughly 40 m
        of travel plus the turns, and a complete survey is longer than that. The
        alternative to stopping short is to publish what was measured and keep
        exploring from it, so the operator gets one finished map instead of an
        unfinished scan and a chore.
        """
        legs = max(1, int(parameters.get("maxLegs", DEFAULT_EXPLORE_LEGS)))
        budget = float(parameters.get("maxTravelM", DEFAULT_EXPLORE_TRAVEL_M))
        self._explore_legs_loop(legs, budget)

    def _explore_legs_loop(self, legs, budget):
        spent = 0.0
        for index in range(legs):
            with self._lock:
                self.state,self.message = "exploring",f"自动探索第 {index+1} 段：正在选择下一个未知区域。"
            spent += self._explore_leg(budget-spent,index+1)
            complete = self._explore_complete
            last = complete or index == legs-1 or budget-spent <= 1.
            with self._lock:
                self.state,self.message = "finalizing",(
                    "未知区域已探索完，正在生成地图。" if complete else
                    f"第 {index+1} 段扫描完成，正在保存后继续探索。")
            if self._summary["travelledM"] < .15:
                # Nothing was measured in this leg. Publishing would be refused
                # (and would add nothing), but "no unknown regions" is only true
                # if a previous leg already saved the map this one extends.
                with self._lock:
                    if self._base_map_id:
                        self.state,self.message = "completed","地图已保存；剩余未知区域当前不可达。"
                    else:
                        self.state,self.message = "failed",(
                            "自动探索未能开始：从当前位置找不到可通行的未知区域，"
                            "请把机器人放到更开阔的位置后重试。")
                self._release_session()
                return
            self._build()
            if last:
                return
            with self._lock:
                # The map is published but the run is not over, and saying
                # "completed" here makes every client believe it is: the CLI
                # stopped after leg one and reported success while the robot
                # went on exploring three more.
                self.state,self.message = "exploring",(
                    f"第 {index+1} 段已保存并启用，继续自动探索。")
            self._open_leg(index+2)

    def _open_leg(self, number):
        """Re-arm the session so the next leg extends the map just published."""
        self._reservation = self.reserve()
        self._base_map_id = self.map_id
        self._base_anchor = list(self._leg_anchor) if self._leg_anchor else None
        self.slam = DenseSLAM()
        self._base_grid_cache = None
        self.session_id = uuid.uuid4().hex
        self.map_id = "scan-"+self.session_id[:12]
        self._cancel = threading.Event()
        self._pause_requested,self._user_cancel = False,False
        with self._lock:
            self._summary = {"frameCount":0,"pointCount":0,"travelledM":0.,"registrationCount":0,
                             "loopClosures":0,"trajectory":[]}
            self._preview = {"points":[],"colors":[]}
            self.state,self.message = "exploring",f"自动探索第 {number} 段：继续从已保存地图扩展。"
        # Every leg needs its own first capture. Without it the leg starts with an
        # empty scan, plans on no grid at all, and reports that the house has
        # nothing left to explore.
        self._sample()

    def _explore_leg(self, remaining_m, number):
        """One session's worth of exploring; returns the distance it travelled."""
        started = self._summary["travelledM"]
        deadline = time.monotonic()+EXPLORATION["legSeconds"]
        self._explore_complete = False
        # Viewpoints the driver refused. Retrying one would spin, and treating a
        # refusal as the end of the survey would be wrong: the obstacle is local,
        # so the planner is told to route around it and pick something else.
        refused = []
        consecutive = 0
        idle = 0
        looks = 0
        last_look = 0.0
        # Sticky target. Re-picking the best frontier on every step made the
        # robot walk to the middle of a room and oscillate between four equally
        # good corners; a target is held until it is reached or goes stale.
        target_xy = None
        self._look_around(*self._live_grid())
        while True:
            self._check_cancel()
            stopped = self._explore_stop_reason(started,remaining_m,deadline,number)
            if stopped:
                self._exploration["stopReason"] = stopped
                return self._summary["travelledM"]-started
            live,blind = self._live_grid()
            if live is None:
                self._exploration["stopReason"] = "no_frames"
                return self._summary["travelledM"]-started
            grid = self._grid_object(live)
            base = self._current_pose()
            pose = self._planning_pose()
            clearance = self._planning_clearance()
            if target_xy is not None and (
                    math.hypot(pose[0]-target_xy[0],pose[1]-target_xy[1]) <= EXPLORATION["arrivalM"]
                    or not still_open(grid,target_xy,EXPLORATION["sensorRadiusM"])):
                target_xy = None
            if target_xy is None:
                target = explore_target(grid,robot_xy=(pose[0],pose[1]),
                    sensor_radius_m=EXPLORATION["sensorRadiusM"],radius_m=clearance,
                    min_frontier_cells=EXPLORATION["minFrontierCells"],avoid_xy=refused,
                    blind=blind,extra_traversable=self._self_mask(grid,pose[0],pose[1],clearance))
                if target is None:
                    # Nothing reachable is still unknown: this is the completion
                    # condition, not a failure, and it is worth saying so plainly.
                    # The counts go into the report so "complete" can be checked
                    # rather than taken on faith.
                    open_frontier = frontier_mask(grid.cells)
                    if blind is not None:
                        open_frontier = open_frontier & ~blind
                    unplanned = int(open_frontier.sum())
                    # "Complete" has to mean the map is finished, not that the
                    # planner ran out of ideas. Unknown space the planner could
                    # not reach is a different, reportable outcome - and calling
                    # it complete would end the survey on a lie.
                    finished = unplanned == 0
                    self._exploration = {**coverage_report(grid.cells),"leg":number,
                        "refused":len(refused),"target":None,
                        "stopReason":"complete" if finished else "no_reachable_frontier",
                        "frontierCells":unplanned,
                        "blindCells":0 if blind is None else int(blind.sum())}
                    self._explore_complete = finished
                    return self._summary["travelledM"]-started
                target_xy = tuple(target.viewpoint)
                path = list(target.path)
            else:
                path,_length = plan_route(grid,robot_xy=(pose[0],pose[1]),goal_xy=target_xy,
                                          radius_m=clearance,avoid_xy=refused,
                                          extra_traversable=self._self_mask(grid,pose[0],pose[1],clearance))
                if path is None:
                    target_xy = None
                    continue
            self._exploration = {**coverage_report(grid.cells),"leg":number,
                "refused":len(refused),"stopReason":"",
                "target":[round(v,3) for v in target_xy]}
            with self._lock:
                self.message = (f"自动探索第 {number} 段：前往未知区域 "
                                f"({target_xy[0]:.1f}, {target_xy[1]:.1f})，"
                                f"地图已探明 {1-self._exploration['unknownFraction']:.0%}。")
            traversable = traversable_for(grid,clearance,avoid_xy=refused,
                extra_traversable=self._self_mask(grid,pose[0],pose[1],clearance))
            waypoint = next_waypoint(grid,path,lookahead_m=EXPLORATION["lookaheadM"],
                                     traversable=traversable)
            if waypoint is None:
                self._exploration["stopReason"] = "no_route"
                return self._summary["travelledM"]-started
            before = list(base)
            try:
                acted = self._drive_step(waypoint,base,pose,after_pose=None)
            except ServiceError as error:
                refused.append(tuple(target_xy))
                target_xy = None
                consecutive += 1
                if consecutive >= EXPLORATION["maxConsecutiveRefusals"]:
                    # Refusals come in storms when the planner and the driver
                    # disagree about one corner. Spinning through them burns the
                    # leg and reports nothing, so stop and say where.
                    self._exploration["stopReason"] = "no_reachable_frontier"
                    return self._summary["travelledM"]-started
                with self._lock:
                    self.message = (f"自动探索：目标被安全层拒绝（{error.code}），改选其他未知区域。")
                continue
            after = self._planning_pose()
            was = pose_se2(before)
            moved = math.hypot(after[0]-was[0],after[1]-was[1])
            if self._depth_starved >= EXPLORATION["depthStarvedLimit"]:
                # Too many views in a row measured almost nothing, so the leg has
                # nothing to register even though it can still drive. Ending here
                # publishes what was mapped instead of failing the session: a
                # partial map with a named reason beats no map at all.
                self._exploration["stopReason"] = "depth_starved"
                return self._summary["travelledM"]-started
            if not acted or (moved < 1e-3 and abs(after[2]-was[2]) < 1e-3):
                # Standing at the viewpoint already, or unable to leave it.
                # Either way this target has nothing left to give, so retire it
                # instead of re-selecting it on the next pass.
                idle += 1
                if tuple(target_xy) not in refused:
                    refused.append(tuple(target_xy))
                target_xy = None
                if idle >= EXPLORATION["maxIdleSteps"]:
                    self._exploration["stopReason"] = "no_progress"
                    return self._summary["travelledM"]-started
                continue
            consecutive = 0
            idle = 0
            # Turning is expensive in both time and keyframes, so a look-around
            # waits until the robot is actually standing at the unknown edge and
            # has covered some ground since the last one.
            travelled = self._summary["travelledM"]-started
            standing_at_edge = math.hypot(after[0]-target_xy[0],
                                          after[1]-target_xy[1]) <= EXPLORATION["arrivalM"]
            if standing_at_edge:
                # Reached it. Whether or not it revealed everything expected,
                # coming back here cannot reveal more.
                if tuple(target_xy) not in refused:
                    refused.append(tuple(target_xy))
                target_xy = None
            if (standing_at_edge and looks < EXPLORATION["maxLooksPerLeg"]
                    and travelled-last_look >= EXPLORATION["lookSpacingM"]):
                self._look_around(live,blind)
                looks += 1
                last_look = self._summary["travelledM"]-started

    def _explore_stop_reason(self, started, remaining_m, deadline, number):
        """Why this leg should stop, or ``None`` to keep exploring."""
        if self._summary["travelledM"]-started >= max(.5,remaining_m):
            return "travel_budget"
        if len(self.slam.frames) >= self.slam.MAX_FRAMES-EXPLORATION["frameMargin"]:
            # Saving is the point of stopping here: the leg still fits the frame
            # budget, so it can be published and continued rather than lost.
            return "frame_budget"
        if time.monotonic() > deadline:
            return "leg_timeout"
        return None

    def _grid_object(self, live):
        return Grid(cells=np.asarray(live["cells"],dtype=np.int16),
                    resolution=float(live["resolution"]),
                    origin=(float(live["origin"][0]),float(live["origin"][1])))

    def _drive_step(self, waypoint, base, pose, after_pose=None):
        """Face the next waypoint and take one bounded step toward it.

        ``pose`` is the robot in the planning frame and ``base`` the same pose
        as the driver reports it; the command is built in the driver's frame
        because that is the frame the safety supervisor admits motion in.

        Returns whether a command was issued. Standing at the waypoint already
        is not a fault, and treating it as one ended legs the survey still had
        work to do in.
        """
        dx,dy = waypoint[0]-pose[0],waypoint[1]-pose[1]
        distance = math.hypot(dx,dy)
        if distance < .05:
            return False
        heading = math.atan2(dy,dx)
        error = math.atan2(math.sin(heading-pose[2]),math.cos(heading-pose[2]))
        if abs(error) > EXPLORATION["headingToleranceRad"]:
            self._turn_by(error,base)
            return True
        reach = min(EXPLORATION["maxStepM"],distance)
        self._move_and_sample([pose[0]+reach*math.cos(heading),pose[1]+reach*math.sin(heading),
                               base[2],math.cos(heading/2),0.,0.,math.sin(heading/2)],
                              bounded=True,fatal=False)
        return True

    def _turn_by(self, delta_rad, base):
        """Rotate in place, in as many bounded commands as the angle needs.

        The driver accepts at most half a radian per command, so a quarter turn
        is three of them. Splitting here rather than at the call site keeps the
        bound in one place; asking for the whole turn at once is rejected outright
        and would strand the survey facing a wall.
        """
        remaining = float(delta_rad)
        while abs(remaining) > 1e-9:
            step = max(-.5,min(.5,remaining))
            pose = pose_se2(self._current_pose())
            yaw = pose[2]+step
            self._move_and_sample([pose[0],pose[1],base[2],math.cos(yaw/2),0.,0.,math.sin(yaw/2)],
                                  bounded=True,fatal=False)
            remaining -= step

    def _look_around(self, live, blind=None):
        """Turn in place while a corner of the map is still unmeasured.

        Standing still and rotating is the cheapest coverage there is: no travel,
        no new pose error, and the keyframes it costs are bounded by asking only
        where the local map is still mostly unknown.
        """
        if live is None:
            return
        grid = self._grid_object(live)
        reserve = self.slam.MAX_FRAMES-EXPLORATION["frameMargin"]-40
        base = self._current_pose()
        pose = pose_se2(list(base))
        unseen = grid.unknown_fraction(pose[0],pose[1],EXPLORATION["lookRadiusM"])
        if unseen > EXPLORATION["lookDenseThreshold"]:
            sweeps = EXPLORATION["lookSweepsMax"]
        elif unseen > EXPLORATION["lookThreshold"]:
            sweeps = EXPLORATION["lookSweepsMin"]
        else:
            return
        for _ in range(sweeps):
            if len(self.slam.frames) >= reserve:
                return
            base = self._current_pose()
            pose = pose_se2(list(base))
            self._turn_by(LOOK_AROUND_STEP_RAD,base)
            live,_blind = self._live_grid()
            if live is None:
                return
            grid = self._grid_object(live)

    def _planning_clearance(self):
        """The distance the planner keeps from observed obstacles, in metres."""
        return self.footprint_radius+EXPLORATION["planningMarginM"]

    def _current_pose(self):
        """The captured base pose, in the driver world frame the live grid uses."""
        return list(self.capture().robot_state["base_pose"])

    def _live_grid(self):
        """The occupancy the map would publish right now, plus its blind spots.

        Built with the same evidence the final map uses - measured floor plus the
        clearance certified along the driven trail - so a route the explorer
        accepts is a route the published map will also contain. Planning on the
        cloud alone would propose paths through the free space the map has but
        the robot never certified, and the driver would refuse them.
        """
        with self._slam_lock:
            if not self.slam.frames:
                return None,None
            anchor = self._planning_anchor()
            cloud = self.slam.cloud()
            sources = self.slam.cloud_sources()
            frame = PointCloud(transform(cloud.xyz,anchor).astype(np.float32),cloud.rgb)
            grid = occupancy_from_points(frame,resolution=.05,floor_z=0.,sources=sources)
            trail = [compose(anchor,f.odometry).tolist() for f in self.slam.frames]
            self._observed_travel(grid,trail,anchor)
            if self._base_map_id:
                # A continued leg plans on the union, not on its own first
                # wedge of the room: the free space it inherited is exactly what
                # makes continuing cheaper than driving the house again.
                inherited = self._cached_base_grid()
                if inherited is not None:
                    try:
                        grid = merge_grids(grid,inherited)
                    except ValueError:
                        pass
            blind = self._blind_mask(grid,trail)
        return grid,blind

    def _cached_base_grid(self):
        """The base map's grid, decoded once per leg instead of once per step."""
        key = (self._base_map_id,self._base_anchor[2] if self._base_anchor else None)
        if self._base_grid_cache is None or self._base_grid_cache[0] != key:
            self._base_grid_cache = (key,self._base_grid(self._base_map_id))
        return self._base_grid_cache[1]

    def _planning_anchor(self):
        """The map frame the planner works in: inherited, or the driver's own."""
        if self._base_anchor is None:
            return np.zeros(3)
        return np.asarray(self._base_anchor,dtype=float)

    def _planning_pose(self):
        """The robot's planar pose in the frame :meth:`_live_grid` builds."""
        return compose(self._planning_anchor(),pose_se2(self._current_pose()))

    def _self_mask(self, grid, x, y, clearance):
        """The robot's own footprint, which is drivable by construction."""
        cells = grid.cells
        mask = np.zeros(cells.shape,dtype=bool)
        resolution = grid.resolution
        origin = np.asarray(grid.origin,dtype=float)
        radius = self.footprint_radius+EXPLORATION["selfRadiusMarginM"]
        reach = max(1,math.ceil(radius/resolution))
        offsets = np.arange(-reach,reach+1)
        disc = ((offsets[None,:]*resolution)**2+(offsets[:,None]*resolution)**2 <= radius*radius)
        column = math.floor((x-origin[0])/resolution)
        row = math.floor((y-origin[1])/resolution)
        r0,r1 = max(0,row-reach),min(cells.shape[0],row+reach+1)
        c0,c1 = max(0,column-reach),min(cells.shape[1],column+reach+1)
        if r0 >= r1 or c0 >= c1:
            return mask
        mask[r0:r1,c0:c1] = disc[r0-(row-reach):r1-(row-reach),c0-(column-reach):c1-(column-reach)]
        # The footprint bridges the near-field gap between the robot's own cell
        # and the floor the camera can actually see - but it must not open the
        # clearance shadow next to a wall the robot happens to be standing
        # beside, or the plan drives straight into it and the driver refuses.
        return mask & (cells < OCCUPIED) & clearance_mask(cells,clearance/resolution)

    def _blind_mask(self, grid, trail):
        """Space the base camera has already shown it cannot measure.

        A forward-facing camera on a mobile base never sees the floor it is
        standing on or the strip it has just left. Those cells stay unknown for
        the whole survey, so a frontier detector counts them at every pose and
        the robot drives at its own footprint forever. Marking them here keeps
        the map honest - they really are unknown - while telling the planner
        that driving cannot settle them.
        """
        cells = np.asarray(grid["cells"])
        mask = np.zeros(cells.shape,dtype=bool)
        if not trail:
            return mask
        resolution = float(grid["resolution"])
        origin = np.asarray(grid["origin"],dtype=float)[:2]
        reach = max(1,math.ceil(EXPLORATION["blindRadiusM"]/resolution))
        offsets = np.arange(-reach,reach+1)
        disc = ((offsets[None,:]*resolution)**2+(offsets[:,None]*resolution)**2
                <= EXPLORATION["blindRadiusM"]**2)
        points = np.asarray(trail,dtype=float)[:,:2]
        seen = np.unique(np.floor(points/resolution).astype(np.int64),axis=0)
        for x,y in seen:
            column = round((x*resolution-origin[0])/resolution)
            row = round((y*resolution-origin[1])/resolution)
            r0,r1 = max(0,row-reach),min(mask.shape[0],row+reach+1)
            c0,c1 = max(0,column-reach),min(mask.shape[1],column+reach+1)
            if r0 >= r1 or c0 >= c1:
                continue
            mask[r0:r1,c0:c1] |= disc[r0-(row-reach):r1-(row-reach),
                                     c0-(column-reach):c1-(column-reach)]
        return mask

    def stop_motion(self, _):
        with self._lock:
            if self.state != "moving":
                return self.status()
            self._pause_requested = True
            self._cancel.set()
        return self.status()

    def cancel(self, _):
        with self._lock:
            if self.state in {"idle","completed","cancelled"}:return self.status()
            self._user_cancel = True
            self._cancel.set()
            # recording is published only after the motion thread has joined.
            # The wrapper thread may still be returning from its pause handler.
            if self.state == "recording" or self._worker is None or not self._worker.is_alive():
                self.state,self.message = "cancelled","本次扫描已取消，已保存地图仍可使用。"
                self._release_session()
            else:
                self.message = "正在停止机器人并取消扫描。"
        return self.status()

    def finish(self, _):
        with self._lock:
            if self.state == "exploring":
                # The explorer saves its own legs; a second builder running
                # alongside it would publish a map from a half-driven scan.
                raise ServiceError("SCAN_NOT_READY","自动探索进行中，它会自动保存；如需提前结束请取消扫描。")
            if self.state not in {"recording","failed"}:
                raise ServiceError("SCAN_NOT_READY","等待移动结束后再保存地图。")
            if self._worker is not None and self._worker.is_alive():
                raise ServiceError("MOTION_STOPPING","等待机器人完成停止后再保存地图。")
            if not self._reservation:
                self._reservation = self.reserve()
            self._cancel = threading.Event()
            self._pause_requested,self._user_cancel = False,False
            self.state,self.message = "finalizing","正在优化轨迹并生成导航地图和三维地图。"
            self._spawn(self._build)
        return self.status()

    def _continuation_anchor(self, map_id):
        """Read the world-to-map anchor of the map being continued.

        Uses the same validation as activation, minus the act of activating: a scan
        must not silently switch which map the robot is navigating on. The checks that
        matter here are the ones that decide whether this session's world frame is the
        one the base map was built in - robot, calibration and world frame - because if
        any of those differ, the anchor would place the new cloud in the wrong place
        and the result would look like a mapping problem rather than a mismatched pair.
        """
        directory,manifest = MapCatalog(self.root).open(map_id,robot_id=self.robot_id,
            calibration_revision=self.calibration_get()["revision"])
        artifacts = manifest.get("artifacts") or {}
        if "slam_session" not in artifacts:
            raise ServiceError("CONTINUATION_UNAVAILABLE",
                "该地图没有位姿会话记录，无法作为继续建图的基础；请选择一次扫描生成的机器人地图。")
        if artifacts["slam_session"]["bytes"] > 2_000_000:
            raise ValueError("SLAM metadata exceeds activation budget")
        metadata = json.loads((directory/artifacts["slam_session"]["href"]).read_text())
        if (metadata.get("schemaVersion") != "slam.session.v1"
                or metadata.get("robotId") != self.robot_id
                or metadata.get("calibrationRevision") != manifest["calibrationRevision"]
                or metadata.get("worldFrameRevision") != self.world_frame_revision):
            raise ServiceError("CONTINUATION_FRAME_MISMATCH",
                "该地图的机器人、标定版本或世界坐标系与当前不一致，不能在其上继续建图。")
        value = np.array(metadata["mapFromWorld"],dtype=float)
        if value.shape != (3,) or not np.isfinite(value).all():
            raise ValueError("invalid map localization anchor")
        return value.tolist()

    def _base_objects(self, map_id):
        """The base map's own object layer, or ``None`` when it has none.

        An older map was published before this layer existed; that is a missing
        answer, not an error, and the continuation simply starts its own.
        """
        try:
            directory,manifest = MapCatalog(self.root).open(map_id,robot_id=self.robot_id,
                calibration_revision=self.calibration_get()["revision"])
            entry = (manifest.get("artifacts") or {}).get("objects")
            if not entry:
                return None
            return json.loads((directory/entry["href"]).read_text())
        except (OSError,ValueError,KeyError):
            return None

    def object_layer(self, map_id):
        """The published object layer of any stored map, or ``None``.

        Read-only and failure-tolerant on purpose: a caller asking where something
        was last seen must get "nothing remembered" rather than an exception that
        takes down the observation it was decorating.
        """
        if not map_id:
            return None
        return self._base_objects(map_id)

    def _base_geometry(self, map_id):
        """The base map's own points and trail, already in the base map's frame.

        Read from the stored artifact rather than recomputed: the base map is the
        verified record of that survey, and re-deriving it from keyframes here would
        introduce a second answer to the same question.
        """
        directory,manifest = MapCatalog(self.root).open(map_id,robot_id=self.robot_id,
            calibration_revision=self.calibration_get()["revision"])
        entry = (manifest.get("artifacts") or {}).get("cloud")
        if not entry:
            return None, []
        decoded, _level = decode_lod((directory/entry["href"]).read_bytes())
        trail = []
        trail_entry = (manifest.get("artifacts") or {}).get("trajectory")
        if trail_entry:
            try:
                document = json.loads((directory/trail_entry["href"]).read_text())
                coordinates = document["features"][0]["geometry"]["coordinates"]
                trail = [[float(x),float(y),0.] for x,y in coordinates]
            except (KeyError,IndexError,TypeError,ValueError,OSError):
                # The trail is decoration on the merged map; a base map whose trail is
                # unreadable still has valid geometry worth merging.
                trail = []
        return decoded, trail

    def _base_grid(self, map_id):
        """The base map's navigation grid, in the base map's own frame.

        A survey's free space is partly evidence from its verified travel, not only
        from its point cloud, so a continuation that rebuilt the grid from the
        merged cloud alone would turn the base survey's rooms back into unknown
        space and break routing through them. The published nav2 artifacts are the
        recorded answer, and they are read back rather than re-derived.
        """
        directory,manifest = MapCatalog(self.root).open(map_id,robot_id=self.robot_id,
            calibration_revision=self.calibration_get()["revision"])
        artifacts = manifest.get("artifacts") or {}
        grid_entry,meta_entry = artifacts.get("navigation_grid"),artifacts.get("navigation")
        if not grid_entry or not meta_entry:
            return None
        try:
            return read_nav2_grid((directory/grid_entry["href"]).read_bytes(),
                                  (directory/meta_entry["href"]).read_bytes())
        except (OSError,ValueError):
            # A base map whose grid is unreadable still has geometry worth merging.
            # Refusing the whole continuation would be a worse answer than a map
            # whose new evidence is intact and whose old free space is not carried.
            return None

    def _build(self):
        self._check_cancel()
        with self._slam_lock:
            if len(self.slam.frames) < 3 or not self.slam.registrations or self._summary["travelledM"] < .15:
                raise ServiceError("SCAN_TOO_SMALL","至少移动 0.15 米并采集 3 个可配准视角，再保存地图。")
            self.slam.optimize()
            cloud,trail = self.slam.cloud(),self.slam.trajectory()
            sources = self.slam.cloud_sources()
            # DenseSLAM keeps every pose in the driver's world frame - the first
            # keyframe is seeded from odometry and the rest are composed from it - so
            # cloud() returns points in world coordinates, not map coordinates. The
            # anchor is what crosses that boundary, and it has to be applied to the
            # geometry, not only to the trail. For a first scan the anchor is near
            # identity so the omission was invisible; a continuation inherits a real
            # anchor, and skipping it here would leave the new points in the world
            # frame while the base map's points are in map coordinates.
            if self._base_map_id:
                # A continuation has to produce the union, not just this session's
                # points. Inheriting the anchor already puts both sessions in one
                # coordinate frame, so merging is a concatenation - but without it a
                # "full scan" would only ever hold the last leg, and the earlier
                # sessions would look lost even though nothing went wrong.
                inherited,inherited_trail = self._base_geometry(self._base_map_id)
                if inherited is not None and inherited.count:
                    cloud = PointCloud(np.concatenate([inherited.xyz,cloud.xyz]).astype(np.float32),
                                       np.concatenate([inherited.rgb,cloud.rgb])
                                       if inherited.rgb is not None and cloud.rgb is not None else None)
                    # -1 marks evidence this session did not produce: the inherited
                    # survey already made these claims, and they are exempt from the
                    # two-viewpoint rule rather than being erased by a re-count.
                    sources = np.concatenate([np.full(inherited.count,-1,dtype=np.int64),sources])
                    trail = inherited_trail + trail
            last = self.slam.frames[-1]
            if self._base_anchor is not None:
                # Continuing an existing map: reuse its world-to-map anchor instead of
                # deriving a fresh one. Deriving one here would place this session
                # relative to its own first frame, so the new cloud would sit next to
                # the old one rather than inside it. The robot may start anywhere -
                # including a region the base map never saw - because the anchor is a
                # rigid transform on the driver's world frame, and that frame is shared
                # across sessions (the runtime pins it with worldFrameRevision).
                anchor = np.array(self._base_anchor,dtype=float).copy()
            else:
                # map_from_world is a rigid localization anchor at the last capture.
                inverse_odom = relative(last.odometry,np.zeros(3))
                anchor = compose(last.pose,inverse_odom)
            # Recorded so an exploration leg can hand its own frame to the next
            # leg without re-deriving it from a scan that has already been reset.
            self._leg_anchor = np.asarray(anchor,dtype=float).tolist()
            cloud = PointCloud(transform(cloud.xyz,anchor).astype(np.float32),cloud.rgb)
            grid = occupancy_from_points(cloud,resolution=.05,floor_z=0.,sources=sources)
            if self._base_map_id:
                # The union of the two surveys' evidence, not just this session's.
                # Both grids are already in the base map's frame because the anchor
                # is inherited, so this is an overlay: free and occupied are each
                # the union, and only what neither survey knows stays unknown.
                inherited_grid = self._base_grid(self._base_map_id)
                if inherited_grid is not None:
                    try:
                        grid = merge_grids(grid,inherited_grid)
                    except ValueError:
                        # A base grid on a different lattice cannot be merged
                        # honestly; the new survey's own evidence still stands.
                        pass
            # Clearance evidence lives in the driver's world frame. Transform
            # actual captured odometry by the same localization anchor used at
            # execution; ICP corrections are not certified robot travel.
            measured_trail=[compose(anchor,f.odometry).tolist() for f in self.slam.frames]
            self._observed_travel(grid,measured_trail,anchor)
            provenance = {**self.slam.provenance(),"name":self.name,"calibrationRevision":self.calibration_revision,
                          "baseMapId":self._base_map_id,
                          "robotId":self.robot_id,"mapId":self.map_id,"worldFrameRevision":self.world_frame_revision,
                          "mapFromWorld":anchor.tolist(),"footprintRadiusM":self.footprint_radius,
                          "navigationEvidenceVersion":2}
            # Objects were accumulated in the frame the live geometry used; the
            # map is written in the final anchor's frame, so the layer has to make
            # the same crossing the point cloud just did.
            self.object_memory.reanchor(self._planning_anchor(),anchor)
            if self._base_map_id:
                adopted = self._base_objects(self._base_map_id)
                if adopted:
                    try:
                        self.object_memory.merge(adopted)
                    except ValueError as error:
                        self._object_errors.append(f"base map objects: {error}")
                        del self._object_errors[:-4]
            now_ms = int(time.time()*1000)
            object_layer = self.object_memory.document(
                now_unix_ms=now_ms, map_id=self.map_id, calibration_revision=self.calibration_revision)
            self.root.mkdir(parents=True,exist_ok=True)
            staging = self.root/("."+self.map_id+".building")
            manifest = build_map(staging,map_id=self.map_id,robot_id=self.robot_id,cloud=cloud,
                poses=trail,times_unix_ms=[f.timestamp for f in self.slam.frames],source="rgbd_slam",
                calibration_revision=self.calibration_revision,occupancy_grid=grid,
                semantic_workspaces=self.semantic_workspaces(anchor),semantic_objects=object_layer,
                slam_metadata=provenance,
                slam_keyframes=self.slam.previews.document(map_id=self.map_id, robot_id=self.robot_id,
                    calibration_revision=self.calibration_revision))
            with self._lock:
                # Cancellation wins before this publication boundary; after it,
                # the immutable map and completion state are committed together.
                self._check_cancel()
                os.rename(staging,self.root/self.map_id)
                self._load_map(self.map_id)
                self.state,self.message = "completed","扫描完成，导航地图已启用，三维地图已保存。"
                self._summary["trajectory"] = trail
                self._summary["pointCount"] = manifest["pointCount"]
        self._release_session()

    def _release_session(self):
        with self._lock:
            token,self._reservation = self._reservation,None
        if token:
            self.release(token)

    def _observed_travel(self, grid, trail, anchor):
        """Include measured base travel; a wider band requires the driver's proof.

        A forward camera may never see the start pose. Keep the robot and its
        trajectory inside the grid, with unknown padding until evidence clears
        it. Never silently clip the initial/terminal chassis out of the map.

        The certified band is the planning clearance, not the planning clearance
        plus a bonus. It used to be footprint + 0.08 m, which the driver's guard
        proved while that guard was a flat 0.40 m; the guard is now the measured
        CAD envelope plus a commissioned margin (0.355 m), so a 0.40 m query is
        refused - correctly, because a proof may not claim more than the check
        behind it - and asking for it would silently leave the whole driven trail
        uncertified. One number, planned and proved.
        """
        res,origin = grid["resolution"],np.array(grid["origin"],dtype=float)
        self._mark_cells(grid,trail,self.footprint_radius,res,origin)
        radius = self._planning_clearance()
        points=np.array(trail)[:,:2]
        low=np.floor((points.min(axis=0)-radius-origin[:2])/res).astype(int)
        high=np.ceil((points.max(axis=0)+radius-origin[:2])/res).astype(int)
        before=np.maximum(-low,0)
        after=np.maximum(high-np.array([grid["width"],grid["height"]])+1,0)
        width,height=np.array([grid["width"],grid["height"]])+before+after
        if width*height>250000:
            raise ServiceError("MAP_TOO_LARGE","本次扫描超出地图预算，请分区域建图。")
        cells=np.pad(grid["cells"],((before[1],after[1]),(before[0],after[0])),constant_values=-1)
        origin[:2]-=before*res
        grid.update(cells=cells,width=int(width),height=int(height),origin=origin.tolist())
        if self.clearance_validator is None:
            return  # Sparse sensor keyframes do not certify the path between them.
        inverse=relative(anchor,np.zeros(3))
        for start,end in itertools.pairwise(trail):
            distance = np.linalg.norm(np.array(end[:2])-start[:2])
            for fraction in np.linspace(0,1,max(2,int(distance/res)+1)):
                point = np.array(start[:2])*(1-fraction)+np.array(end[:2])*fraction
                if self.clearance_validator:
                    world=compose(inverse,[*point,0.])
                    if self.clearance_validator(world[:2].tolist(),radius) is not True:
                        continue
                self._mark_cells(grid,[[*point,0.]],radius,res,origin)

    def _mark_cells(self, grid, points, radius, res, origin):
        """Mark the footprint around measured poses as drivable.

        This is proprioception, not inference about the room: the robot was
        standing at these poses, so those cells cannot contain an obstacle. It
        is what keeps the commissioning pose plannable at all - a forward camera
        never photographs the floor it is standing on, so without this the cell
        the robot occupies stays unknown and the router refuses to start from
        its own position. Observed obstacles still win: a cell the cloud calls
        occupied is never opened.
        """
        cells = grid["cells"]
        for point in points:
            col,row = np.floor((np.asarray(point[:2])-np.array(origin[:2]))/res).astype(int)
            n = math.ceil(radius/res)
            for y in range(max(0,row-n),min(cells.shape[0],row+n+1)):
                for x in range(max(0,col-n),min(cells.shape[1],col+n+1)):
                    if (math.hypot((x+.5)*res+origin[0]-point[0],(y+.5)*res+origin[1]-point[1])
                            +res/math.sqrt(2) < radius and cells[y,x] < 65):
                        cells[y,x] = 0

    def activate(self, parameters):
        token = self.reserve()
        try: self._load_map(parameters["mapId"])
        finally: self.release(token)
        return self.status()

    def _load_map(self, map_id, persist=True, expected_revision=None):
        directory,manifest = MapCatalog(self.root).open(map_id,robot_id=self.robot_id,
            calibration_revision=self.calibration_get()["revision"],map_revision=expected_revision)
        artifacts = manifest["artifacts"]
        if "slam_session" not in artifacts:
            raise ServiceError("LOCALIZATION_REQUIRED","此地图需要机器人定位服务提供标定变换，不能直接启用。")
        if artifacts["slam_session"]["bytes"] > 2_000_000:
            raise ValueError("SLAM metadata exceeds activation budget")
        metadata = json.loads((directory/artifacts["slam_session"]["href"]).read_text())
        if (metadata.get("schemaVersion") != "slam.session.v1" or metadata.get("navigationEvidenceVersion") != 2
                or metadata.get("robotId") != self.robot_id
                or metadata.get("mapId") != map_id or metadata.get("calibrationRevision") != manifest["calibrationRevision"]
                or metadata.get("worldFrameRevision") != self.world_frame_revision):
            raise ServiceError("LOCALIZATION_REQUIRED","地图与当前机器人定位坐标系不一致，请重新定位或重新扫描。")
        anchor = np.array(metadata["mapFromWorld"],dtype=float)
        if anchor.shape != (3,) or not np.isfinite(anchor).all():
            raise ValueError("invalid map localization anchor")
        grid = MapCatalog.navigation_grid(directory,manifest)
        active = {"mapId":map_id,"mapRevision":manifest["hash"],"calibrationRevision":manifest["calibrationRevision"]}
        if persist:
            temporary = self.root/".active-map.tmp"
            temporary.write_text(json.dumps(active),encoding="utf-8")
            os.replace(temporary,self.root/"active-map.json")
        with self._lock:
            self.active,self.grid,self.map_from_world = active,grid,anchor

    def _restore_active(self):
        try:
            active = json.loads((self.root/"active-map.json").read_text())
            if active.get("calibrationRevision") != self.calibration_get()["revision"]:
                return
            self._load_map(active["mapId"],persist=False,expected_revision=active["mapRevision"])
        except (OSError,ValueError,KeyError):
            self.active,self.grid = None,None

    def navigation_map(self):
        with self._lock:
            active,grid,anchor = copy.deepcopy(self.active),self.grid,self.map_from_world.copy()
        if not active or grid is None:
            return {"robotId":self.robot_id,"frameId":"map","ready":False,"localizationState":"unavailable",
                    "mode":"mapping","gridUnavailable":True,"cells":[],"origin":[],"mapPose":[]}
        observation = self.capture()
        base = list(observation.robot_state["base_pose"])
        pose = compose(anchor,pose_se2(base))
        stamp = observation.wall_time_unix_ms
        with self._lock:still_active=active==self.active
        return {"robotId":self.robot_id,"frameId":"map","ready":still_active and 0<=int(time.time()*1000)-stamp<=1000,
                "mode":"localization","mapRevision":active["mapRevision"],"mapId":active["mapId"],
                "width":grid["width"],"height":grid["height"],"resolution":grid["resolution"],"cells":grid["cells"].ravel().tolist(),
                "origin":[*grid["origin"][:2],0.,math.cos(grid["origin"][2]/2),0.,0.,math.sin(grid["origin"][2]/2)],
                "mapPose":[pose[0],pose[1],base[2],math.cos(pose[2]/2),0.,0.,math.sin(pose[2]/2)],
                "poseSource":"registered_localization","poseObservedAtUnixMs":stamp,"observedAtUnixMs":int(time.time()*1000),
                "localizationState":"localized","gridUnavailable":False}
