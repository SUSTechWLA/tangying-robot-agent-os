"""Local Gazebo calibration, manual RGB-D map publication and reload acceptance.

Uses production service RPCs. Run in a disposable scene namespace with at least
0.6 m of clear space behind the workcell. A saved local scan is not house coverage.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

import grpc
from google.protobuf.json_format import MessageToDict, ParseDict
from tangying_robot_proto.robot.v1 import robot_pb2 as pb
from tangying_robot_proto.robot.v1 import robot_pb2_grpc as rpc


def evaluate(address, output):
    output.mkdir(parents=True, exist_ok=False)
    stub = rpc.RobotRuntimeStub(grpc.insecure_channel(address))
    info = stub.GetRuntimeInfo(pb.GetRuntimeInfoRequest(), timeout=10)
    assert info.adapter == 'gazebo'
    report = {'schemaVersion': 'gazebo.workflow_acceptance.v1', 'robotId': info.robot_id,
              'scope': 'calibration_manual_local_map_publish_reload', 'wholeHouseCoverage': False,
              'calls': [], 'passed': False}

    def call(name, parameters=None):
        request = pb.ServiceRequest(robot_id=info.robot_id, name=name, request_id=uuid.uuid4().hex)
        ParseDict(parameters or {}, request.parameters)
        start = time.monotonic()
        result = stub.CallService(request, timeout=20)
        row = {'service': name, 'parameters': parameters or {},
               'seconds': round(time.monotonic()-start, 3), 'response': MessageToDict(result)}
        report['calls'].append(row)
        assert result.ok, (name, result.code, result.message)
        return MessageToDict(result.result)

    def wait(allowed, timeout=90):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            state = call('mapping.status')
            if state['state'] in allowed:
                return state
            assert state['state'] != 'failed', state.get('message')
            time.sleep(.5)
        raise AssertionError('workflow timeout')

    try:
        calibration = call('calibration.get')
        assert calibration['available'] and calibration['revision']
        derived = call('calibration.run')
        assert derived['revision'] == calibration['revision'], 'derived calibration drift'
        call('calibration.save', {'document': derived['document'],
                                 'expectedRevision': derived['revision'], 'algorithm': 'simulation-derived'})
        call('mapping.start', {'name': 'Gazebo local workflow acceptance', 'mode': 'manual'})
        wait({'recording'})
        for action, parameters in [('backward', {'distanceM': .3}),
                                   ('turn_left', {'angleRad': .35}),
                                   ('backward', {'distanceM': .2}),
                                   ('turn_right', {'angleRad': .35})]:
            call('mapping.move', {'action': action, **parameters})
            wait({'recording'})
        scan = call('mapping.status')
        assert scan['travelledM'] >= .35, 'insufficient measured travel'
        call('mapping.finish')
        saved = wait({'completed'}, timeout=120)
        assert saved['mapId'] and saved['activeMap'], 'map not published/activated'
        call('mapping.inventory')
        call('mapping.activate', {'mapId': saved['mapId']})
        call('navigation.map')
        call('mapping.conflicts')
        call('mapping.start', {'name': 'cancel acceptance', 'mode': 'manual'})
        wait({'recording'})
        call('mapping.cancel')
        wait({'cancelled', 'idle'})
        report.update(passed=True, measuredTravelM=scan['travelledM'], mapId=saved['mapId'])
    except (AssertionError, ValueError, KeyError, OSError, grpc.RpcError) as error:
        report['error'] = str(error)
        try:
            call('mapping.cancel')
        except (AssertionError, OSError, grpc.RpcError) as cleanup_error:
            report['cleanupError'] = str(cleanup_error)
    finally:
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'calls'}, ensure_ascii=False))
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', default='127.0.0.1:50051')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if evaluate(args.runtime, args.output) else 1)
