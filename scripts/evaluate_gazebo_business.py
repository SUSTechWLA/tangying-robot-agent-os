"""Real Gazebo business acceptance with durable evidence and an independent oracle.

Use a fresh isolated scene for each invocation. Executes physical simulation
commands once: a failed or unknown outcome is recorded and never auto-retried.
The optional native-physics oracle is evaluation-only, never planner input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

import grpc
from console_session import fetch_live_token
from console_session import headers as session_headers
from google.protobuf.json_format import MessageToDict, ParseDict
from tangying_robot_proto.robot.v1 import robot_pb2 as pb
from tangying_robot_proto.robot.v1 import robot_pb2_grpc as rpc

CASES = {
    'place': ('把红色杯子放进右侧收纳盒', [('red-cup', 'right-bin')]),
    'fetch': ('把蓝色瓶子拿过来', [('blue-bottle', 'front-tray')]),
    'sequence': ('把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来',
                 [('red-cup', 'right-bin'), ('blue-bottle', 'front-tray')]),
}
MODELS = {'red-cup': 'red_cup', 'blue-bottle': 'blue_bottle',
          'right-bin': 'tray_floor', 'front-tray': 'delivery_tray'}


def physics(container):
    result = subprocess.run(['docker', 'exec', container, 'bash', '-lc',
        'source /opt/ros/jazzy/setup.bash; timeout 4 gz topic -e -t /tangying/suction/state -n 1'],
        capture_output=True, text=True, timeout=8, check=True)
    state = json.loads(json.loads(result.stdout.strip().removeprefix('data: ')))
    assert state['schemaVersion'] == 'gazebo.suction.v1'
    return state


def oracle(samples, goals):
    assert len({s['sequence'] for s in samples}) == len(samples), 'repeated physics frame'
    assert samples[-1]['simTimeNs']-samples[0]['simTimeNs'] >= 200_000_000, 'physics window too short'
    for obj, dest in goals:
        previous = None
        for sample in samples:
            assert not sample['attached'], 'payload remains attached'
            p, d = sample['objects'][MODELS[obj]], sample['objects'][MODELS[dest]]
            assert max(abs(p[n]-d[n]) for n in (0, 1)) <= .043, f'{obj} not contained in {dest}'
            assert abs(p[2]-d[2]-.075) <= .012, f'{obj} not supported'
            assert 1-2*(p[4]**2+p[5]**2) >= math.cos(.15), f'{obj} tipped over'
            if previous is not None:
                assert math.dist(p[:3], previous[:3]) <= .004, f'{obj} unstable'
            previous = p


def evaluate(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows, captures = [], []
    report = {'schemaVersion': 'gazebo.business_eval.v1', 'case': args.case, 'mode': args.mode,
              'startedAtUnixMs': int(time.time()*1000), 'scope': 'commissioned_coloured_workcell',
              'graspMode': 'sim_suction', 'checks': rows, 'captures': captures, 'passed': False,
              'independentPhysicsOracle': bool(args.oracle_container)}
    channel = grpc.insecure_channel(args.runtime)
    stub = rpc.RobotRuntimeStub(channel)
    try:
        info = stub.GetRuntimeInfo(pb.GetRuntimeInfoRequest(), timeout=10)
        assert info.adapter == 'gazebo', 'wrong runtime adapter'
        report['runtime'] = MessageToDict(info)
        report['sourceRevision'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        report['sourceDirty'] = bool(subprocess.check_output(['git', 'status', '--porcelain'], text=True).strip())
        if args.oracle_container:
            # Bind the measurements to the exact running image, not just the
            # checkout which may have changed since the container was started.
            container = json.loads(subprocess.check_output(
                ['docker', 'inspect', args.oracle_container], text=True))[0]
            image = json.loads(subprocess.check_output(
                ['docker', 'image', 'inspect', container['Image']], text=True))[0]
            report['simulationImage'] = {'id': container['Image'],
                'sourceSHA256': image['Config'].get('Labels', {}).get('org.tangying.source.sha256')}
            versions = subprocess.check_output(['docker', 'exec', args.oracle_container,
                'cat', '/opt/tangying-nav/simulation-packages.txt'], text=True)
            (output/'simulation-packages.txt').write_text(versions)
        text, goals = CASES[args.case]

        def capture(label):
            source = info.robot_id+'/head-rgbd'
            stream = stub.Observe(pb.ObserveRequest(source_id=source,
                streams=['rgb', 'depth', 'reconstruction', 'robot_state']), timeout=10)
            try:
                frame = next(stream)
            finally:
                stream.cancel()
            assert 0 <= int(time.time()*1000)-frame.wall_time_unix_ms <= 1000, 'stale capture'
            (output/(label+'.png')).write_bytes(frame.compressed_image)
            (output/(label+'-depth.png')).write_bytes(frame.compressed_depth_image)
            data = MessageToDict(frame)
            data.pop('compressedImage', None); data.pop('compressedDepthImage', None)
            (output/(label+'.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
            captures.append({'label': label, 'observationId': frame.observation_id,
                'sha256': hashlib.sha256(frame.compressed_image).hexdigest()})

        capture('before')
        if args.oracle_container:
            before = physics(args.oracle_container)
            (output/'physics-before.json').write_text(json.dumps(before, indent=2)+'\n')
        if args.mode == 'rpc':
            task = 'gazebo-business-'+uuid.uuid4().hex
            for obj, dest in goals:
                for skill, params, target in (
                    ('resolve_targets', {'objectId': obj, 'destinationId': dest}, ''),
                    ('plan_grasp', {'objectId': obj, 'destinationId': dest}, ''),
                    ('manipulation.pick', {'targetRef': obj}, obj),
                    ('verify_grasp', {'objectId': obj}, ''),
                    ('manipulation.place', {'targetRef': dest}, dest),
                    ('verify_placement', {'objectId': obj, 'destinationId': dest}, ''),
                ):
                    identity = uuid.uuid4().hex
                    c = pb.SkillCommand(schema_version='robot.v1', command_id=identity, task_id=task,
                        skill=skill, target_ref=target, robot_id=info.robot_id, catalog_revision=info.catalog_revision,
                        deadline_unix_ms=int(time.time()*1000)+60000, lease_ms=60000,
                        idempotency_key=identity, safety_profile='desktop_standard', approval_id='simulation-eval')
                    ParseDict(params, c.parameters)
                    start = time.monotonic()
                    events = list(stub.ExecuteSkill(c, timeout=65))
                    row = {'skill': skill, 'object': obj, 'destination': dest,
                           'seconds': round(time.monotonic()-start, 3),
                           'events': [MessageToDict(e) for e in events],
                           'passed': bool(events) and events[-1].type == pb.SKILL_EVENT_SUCCEEDED}
                    rows.append(row)
                    print(skill, row['passed'], row['seconds'], flush=True)
                    assert row['passed'], events[-1].message if events else 'no terminal result'
        else:
            base = args.console.rstrip('/')
            token = fetch_live_token(base)
            assert token, 'console session unavailable'
            def request(path, body=None, method='GET'):
                req = Request(base+path, method=method,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={**session_headers(token), 'Content-Type': 'application/json'})
                with urlopen(req, timeout=30) as response:
                    return json.load(response)
            model = request('/v1/config/status')
            report['agentModel'] = {key: model.get(key) for key in ('provider', 'model')}
            task = request('/v1/tasks', {'request': text, 'adapter': 'gazebo'}, 'POST')
            report['taskId'] = task['id']
            request('/v1/tasks/'+task['id']+'/approve', method='POST')
            deadline = time.monotonic()+args.task_timeout
            while time.monotonic() < deadline:
                task = request('/v1/tasks/'+task['id'])
                if task['state'] in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'RECOVERABLE_FAILURE', 'SAFETY_STOPPED'}:
                    break
                time.sleep(.5)
            (output/'task.json').write_text(json.dumps(task, ensure_ascii=False, indent=2)+'\n')
            rows.append({'task': task['id'], 'state': task['state'], 'passed': task['state'] == 'SUCCEEDED'})
            assert task['state'] == 'SUCCEEDED', 'Agent task ended in '+task['state']
            assert any(e['type'] == 'LOCAL_RUN_SUCCEEDED' for e in task.get('events', [])), 'missing run confirmation'
        capture('after')
        if args.oracle_container:
            samples = []
            for _ in range(5):
                samples.append(physics(args.oracle_container))
                time.sleep(.15)
            (output/'physics-after.json').write_text(json.dumps(samples, indent=2)+'\n')
            oracle(samples, goals)
            rows.append({'check': 'independent_physics_placement', 'passed': True})
        report['passed'] = True
    except Exception as error:  # noqa: BLE001 - Persist failed acceptance, including transport failures.
        report['error'] = str(error)
        # Capture failure evidence without retrying any physical command.
        try:
            capture('failure')
            if args.oracle_container:
                (output/'physics-failure.json').write_text(json.dumps(physics(args.oracle_container), indent=2)+'\n')
        except Exception as capture_error:  # noqa: BLE001 - Failure capture must not lose the original failure.
            report['failureCaptureError'] = str(capture_error)
    finally:
        report['finishedAtUnixMs'] = int(time.time()*1000)
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        channel.close()
    print(json.dumps({'passed': report['passed'], 'error': report.get('error'), 'output': str(output)}), flush=True)
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', default='127.0.0.1:50051')
    parser.add_argument('--console', default='http://127.0.0.1:8897')
    parser.add_argument('--mode', choices=['rpc', 'task'], default='task')
    parser.add_argument('--case', choices=CASES, default='sequence')
    parser.add_argument('--task-timeout', type=float, default=180)
    parser.add_argument('--oracle-container')
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(0 if evaluate(parser.parse_args()) else 1)
