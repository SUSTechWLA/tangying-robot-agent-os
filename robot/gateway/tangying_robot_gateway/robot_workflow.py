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
from .map_catalog import MapCatalog
from .map_pipeline import build_map, occupancy_from_points
from .service_registry import RegisteredService, ServiceError, object_schema


class RobotWorkflow:
    """The provider supplies acquisition, commissioned motion and calibration.

    Reservation must atomically exclude ordinary robot commands; service calls
    that move use the very same safety supervisor as normal tool execution.
    """
    def __init__(self, *, robot_id, root, calibration_get, calibration_run,
                 calibration_save, capture, move, reserve, release,
                 survey_goals, semantic_workspaces, footprint_radius=.30, world_frame_revision=None,
                 clearance_validator=None):
        self.robot_id = robot_id
        self.root = Path(root).resolve()
        self.calibration_get, self.calibration_run, self.calibration_save = calibration_get, calibration_run, calibration_save
        self.capture, self.move, self.reserve, self.release = capture, move, reserve, release
        self.survey_goals, self.semantic_workspaces = survey_goals, semantic_workspaces
        self.footprint_radius = footprint_radius
        self.clearance_validator = clearance_validator
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
        self._restore_active()

    def register(self, registry):
        number = lambda maximum: {"type":"number","minimum":.01,"maximum":maximum}
        entries = [
            ("calibration.get","读取机器人标定与编辑模板",object_schema(),lambda _:self.calibration_get(),False),
            ("calibration.run","运行机器人注册的标定算法",object_schema(),self.run_calibration,True),
            ("calibration.save","验证并应用自行标定结果",object_schema({"document":{"type":"object","additionalProperties":True},"expectedRevision":{"type":"string"},"algorithm":{"type":"string"}},["document","expectedRevision"]),self.save_calibration,True),
            ("mapping.status","读取扫描进度和当前地图",object_schema(),lambda _:self.status(),False),
            ("mapping.start","开始机器人移动与 RGB-D SLAM",object_schema({"name":{"type":"string"},"mode":{"type":"string","enum":["manual","survey"]}}),self.start,True),
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
                "message":self.message,"activeMap":self.active,"preview":self._preview,**self._summary})

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
            self.slam = DenseSLAM()
            self.session_id = uuid.uuid4().hex
            self.map_id = "scan-"+self.session_id[:12]
            self.name = str(parameters.get("name", "家庭地图"))[:120]
            self.state,self.message = "moving","正在采集第一帧。"
            self._preview = {"points":[],"colors":[]}
            self._summary = {"frameCount":0,"pointCount":0,"travelledM":0.,"registrationCount":0,"loopClosures":0,"trajectory":[]}
            self._spawn(lambda:self._begin(parameters.get("mode","manual")))
        return self.status()

    def _begin(self, mode):
        self._sample()
        if mode == "survey":
            goals = self.survey_goals()
            if not goals:
                raise ServiceError("SURVEY_UNAVAILABLE","机器人未注册自动扫描路线，请选择手动扫描。")
            for i,goal in enumerate(goals):
                self._check_cancel()
                with self._lock: self.message = f"正在扫描路线 {i+1} / {len(goals)}，持续采集 RGB-D。"
                self._move_and_sample(goal)
            with self._lock: self.state = "finalizing"
            self._build()
        else:
            self._check_cancel()
            with self._lock:
                self._check_cancel()
                self.state,self.message = "recording","已开始采集，可使用方向按钮移动机器人。"

    def _check_cancel(self):
        if self._cancel.is_set():
            raise ServiceError("CANCELLED","扫描移动已停止。")

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
            added = self.slam.add(observation)
            if not added: return
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

    def _move_and_sample(self, goal):
        result = []
        def moving():
            try: result.append(self.move(goal,self._cancel))
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
        self._move_and_sample(goal)
        with self._lock:
            self._check_cancel()
            self.state,self.message = "recording","移动完成，可继续扫描或保存地图。"

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

    def _build(self):
        self._check_cancel()
        with self._slam_lock:
            if len(self.slam.frames) < 3 or not self.slam.registrations or self._summary["travelledM"] < .15:
                raise ServiceError("SCAN_TOO_SMALL","至少移动 0.15 米并采集 3 个可配准视角，再保存地图。")
            self.slam.optimize()
            cloud,trail = self.slam.cloud(),self.slam.trajectory()
            last = self.slam.frames[-1]
            # map_from_world is a rigid localization anchor at the last capture.
            inverse_odom = relative(last.odometry,np.zeros(3))
            anchor = compose(last.pose,inverse_odom)
            grid = occupancy_from_points(cloud,resolution=.05,floor_z=0.)
            # Clearance evidence lives in the driver's world frame. Transform
            # actual captured odometry by the same localization anchor used at
            # execution; ICP corrections are not certified robot travel.
            measured_trail=[compose(anchor,f.odometry).tolist() for f in self.slam.frames]
            self._observed_travel(grid,measured_trail,anchor)
            provenance = {**self.slam.provenance(),"name":self.name,"calibrationRevision":self.calibration_revision,
                          "robotId":self.robot_id,"mapId":self.map_id,"worldFrameRevision":self.world_frame_revision,
                          "mapFromWorld":anchor.tolist(),"footprintRadiusM":self.footprint_radius,
                          "navigationEvidenceVersion":2}
            self.root.mkdir(parents=True,exist_ok=True)
            staging = self.root/("."+self.map_id+".building")
            manifest = build_map(staging,map_id=self.map_id,robot_id=self.robot_id,cloud=cloud,
                poses=trail,times_unix_ms=[f.timestamp for f in self.slam.frames],source="rgbd_slam",
                calibration_revision=self.calibration_revision,occupancy_grid=grid,
                semantic_workspaces=self.semantic_workspaces(anchor),slam_metadata=provenance,
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
        """Include measured base travel; enlarged clearance requires a validator.

        A forward camera may never see the start pose. Keep the robot and its
        trajectory inside the grid, with unknown padding until evidence clears
        it. Never silently clip the initial/terminal chassis out of the map.
        """
        res,origin = grid["resolution"],np.array(grid["origin"],dtype=float)
        radius = self.footprint_radius+.08 if self.clearance_validator else self.footprint_radius
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
                col,row = np.floor((point-np.array(origin[:2]))/res).astype(int)
                n = math.ceil(radius/res)
                for y in range(max(0,row-n),min(cells.shape[0],row+n+1)):
                    for x in range(max(0,col-n),min(cells.shape[1],col+n+1)):
                        if math.hypot((x+.5)*res+origin[0]-point[0],(y+.5)*res+origin[1]-point[1])+res/math.sqrt(2) < radius and cells[y,x] < 65:
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
