"""Reference driver bindings for the ordinary robot service catalogue."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid

import numpy as np
from tangying_robot_gateway.dense_slam import compose, pose_se2, transform
from tangying_robot_gateway.robot_workflow import RobotWorkflow
from tangying_robot_gateway.service_registry import ServiceError
from tangying_robot_proto.robot.v1 import robot_pb2

from .home_scene import HOME_WAYPOINTS


class WorkflowBindings:
    def __init__(self, service):
        self.service = service
        self.session = {"status":"idle","message":"选择机器人服务标定，或录入自己的标定结果。"}
        self.workflow = RobotWorkflow(robot_id=service._robot_id,
            root=os.environ.get("TANGYING_MAP_ROOT","artifacts/maps"),
            calibration_get=self.calibration_get,calibration_run=self.calibration_run,
            calibration_save=self.calibration_save,capture=self.capture,move=self.move,
            reserve=self.reserve,release=self.release,survey_goals=self.survey_goals,
            semantic_workspaces=self.semantic_workspaces,footprint_radius=.32,
            clearance_validator=service.navigation.verified_travel_clearance,
            world_frame_revision=hashlib.sha256(b"fixed-model-world-v2"+service.world.model.body_pos.tobytes()
                +service.world.model.geom_pos.tobytes()+service.world.model.geom_size.tobytes()).hexdigest())
        self.workflow.register(service.services)

    def reserve(self):
        service = self.service
        with service._commands_lock:
            if service._estopped:
                raise ServiceError("EMERGENCY_STOP_LATCHED","机器人急停已锁定，请在机器人服务恢复后继续。")
            if service._service_reserved or service._active_commands:
                raise ServiceError("ROBOT_BUSY","机器人正在执行任务或扫描，请先完成或停止当前操作。")
            token = uuid.uuid4().hex
            service._service_reserved = token
            return token

    def release(self, token):
        with self.service._commands_lock:
            if token and self.service._service_reserved == token:
                self.service._service_reserved = False

    def calibration_get(self):
        calibration = self.service.calibration
        return {"available":True,"document":copy.deepcopy(calibration.document),"revision":calibration.revision,
                "session":copy.deepcopy(self.session),"methods":["service","manual"]}

    def calibration_run(self):
        # Derivation is the registered reference driver's algorithm. No simulator
        # choice is exposed to or interpreted by the OS or the user flow.
        service = self.service
        with service.world.lock,service._capture_lock,service.navigation._capture_lock:
            service.calibration.replace(service.calibration.derive(),expected_revision=service.calibration.revision)
            service.world._publish_sensor_snapshot()
            self.session = {"status":"completed","message":"机器人标定服务已完成，参数已经应用。"}
        return self.calibration_get()

    def calibration_save(self, document, expected_revision, algorithm):
        service = self.service
        if set(document.get("motors",{})) != set(service.calibration.document["motors"]):
            raise ServiceError("MOTOR_LAYOUT_MISMATCH","标定电机必须与当前机器人注册的电机布局一致。")
        if set(document.get("cameras",{})) != set(service.calibration.document["cameras"]):
            raise ServiceError("CAMERA_LAYOUT_MISMATCH","标定相机必须与当前机器人注册的相机一致。")
        # Physical topology cannot be changed by a calibration document.
        for name,camera in document["cameras"].items():
            parent = camera.get("extrinsics",{}).get("parentLink")
            if parent != service.calibration.document["cameras"][name]["extrinsics"]["parentLink"]:
                raise ServiceError("CAMERA_PARENT_MISMATCH","相机父坐标系必须与注册的机器人结构一致。")
        with service.world.lock,service._capture_lock,service.navigation._capture_lock:
            service.calibration.replace(document,expected_revision=expected_revision)
            service.world._publish_sensor_snapshot()
            self.session = {"status":"completed","message":"自行标定结果已验证、保存并应用。","algorithm":algorithm}
            if service.calibration.root:
                path = service.calibration.root/"provenance.json"
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps({**self.session,"revision":service.calibration.revision},ensure_ascii=False))
                os.replace(temporary,path)
        return self.calibration_get()

    def capture(self):
        return self.service._sensor_observation(self.service._robot_id+"/base-rgbd")

    def move(self, goal, cancel):
        service = self.service
        identifier = "mapping-"+uuid.uuid4().hex
        command = robot_pb2.SkillCommand(schema_version="robot.v1",command_id=identifier,
            task_id="commissioning",skill="navigation.navigate",robot_id=service._robot_id,
            idempotency_key=identifier,safety_profile="simulation",approval_id="operator-service-request",
            deadline_unix_ms=int(time.time()*1000)+120000,lease_ms=120000)
        command.parameters.update({"goalPose":list(goal)})
        service._service_owner.enabled = True
        service._service_owner.cancel = cancel
        try:
            if cancel.is_set(): return {"ok":False,"code":"CANCELLED","message":"扫描移动已停止。"}
            events = list(service.execute_for_test(command))
            terminal = events[-1]
            return {"ok":terminal.type==robot_pb2.SKILL_EVENT_SUCCEEDED,"code":terminal.code,"message":terminal.message}
        finally:
            service._service_owner.enabled = False
            service._service_owner.cancel = None

    def survey_goals(self):
        if self.service.world.scene not in {"home","home_task"}: return []
        names = ["home_corridor","kitchen","home_corridor","bedroom","bathroom","bedroom","home_corridor","living_room"]
        return [list(HOME_WAYPOINTS[name]) for name in names]

    def semantic_workspaces(self, anchor):
        if self.service.world.scene not in {"home","home_task"}: return []
        aliases = {"living_room":["客厅"],"kitchen":["厨房"],"bedroom":["卧室"],"bathroom":["浴室","卫生间"],"home_corridor":["走廊","hallway"]}
        result=[]
        for name,goal in HOME_WAYPOINTS.items():
            point = transform(np.array([goal[:3]],dtype=float),anchor)[0]
            pose = compose(anchor,pose_se2(goal))
            result.append({"name":name,"aliases":aliases.get(name,[]),"target":point.tolist(),
                           "navigationPose":[point[0],point[1],goal[2],float(np.cos(pose[2]/2)),0.,0.,float(np.sin(pose[2]/2))],
                           "annotationSource":"commissioned_workspace"})
        return result
