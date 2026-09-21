"""Live Gazebo acceptance through the same public RPCs used by the Agent.

Never declares manipulation parity from camera/navigation success. --require-full
fails if any canonical task capability is missing. --estop latches the runtime:
use a disposable stack namespace; restart deliberately preserves that latch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path

import grpc
from google.protobuf.json_format import MessageToDict, ParseDict
from tangying_robot_proto.robot.v1 import robot_pb2 as pb
from tangying_robot_proto.robot.v1 import robot_pb2_grpc as rpc

FULL_TASK_TOOLS = {"observe_scene", "resolve_targets", "plan_grasp", "manipulation.pick",
                   "verify_grasp", "manipulation.place", "verify_placement", "navigation.navigate",
                   "verify_arrival", "recover_to_safe_pose", "emergency_stop"}


def evaluate(address, output, *, require_full=False, estop=False, motion=False, arm_only=False, cancel=False):
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    stub = rpc.RobotRuntimeStub(grpc.insecure_channel(address))
    info = stub.GetRuntimeInfo(pb.GetRuntimeInfoRequest(), timeout=10)
    if info.adapter != "gazebo":
        raise RuntimeError(f"wrong adapter: {info.adapter}")
    profile = MessageToDict(info.robot_profile)
    available = {c.name for c in info.capabilities if c.available}

    def check(name, operation):
        start = time.monotonic()
        try:
            data = operation()
            rows.append({"check": name, "status": "PASS", "seconds": round(time.monotonic()-start, 3), "data": data})
        except (AssertionError, grpc.RpcError, ValueError, KeyError) as error:
            rows.append({"check": name, "status": "FAIL", "seconds": round(time.monotonic()-start, 3), "error": str(error)})

    def capture(source):
        stream = stub.Observe(pb.ObserveRequest(source_id=source, streams=["rgb", "depth", "reconstruction", "robot_state"]), timeout=10)
        try:
            return next(stream)
        finally:
            stream.cancel()

    def camera(source):
        frame = capture(source)
        reconstruction = MessageToDict(frame.reconstruction)
        assert reconstruction["sourceId"] == source
        assert reconstruction["observationId"] == frame.observation_id
        assert reconstruction["sourceType"] == "rgbd_camera"
        assert len(reconstruction["points"]) >= 50
        assert frame.compressed_image.startswith(b'\x89PNG') and frame.compressed_depth_image.startswith(b'\x89PNG')
        age = int(time.time()*1000) - frame.wall_time_unix_ms
        assert 0 <= age <= 1000, f"capture age {age} ms"
        (output / (source.split('/')[-1]+'.png')).write_bytes(frame.compressed_image)
        return {"source": source, "ageMs": age, "points": len(reconstruction["points"]),
                "capture": frame.observation_id, "rgbSHA256": hashlib.sha256(frame.compressed_image).hexdigest()}

    for sensor in profile['sensors']:
        if sensor['sourceType'] == 'rgbd_camera':
            check('camera:'+sensor['sourceId'], lambda source=sensor['sourceId']: camera(source))

    def services():
        catalogue = stub.ListServices(pb.GetRuntimeInfoRequest(), timeout=10)
        names = {s.name for s in catalogue.services if s.available}
        assert {"mapping.start", "mapping.cancel", "mapping.status", "calibration.get", "navigation.map"} <= names, names
        return sorted(names)
    check('workflow_catalogue', services)

    def make_command(skill, parameters):
        identity = uuid.uuid4().hex
        request = pb.SkillCommand(schema_version="robot.v1", command_id=identity, task_id="gazebo-eval",
            skill=skill, robot_id=info.robot_id, catalog_revision=info.catalog_revision,
            deadline_unix_ms=int(time.time()*1000)+20000, lease_ms=20000,
            idempotency_key=identity, safety_profile="desktop_standard", approval_id="simulation-eval")
        ParseDict(parameters, request.parameters)
        return request

    def command(skill, parameters):
        events = list(stub.ExecuteSkill(make_command(skill, parameters), timeout=25))
        assert events, "no terminal event"
        return events[-1]

    def verify(wrong=False):
        current = MessageToDict(capture(profile['sensors'][0]['sourceId']).robot_state)['base_pose']
        if wrong:
            current[0] += 1.0
        result = command('verify_arrival', {'goalPose': current})
        assert result.code == ('NOT_AT_DESTINATION' if wrong else 'OK'), (result.code, result.message)
        return {"code": result.code}
    check('arrival_current_pose', verify)
    check('arrival_wrong_pose_refused', lambda: verify(True))

    if motion or arm_only:
        def arm():
            key = 'left_arm_shoulder_pan'
            before = MessageToDict(capture(profile['sensors'][0]['sourceId']).robot_state)['joint_positions'][key]
            goal = before + .15
            result = command('arm.move', {'action_chunk': [{key+'.pos': goal}]})
            assert result.type == pb.SKILL_EVENT_SUCCEEDED, (result.code, result.message)
            after = MessageToDict(capture(profile['sensors'][0]['sourceId']).robot_state)['joint_positions'][key]
            assert abs(after-goal) < .04 and abs(after-before) > .08, (before, after, goal)
            restored = command('recover_to_safe_pose', {'action_chunk': [{key+'.pos': before}]})
            assert restored.type == pb.SKILL_EVENT_SUCCEEDED, restored.code
            return {'beforeRad': before, 'afterRad': after, 'targetRad': goal, 'recovered': True}
        check('joint_move_and_recovery', arm)
    if motion:
        def navigate():
            import math
            initial = MessageToDict(capture(profile['sensors'][0]['sourceId']).robot_state)['base_pose']
            goal = list(initial)
            yaw = 2*math.atan2(goal[6], goal[3])
            goal[0] += .15*math.cos(yaw)
            goal[1] += .15*math.sin(yaw)
            result = command('navigation.navigate', {'goalPose': goal})
            assert result.type == pb.SKILL_EVENT_SUCCEEDED, (result.code, result.message)
            after = MessageToDict(capture(profile['sensors'][0]['sourceId']).robot_state)['base_pose']
            distance = math.dist(initial[:2], after[:2])
            assert distance >= .05, f'only moved {distance} m'
            return {'distanceM': distance, 'code': result.code}
        check('nav2_measured_motion', navigate)

    if cancel:
        def cancellation():
            import threading
            key = 'left_arm_shoulder_pan'
            source = profile['sensors'][0]['sourceId']
            before = MessageToDict(capture(source).robot_state)['joint_positions'][key]
            request = make_command('arm.move', {'action_chunk': [{key+'.pos': before+1.2}]})
            running = threading.Event()
            events, errors = [], []
            def collect():
                try:
                    for event in stub.ExecuteSkill(request, timeout=25):
                        events.append(event)
                        if event.type == pb.SKILL_EVENT_RUNNING:
                            running.set()
                except grpc.RpcError as error:
                    errors.append(str(error))
            worker = threading.Thread(target=collect, daemon=True)
            worker.start()
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                frame = capture(source)
                measured = MessageToDict(frame.robot_state)['joint_positions'][key]
                if frame.semantic_state.activity == 'EXECUTING' and abs(measured-before) >= .04:
                    break
                time.sleep(.05)
            else:
                raise AssertionError('skill never observed moving while executing')
            time.sleep(.1)
            receipt = stub.Cancel(pb.CancelRequest(command_id=request.command_id, reason='ACCEPTANCE_CANCEL'), timeout=10)
            worker.join(5)
            assert receipt.accepted and not worker.is_alive() and not errors, (receipt, errors, [(e.code, e.message) for e in events])
            assert events[-1].type == pb.SKILL_EVENT_CANCELLED, events[-1].code
            time.sleep(.3)
            first = MessageToDict(capture(source).robot_state)['joint_positions'][key]
            time.sleep(.4)
            second = MessageToDict(capture(source).robot_state)['joint_positions'][key]
            assert abs(second-first) < .04, (first, second)
            recovery = command('recover_to_safe_pose', {'action_chunk': [{key+'.pos': before}]})
            assert recovery.type == pb.SKILL_EVENT_SUCCEEDED, recovery.code
            return {'terminalCode': events[-1].code, 'driftAfterCancelRad': abs(second-first), 'recovered': True}
        check('cancel_joint_motion_and_recover', cancellation)

    if estop:
        def stop():
            receipt = stub.EmergencyStop(pb.EStopRequest(reason='GAZEBO_ACCEPTANCE', operator_id='simulation-eval'), timeout=10)
            assert receipt.latched
            blocked = stub.CallService(pb.ServiceRequest(name='mapping.start', request_id=uuid.uuid4().hex), timeout=10)
            assert not blocked.ok and blocked.code == 'EMERGENCY_STOP_LATCHED', blocked.code
            return {"latched": receipt.latched, "mappingBlocked": blocked.code}
        check('estop_blocks_service_motion', stop)
    missing = sorted(FULL_TASK_TOOLS - available)
    report = {"schemaVersion": "gazebo.acceptance.v1", "evaluatedAtUnixMs": int(time.time()*1000), "robotId": info.robot_id,
              "adapterVersion": info.adapter_version, "scope": "capability_gate" if require_full else "integration", "worldRevision": profile['sensors'][0]['transformRevision'],
              "checks": rows, "missingTaskCapabilities": missing, "fullTaskCapabilitiesAvailable": not missing, "fullTaskParity": False,
              "fullTaskParityReason": "No end-to-end manipulation task executed by this probe",
              "passed": all(r['status']=='PASS' for r in rows) and (not require_full or not missing)}
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', default='127.0.0.1:50051')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-full', action='store_true')
    parser.add_argument('--estop', action='store_true')
    parser.add_argument('--cancel', action='store_true')
    parser.add_argument('--motion', action='store_true')
    parser.add_argument('--arm-only', action='store_true', help='Test joints without requiring a commissioned navigation map')
    args = parser.parse_args()
    raise SystemExit(0 if evaluate(args.runtime, args.output, require_full=args.require_full, estop=args.estop, motion=args.motion, arm_only=args.arm_only, cancel=args.cancel) else 1)
