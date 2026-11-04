"""Synthetic audit tests only; these fixtures are not robot experiment results."""
import copy
import importlib.util
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('collect_task_audit', HERE / 'collect_task_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture(revision=1):
    task_id, robot = 'synthetic-task', 'synthetic-robot'
    rooms = ['kitchen', 'bedroom', 'bathroom', 'living_room']
    identity = {'mapId':'synthetic-map','mapRevision':'r1','calibrationRevision':'c1'}
    goals = {room:[index+1, 2, 0, 1, 0, 0, 0] for index,room in enumerate(rooms)}
    preflight = {'robotId':robot, 'robotState':{'active_map':identity,
                 'semantic_navigation':{**identity,'frameId':'world','goals':goals}}}
    task = {'id':task_id, 'request':'依次到厨房、卧室、卫生间、客厅，各处独立确认到位。',
            'state':'SUCCEEDED','currentRevision':revision,'approved':True,
            'plan':{'capabilities':{'robotId':robot,'calls':[
                {'tool':'robot.task','legacyIntent':{'routeRooms':[room]}} for room in rooms]}},'events':[]}
    captures = {}
    for index,room in enumerate(rooms):
        navigation = f'{task_id}/revision-{revision}/navigate-{index}'
        for half,tool in enumerate(('navigation.navigate','verify_arrival')):
            number = index*2+half
            step = f'step-{number}'
            execution_step = f'revision-{revision}/{step}' if revision > 1 else step
            command = navigation if half == 0 else f'{task_id}/revision-{revision}/verify-{index}'
            capture = f'capture-{number}'
            payload = {'robotId':robot,'taskRevision':revision,'toolName':tool,'commandId':command,
                       'arguments':{'goalPose':goals[room]}}
            for phase,second in [('SENDING',number*3),('CONFIRMED',number*3+2)]:
                event = {'sequence':len(task['events'])+1,'type':'TOOL_ACTIVITY','stepId':step,
                         'message':'','payload':{**payload,'activityStatus':phase},
                         'occurredAt':f'2026-11-04T00:00:{second:02d}Z'}
                if phase == 'CONFIRMED':
                    event['payload']['receiptObservationId'] = capture
                task['events'].append(event)
            observed = f'2026-11-04T00:00:{number*3+1:02d}Z'
            provenance = {'robotId':robot,'observedAtUnixMs':int(audit.ms(observed)),
                          'frameId':'world','sourceId':'synthetic-camera','sourceType':'rgbd_camera',
                          'sourceFrameId':'camera_optical','transformRevision':'c1'}
            snapshot = {'schemaVersion':'telemetry.v1','robotId':robot,'taskId':task_id,
                        'stepId':execution_step,'taskRevision':revision,'observedAt':observed,
                        'robotProfile':{'robotId':robot},
                        'reconstruction':{'schemaVersion':'scene.reconstruction.v1','observationId':capture,
                                          'sequence':number+1,**provenance},
                        'robotState':{'base_pose':goals[room],
                        'map_route':{**identity,'commandId':navigation,'goalPose':goals[room]}}}
            wire = json.dumps(snapshot,separators=(',',':')).encode()
            metadata = {'id':audit.digest(f'{task_id}/{execution_step}/{capture}'.encode()),
                        'taskId':task_id,'stepId':execution_step,'captureId':capture,
                        'robotId':robot,'taskRevision':revision,'recordIndex':number+1,'expired':False,
                        'captureSequence':number+1,**provenance,
                        'snapshotSha256':audit.digest(wire),'rgbSha256':audit.digest(b'rgb'),
                        'depthSha256':audit.digest(b'depth')}
            captures[(execution_step,capture)] = {'metadata':metadata,'hashChecks':dict(snapshot=True,rgb=True,depth=True),
                                                   'snapshot':snapshot}
    for event_type,payload in [('state.transition',{'state':'SUCCEEDED'}),
                               ('LOCAL_RUN_SUCCEEDED',{'taskRevision':revision}),
                               ('LOCAL_RUN_FINISHED',{'state':'SUCCEEDED','taskRevision':revision})]:
        task['events'].append({'sequence':len(task['events'])+1,'type':event_type,'stepId':'','message':'',
                               'payload':payload,'occurredAt':'2026-11-04T00:01:00Z'})
    return task,rooms,preflight,captures


@pytest.mark.parametrize('revision',[1,2])
def test_four_station_route_and_current_revision(revision):
    task,rooms,preflight,captures = fixture(revision)
    result = audit.patrol_audit(task,rooms,preflight,captures)
    assert result['passed'],result['errors']
    assert len(result['confirmations']) == 8
    assert audit.closure(task)['verified']


@pytest.mark.parametrize('mutation',['wrong_route','wrong_robot','stale_capture','wrong_revision','bad_hash',
                                    'bad_pose','wrong_route_goal','wrong_command','wrong_map','missing_confirmation'])
def test_patrol_rejects_invalid_evidence(mutation):
    task,rooms,preflight,captures = fixture()
    first = next(iter(captures.values()))
    if mutation == 'wrong_route': rooms.reverse()
    if mutation == 'wrong_robot': first['metadata']['robotId'] = 'other'
    if mutation == 'stale_capture': first['metadata']['observedAtUnixMs'] -= 5000
    if mutation == 'wrong_revision': first['metadata']['taskRevision'] += 1
    if mutation == 'bad_hash': first['hashChecks']['depth'] = False
    if mutation == 'bad_pose': first['snapshot']['robotState']['base_pose'] = [50,50,0,1,0,0,0]
    if mutation == 'wrong_route_goal': first['snapshot']['robotState']['map_route']['goalPose'] = [50,50,0,1,0,0,0]
    if mutation == 'wrong_command': first['snapshot']['robotState']['map_route']['commandId'] = 'old-command'
    if mutation == 'wrong_map': preflight['robotState']['active_map'] = {'mapId':'wrong'}
    if mutation == 'missing_confirmation': del task['events'][1]
    assert not audit.patrol_audit(task,rooms,preflight,captures)['passed']


@pytest.mark.parametrize('mutation',['missing_finish','missing_success','wrong_finish_revision','finish_before_transition'])
def test_closure_requires_ordered_finish_for_current_revision(mutation):
    task,*_ = fixture()
    if mutation == 'missing_finish': task['events'].pop()
    if mutation == 'missing_success': task['events'].pop(-2)
    if mutation == 'wrong_finish_revision': task['events'][-1]['payload']['taskRevision'] += 1
    if mutation == 'finish_before_transition': task['events'][-1]['sequence'] = 1
    assert not audit.closure(task)['verified']


def call(call_id='a', **overrides):
    return {'schema_version':'agent.model-call.v1','call_id':call_id,'usage_status':'valid',
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'cached_tokens':10},**overrides}


def test_usage_deduplicates_and_excludes_conflicting_identity():
    first,second = call(),call('b')
    conflicting = call('b',usage={'prompt_tokens':90,'completion_tokens':20,'total_tokens':110})
    result = audit.model_usage([first,first,second,conflicting])
    assert result['providerReportedKnownTotals']['total_tokens'] == 120
    assert result['conflictingCallIDs'] == ['b']
    assert result['duplicateReferencesExcluded'] == 2
    assert not result['totalUsageComplete']


def test_partial_usage_is_valid_missing_fields_are_unknown_not_zero():
    result = audit.model_usage([call(usage={'total_tokens':120})])
    assert result['validUsageCalls'] == 1
    assert result['invalidUsageCalls'] == 0
    assert result['providerReportedKnownTotals']['total_tokens'] == 120
    assert result['providerReportedKnownTotals']['prompt_tokens'] is None
    assert not result['totalUsageComplete']
    assert result['fieldTotalsComplete']['total_tokens']


@pytest.mark.parametrize('usage',[{'prompt_tokens':-1},{'total_tokens':False},
                         {'prompt_tokens':10,'completion_tokens':20,'total_tokens':31},
                         {'prompt_tokens':10,'cached_tokens':11}])
def test_invalid_usage_does_not_enter_total(usage):
    result = audit.model_usage([call(usage=usage)])
    assert result['invalidUsageCalls'] == 1
    assert all(value is None for value in result['providerReportedKnownTotals'].values())


def test_unknown_usage_is_not_zero_or_output_cap():
    result = audit.model_usage([call(usage_status='unavailable',usage=None,output_tokens=512)])
    assert result['unknownUsageCalls'] == 1
    assert result['providerReportedKnownTotals']['total_tokens'] is None
    assert not result['totalUsageComplete']


def test_required_calibration_is_planned_and_confirmed_for_same_robot_revision():
    task,*_ = fixture()
    task['plan']['capabilities']['calls'].insert(0,{'tool':'calibration.get','arguments':{}})
    step={'tool':'calibration.get','status':'CONFIRMED','outcomeUnknown':False,
          'taskRevision':1,'robotId':'synthetic-robot','commandId':'synthetic/calibration'}
    assert audit.required_capabilities(task,{'steps':[step]},['calibration.get'])['passed']
    step['robotId']='other-robot'
    assert not audit.required_capabilities(task,{'steps':[step]},['calibration.get'])['passed']
    assert not audit.required_capabilities(task,{'steps':[]},['calibration.get'])['passed']


def test_snapshot_identity_requires_matching_sensor_and_command_provenance():
    *_,captures = fixture()
    entry = next(iter(captures.values()))
    assert not audit.snapshot_binding_errors(entry['metadata'],entry['snapshot'])
    entry['snapshot']['reconstruction']['observationId'] = 'old-capture'
    entry['snapshot']['robotId'] = 'other-robot'
    errors = audit.snapshot_binding_errors(entry['metadata'],entry['snapshot'])
    assert 'snapshot robotId' in errors
    assert 'reconstruction observationId' in errors


def make_journal(path,task,captures):
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE tasks(id TEXT, request TEXT, state TEXT, current_revision INTEGER, approved INTEGER, plan_json TEXT);
            CREATE TABLE task_events(task_id TEXT, sequence INTEGER, type TEXT, step_id TEXT, message TEXT, payload_json TEXT, occurred_at TEXT);
            CREATE TABLE step_runs(task_id TEXT, step_id TEXT, status TEXT, reconcile_outcome TEXT);
            CREATE TABLE observation_evidence(task_id TEXT, record_index INTEGER, metadata_json TEXT, snapshot_json BLOB, rgb BLOB, depth BLOB, expired INTEGER);''')
        for current_id in (task['id'],'other-task-do-not-export'):
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',(current_id,task['request'],task['state'],task['currentRevision'],1,json.dumps(task['plan'])))
        for event in task['events']:
            db.execute('INSERT INTO task_events VALUES(?,?,?,?,?,?,?)',(task['id'],event['sequence'],event['type'],event['stepId'],event['message'],json.dumps(event['payload']),event['occurredAt']))
        for entry in captures.values():
            record = entry['metadata']
            db.execute('INSERT INTO observation_evidence VALUES(?,?,?,?,?,?,?)',(task['id'],record['recordIndex'],json.dumps(record),json.dumps(entry['snapshot'],separators=(',',':')).encode(),b'rgb',b'depth',0))


def test_journal_is_task_scoped_read_only_and_hashes_bytes(tmp_path):
    task,rooms,preflight,captures = fixture()
    path = tmp_path/'journal.sqlite'
    make_journal(path,task,captures)
    before = path.read_bytes()
    output = tmp_path/'out'; output.mkdir()
    result,archived = audit.read_journal(path,task,output)
    assert result['exactMatch'] and result['allDeclaredCaptureHashesValid']
    assert before == path.read_bytes()
    assert 'other-task-do-not-export' not in (output/'journal-task.json').read_text()
    assert len(list(output.glob('*-snapshot.json'))) == 8
    assert not list(output.glob('*.png'))
    assert audit.patrol_audit(task,rooms,preflight,archived)['passed']


def test_undeclared_images_are_not_corrupt_but_do_not_satisfy_patrol(tmp_path):
    task,rooms,preflight,captures = fixture()
    path = tmp_path/'journal.sqlite'; make_journal(path,task,captures)
    record = dict(next(iter(captures.values()))['metadata'])
    record.pop('rgbSha256'); record.pop('depthSha256')
    record.update(rgbBytes=0,depthBytes=0)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE observation_evidence SET metadata_json=?,rgb=NULL,depth=NULL WHERE record_index=1',
                   (json.dumps(record),))
    output = tmp_path/'out'; output.mkdir()
    result,archived = audit.read_journal(path,task,output)
    assert result['allDeclaredCaptureHashesValid']
    assert result['capturesWithCompleteRGBD'] == 7
    assert next(iter(archived.values()))['hashChecks']['rgb'] is None
    assert not audit.patrol_audit(task,rooms,preflight,archived)['passed']


def test_journal_rejects_path_traversal_and_does_not_write_outside_output(tmp_path):
    task,_,_,captures = fixture()
    next(iter(captures.values()))['metadata']['id'] = '../escape'
    path = tmp_path/'journal.sqlite'; make_journal(path,task,captures)
    output = tmp_path/'out'; output.mkdir()
    result,_ = audit.read_journal(path,task,output,images=True)
    assert not result['exactMatch']
    assert not (tmp_path/'escape-snapshot.json').exists()
    assert not (tmp_path/'escape-rgb.png').exists()


@pytest.mark.parametrize('url',['https://127.0.0.1:8000','http://example.org:8000',
                               'http://127.0.0.1','http://token@127.0.0.1:8000',
                               'http://127.0.0.1:8000/secret','http://127.0.0.1:8000?key=secret'])
def test_reader_rejects_nonlocal_origins_and_credentials(url):
    with pytest.raises(ValueError): audit.Reader(url)


def test_reader_only_gets_and_does_not_follow_redirects():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            if self.path == '/redirect':
                self.send_response(302); self.send_header('Location','/should-not-read'); self.end_headers()
            else:
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
        def log_message(self,*args): pass
    server = HTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        reader = audit.Reader(f'http://127.0.0.1:{server.server_port}')
        assert reader.get('/task') == {'ok':True}
        assert reader.get('/redirect')['collectorError'] == 'ValueError'
        assert seen == ['/task','/redirect']
        assert all(item['method'] == 'GET' for item in reader.log)
    finally:
        server.shutdown(); server.server_close(); thread.join()
