#!/usr/bin/env python3
"""GET-only task audit. Never creates, approves, retries, resumes or stops tasks.

Reads only explicit evidence inputs and optional task-scoped SQLite rows. Does
not inspect credentials/config/environment. Requires a new output directory.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import ipaddress
import json
import math
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TERMINAL = {'SUCCEEDED', 'FAILED', 'RECOVERABLE_FAILURE', 'CANCELLED', 'SAFETY_STOPPED'}
ROOMS = {'厨房': 'kitchen', '卧室': 'bedroom', '卫生间': 'bathroom', '浴室': 'bathroom', '客厅': 'living_room'}
CALL_FIELDS = ('call_id', 'stage', 'model', 'started_at', 'duration_ms', 'http_status', 'outcome',
               'finish_reason', 'usage_status', 'usage', 'output_tokens', 'cache_mode', 'request_bytes', 'response_bytes')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def utc():
    return dt.datetime.now(dt.UTC).isoformat()


def ms(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000


def validate_base(value):
    parsed = urllib.parse.urlsplit(value)
    try:
        loopback = ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        loopback = parsed.hostname == 'localhost'
    if (parsed.scheme != 'http' or not loopback or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path not in ('', '/') or not parsed.port):
        raise ValueError('base URL must be an explicit HTTP loopback origin with port and no credentials/path/query')
    return value.rstrip('/')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('read-only collector does not follow redirects')


class Reader:
    def __init__(self, base):
        self.base = validate_base(base)
        self.log = []
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(self, path):
        start = time.monotonic()
        item = {'method': 'GET', 'path': path, 'atUTC': utc()}
        try:
            with self.opener.open(urllib.request.Request(self.base + path, method='GET'), timeout=15) as response:
                wire = response.read(64 * 1024 * 1024 + 1)
                if len(wire) > 64 * 1024 * 1024:
                    raise ValueError('response exceeds 64 MiB collector limit')
                value = json.loads(wire)
                item.update(status=response.status, responseSHA256=digest(wire), responseBytes=len(wire))
                return value
        except urllib.error.HTTPError as error:
            item['status'] = error.code
            return {'collectorError': 'HTTP_ERROR', 'status': error.code}
        except (OSError, ValueError) as error:
            item['errorType'] = type(error).__name__
            return {'collectorError': type(error).__name__}
        finally:
            item['durationMs'] = round((time.monotonic() - start) * 1000, 3)
            self.log.append(item)


def closure(task):
    events = task.get('events', [])
    state, revision = task.get('state'), task.get('currentRevision')
    transitions = [e for e in events if e.get('type') in {'STATE_CHANGED', 'state.transition'}
                   and (e.get('payload') or {}).get('state') == state]
    transition = max((e.get('sequence', 0) for e in transitions), default=0)
    ends = [e for e in events if e.get('type') == 'LOCAL_RUN_FINISHED'
            and (e.get('payload') or {}).get('state') == state
            and (e.get('payload') or {}).get('taskRevision') == revision
            and e.get('sequence', 0) > transition > 0]
    success = any(e.get('type') == 'LOCAL_RUN_SUCCEEDED' and
                  (e.get('payload') or {}).get('taskRevision') == revision for e in events)
    return {'verified': state in TERMINAL and bool(ends) and (state != 'SUCCEEDED' or success),
            'state': state, 'taskRevision': revision, 'terminalTransitionSequence': transition,
            'finishSequences': [e['sequence'] for e in ends], 'successMarkerPresent': success}


def model_usage(value):
    variants, references = {}, collections.Counter()
    missing_ids = 0

    def walk(item):
        nonlocal missing_ids
        if isinstance(item, dict):
            if item.get('schema_version') == 'agent.model-call.v1':
                call_id = item.get('call_id')
                if isinstance(call_id, str) and call_id:
                    projected = {key: item[key] for key in CALL_FIELDS if key in item}
                    canonical = json.dumps(projected, sort_keys=True, allow_nan=False)
                    variants.setdefault(call_id, {})[canonical] = projected
                    references[call_id] += 1
                else:
                    missing_ids += 1
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        # Text that happens to contain old serialized model records is not reparsed.

    walk(value)
    conflicts = sorted(key for key, values in variants.items() if len(values) != 1)
    calls = [next(iter(values.values())) for values in variants.values() if len(values) == 1]
    totals = {key: 0 for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'cached_tokens', 'cache_write_tokens')}
    reported = collections.Counter()
    valid = unknown = invalid = 0
    for call in calls:
        usage = call.get('usage')
        if call.get('usage_status') != 'valid' or not isinstance(usage, dict):
            unknown += 1
            continue
        core = ('prompt_tokens', 'completion_tokens', 'total_tokens')
        if (not any(key in usage for key in totals)
                or any(type(usage[key]) is not int or usage[key] < 0 for key in totals if key in usage)
                or (all(key in usage for key in core)
                    and usage['total_tokens'] != usage['prompt_tokens'] + usage['completion_tokens'])
                or (all(key in usage for key in ('cached_tokens', 'prompt_tokens'))
                    and usage['cached_tokens'] > usage['prompt_tokens'])):
            invalid += 1
            continue
        valid += 1
        for key in totals:
            if key in usage:
                totals[key] += usage[key]
                reported[key] += 1
    complete = {key: bool(calls) and reported[key] == len(calls) and not (conflicts or missing_ids)
                for key in totals}
    return {'uniqueCallIDs': len(variants), 'references': sum(references.values()),
            'duplicateReferencesExcluded': sum(references.values()) - len(variants),
            'conflictingCallIDs': conflicts, 'missingIdentityRecords': missing_ids,
            'validUsageCalls': valid, 'unknownUsageCalls': unknown, 'invalidUsageCalls': invalid,
            'providerReportedKnownTotals': {key: value if reported[key] else None for key, value in totals.items()},
            'fieldReportingCalls': {key: reported[key] for key in totals},
            'fieldTotalsComplete': complete,
            'totalUsageComplete': all(complete[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')),
            'calls': sorted(calls, key=lambda c: (c.get('started_at', ''), c['call_id'])),
            'boundary': 'Provider-reported telemetry, not an invoice. Unknown/conflicting usage excluded, never zero. Cached tokens are included in prompt tokens; no token estimation.'}


def event_view(event):
    return {key: event.get(key, default) for key, default in (
        ('sequence', None), ('type', ''), ('stepId', ''), ('message', ''), ('payload', None), ('occurredAt', ''))}


def snapshot_binding_errors(record, snapshot):
    """A valid byte hash alone does not prove a capture belongs to this command."""
    errors = []
    if snapshot.get('schemaVersion') != 'telemetry.v1':
        errors.append('snapshot schema')
    for key in ('taskId', 'stepId', 'taskRevision', 'robotId'):
        if snapshot.get(key) != record.get(key):
            errors.append('snapshot ' + key)
    reconstruction = snapshot.get('reconstruction') or {}
    if reconstruction.get('schemaVersion') != 'scene.reconstruction.v1':
        errors.append('reconstruction schema')
    for source, destination in [('observationId', 'captureId'), ('sequence', 'captureSequence')]:
        if reconstruction.get(source) != record.get(destination):
            errors.append('reconstruction ' + source)
    for key in ('robotId', 'observedAtUnixMs', 'frameId', 'sourceId', 'sourceType', 'sourceFrameId', 'transformRevision'):
        if reconstruction.get(key) != record.get(key):
            errors.append('reconstruction ' + key)
    if (snapshot.get('robotProfile') or {}).get('robotId') != record.get('robotId'):
        errors.append('snapshot robot profile')
    try:
        if abs(ms(snapshot['observedAt']) - record['observedAtUnixMs']) > .01:
            errors.append('snapshot observed timestamp')
    except (KeyError, TypeError, ValueError):
        errors.append('snapshot observed timestamp missing or invalid')
    return errors


def read_journal(path, task, output, images=False):
    """One read transaction; export only this task, no live database writes."""
    records, errors, rows_by_capture = [], [], {}
    uri = path.resolve(strict=True).as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=10) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
        if row is None:
            return {'available': True, 'exactMatch': False, 'errors': ['task absent from SQLite']}, {}
        fields = {'id': row['id'], 'request': row['request'], 'state': row['state'],
                  'currentRevision': row['current_revision'], 'approved': bool(row['approved']),
                  'plan': json.loads(row['plan_json'])}
        events = [dict(sequence=r['sequence'], type=r['type'], stepId=r['step_id'], message=r['message'],
                       payload=json.loads(r['payload_json']), occurredAt=r['occurred_at'])
                  for r in db.execute('SELECT * FROM task_events WHERE task_id=? ORDER BY sequence', (task['id'],))]
        runs = [dict(r) for r in db.execute('SELECT step_id,status,reconcile_outcome FROM step_runs WHERE task_id=? ORDER BY step_id', (task['id'],))]
        for row in db.execute('SELECT record_index,metadata_json,snapshot_json,rgb,depth,expired FROM observation_evidence WHERE task_id=? ORDER BY record_index', (task['id'],)):
            record = json.loads(row['metadata_json'])
            record.update(recordIndex=row['record_index'], expired=bool(row['expired']))
            safe_id = isinstance(record.get('id'), str) and re.fullmatch(r'[0-9a-f]{64}', record['id'])
            if not safe_id:
                errors.append('invalid evidence file identity')
            entry = {'metadata': record, 'hashChecks': {}}
            for name, column, extension in [('snapshot', 'snapshot_json', '.json'), ('rgb', 'rgb', '.png'), ('depth', 'depth', '.png')]:
                data = row[column]
                if isinstance(data, str):
                    data = data.encode()
                valid = isinstance(data, bytes) and bool(data) and digest(data) == record.get(name + 'Sha256')
                undeclared_image = (name != 'snapshot' and not data and not record.get(name+'Sha256')
                                    and record.get(name+'Bytes', 0) == 0)
                entry['hashChecks'][name] = None if undeclared_image else valid
                if name == 'snapshot' and valid and safe_id:
                    entry['snapshot'] = json.loads(data)
                    entry['identityErrors'] = snapshot_binding_errors(record, entry['snapshot'])
                    if entry['identityErrors']:
                        errors.append('capture snapshot identity mismatch: ' + record['id'])
                    # Preserve exact bytes so snapshot checksum is reproducible.
                    file = output / (record['id'] + '-snapshot.json')
                    file.write_bytes(data)
                    entry['snapshotFile'] = file.name
                elif images and valid and safe_id:
                    file = output / (record['id'] + '-' + name + extension)
                    file.write_bytes(data)
                    entry[name + 'File'] = file.name
            records.append(entry)
            key = (record.get('stepId'), record.get('captureId'))
            if key in rows_by_capture:
                errors.append('duplicate step/capture identity')
            rows_by_capture[key] = entry
    fields_equal = all(task.get(key) == value for key, value in fields.items())
    events_equal = [event_view(e) for e in task.get('events', [])] == [event_view(e) for e in events]
    exported = {'task': fields, 'events': events, 'stepRuns': runs, 'observations': records}
    save(output / 'journal-task.json', exported)
    return {'available': True, 'exactMatch': fields_equal and events_equal and not errors,
            'fieldsEqual': fields_equal, 'eventsEqual': events_equal, 'eventCount': len(events),
            'captureCount': len(records),
            'allDeclaredCaptureHashesValid': all(all(value is not False for value in x['hashChecks'].values()) for x in records) if records else None,
            'capturesWithCompleteRGBD': sum(all(value is True for value in x['hashChecks'].values()) for x in records),
            'hashCheckBoundary': 'Null image hash check means no image or digest declared in this capture, not corrupt bytes; patrol arrival requires complete snapshot/RGB/depth checks.',
            'errors': errors, 'boundary': 'Task-scoped SQLite read transaction, not whole-database backup; image bytes saved only with --images.'}, rows_by_capture


def pose_error(a, b):
    for pose in (a, b):
        if not isinstance(pose, list) or len(pose) != 7 or any(type(x) not in (int, float) or not math.isfinite(x) for x in pose):
            raise ValueError('pose must have seven finite numbers')
        if abs(sum(x * x for x in pose[3:]) - 1) > .001:
            raise ValueError('pose quaternion not normalized')
    def yaw(pose):
        w, x, y, z = pose[3:]
        return math.atan2(2 * (w*z+x*y), 1-2*(y*y+z*z))
    angle = yaw(a)-yaw(b)
    return math.hypot(a[0]-b[0], a[1]-b[1]), abs(math.atan2(math.sin(angle), math.cos(angle)))


def patrol_audit(task, expected, preflight, captures):
    errors, confirmations = [], []
    if not expected:
        return {'performed': False, 'passed': None, 'reason': 'no pre-registered room sequence supplied'}
    expected = [ROOMS.get(name, name) for name in expected]
    plan = (task.get('plan') or {}).get('capabilities') or {}
    robot, revision = plan.get('robotId'), task.get('currentRevision', 0)
    route = []
    for call in plan.get('calls', []):
        if call.get('tool') == 'robot.task':
            rooms = (call.get('legacyIntent') or {}).get('routeRooms')
            if not isinstance(rooms, list) or not rooms:
                errors.append('robot.task missing frozen routeRooms')
            else:
                route.extend(ROOMS.get(room, room) for room in rooms)
    if route != expected:
        errors.append('frozen route differs from expected room sequence')
    telemetry = preflight.get('latest', preflight)
    state = telemetry.get('robotState') or {}
    active, semantic = state.get('active_map') or {}, state.get('semantic_navigation') or {}
    targets = semantic.get('goals') or {}
    identity = ('mapId', 'mapRevision', 'calibrationRevision')
    if telemetry.get('robotId') != robot or not robot:
        errors.append('preflight robot identity missing or different')
    if not all(active.get(key) and active.get(key) == semantic.get(key) for key in identity):
        errors.append('preflight map/semantic identity mismatch')
    if semantic.get('frameId') != 'world':
        errors.append('preflight target frame is not world')
    sends = {}
    for event in task.get('events', []):
        payload = event.get('payload') or {}
        if event.get('type') != 'TOOL_ACTIVITY':
            continue
        key = (event.get('stepId'), payload.get('commandId'), payload.get('taskRevision'), payload.get('robotId'))
        if payload.get('activityStatus') == 'SENDING':
            sends[key] = event
        if payload.get('activityStatus') == 'CONFIRMED' and payload.get('toolName') in {'navigation.navigate', 'verify_arrival'}:
            confirmations.append((event, key))
    if len(confirmations) != len(expected) * 2:
        errors.append(f'expected {len(expected)*2} navigation/arrival confirmations, got {len(confirmations)}')
    results, seen_commands, seen_captures = [], set(), set()
    for index, (event, key) in enumerate(confirmations):
        payload = event['payload']
        room = expected[index//2] if index//2 < len(expected) else 'unexpected'
        tool = 'navigation.navigate' if index % 2 == 0 else 'verify_arrival'
        capture = payload.get('receiptObservationId')
        step, command = key[:2]
        entry = {'room': room, 'tool': payload.get('toolName'), 'stepId': step, 'commandId': command,
                 'captureId': capture, 'eventSequence': event['sequence'], 'errors': []}
        results.append(entry)
        if payload.get('toolName') != tool or payload.get('robotId') != robot or payload.get('taskRevision') != revision:
            entry['errors'].append('tool/robot/revision differs from expected execution')
        if not isinstance(command, str) or not command.startswith(task['id']+'/') or command in seen_commands:
            entry['errors'].append('missing or reused command identity')
        if not capture or capture in seen_captures:
            entry['errors'].append('missing or reused capture identity')
        seen_commands.add(command)
        seen_captures.add(capture)
        execution_step = f'revision-{revision}/{step}' if revision > 1 else step
        record = captures.get((execution_step, capture)) or captures.get((step, capture))
        if not record:
            entry['errors'].append('no matching persisted capture')
        else:
            metadata = record['metadata']
            if metadata.get('expired') or metadata.get('taskId') != task['id'] or metadata.get('robotId') != robot or metadata.get('taskRevision') != revision:
                entry['errors'].append('capture identity differs or expired')
            if record.get('identityErrors'):
                entry['errors'].append('snapshot content differs from capture metadata')
            if metadata.get('transformRevision') != active.get('calibrationRevision'):
                entry['errors'].append('capture transform differs from preflight calibration')
            if not all(record['hashChecks'].values()):
                entry['errors'].append('capture snapshot/RGB/depth checksum missing or invalid')
            try:
                observed = metadata['observedAtUnixMs']
                if not ms(sends[key]['occurredAt']) <= observed <= ms(event['occurredAt']):
                    entry['errors'].append('capture not after its own command and before confirmation')
                observation = record['snapshot']['robotState']
                target = targets[room]
                distance, angle = pose_error(observation['base_pose'], target)
                entry.update(positionErrorM=distance, yawErrorRad=angle)
                if distance > .08 or angle > .15:
                    entry['errors'].append('recorded pose outside 0.08m/0.15rad arrival tolerance')
                d, a = pose_error((payload.get('arguments') or {}).get('goalPose'), target)
                if d > 1e-6 or a > 1e-6:
                    entry['errors'].append('command target differs from preflight semantic target')
                route_state = observation.get('map_route') or {}
                if any(route_state.get(k) != active.get(k) for k in identity):
                    entry['errors'].append('map-route identity differs from preflight')
                navigation_command = command if tool == 'navigation.navigate' else results[-2]['commandId']
                if route_state.get('commandId') != navigation_command:
                    entry['errors'].append('map-route does not reference this station navigation')
                d, a = pose_error(route_state.get('goalPose'), target)
                if d > 1e-6 or a > 1e-6:
                    entry['errors'].append('map-route target differs from preflight semantic target')
            except (KeyError, TypeError, ValueError, IndexError) as error:
                entry['errors'].append('cannot evaluate capture/route: ' + type(error).__name__)
        errors.extend(f'{room} {tool}: {message}' for message in entry['errors'])
    return {'performed': True, 'passed': not errors, 'expectedRooms': expected, 'frozenRooms': route,
            'confirmations': results, 'errors': errors,
            'boundary': 'Recorded odometry/map-route and raw sensor identity, not independent ground truth, collision/contact proof or semantic image interpretation.'}


def required_capabilities(task, trace, required):
    plan = (task.get('plan') or {}).get('capabilities') or {}
    planned = [call.get('tool') for call in plan.get('calls', [])]
    checks = []
    for name in required:
        confirmed = [step for step in trace.get('steps', []) if step.get('tool') == name
                     and step.get('status') == 'CONFIRMED' and not step.get('outcomeUnknown')
                     and step.get('taskRevision') == task.get('currentRevision')
                     and step.get('robotId') == plan.get('robotId')]
        checks.append({'tool':name, 'presentInFrozenPlan':name in planned,
                       'confirmedCommandIds':[step.get('commandId') for step in confirmed],
                       'passed':name in planned and bool(confirmed)})
    return {'required':required, 'checks':checks, 'passed':all(check['passed'] for check in checks)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--journal', type=Path)
    parser.add_argument('--preflight-telemetry', type=Path)
    parser.add_argument('--expected-room', action='append', default=[])
    parser.add_argument('--required-capability', action='append', default=[])
    parser.add_argument('--images', action='store_true')
    parser.add_argument('--wait-seconds', type=float, default=0)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.task_id) or not 0 <= args.wait_seconds <= 3600:
        parser.error('plain task identity and wait between 0 and 3600 seconds required')
    reader = Reader(args.base_url)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'collector-used.py').write_bytes(Path(__file__).read_bytes())
    root = '/v1/tasks/' + args.task_id
    polls, deadline = [], time.monotonic() + args.wait_seconds
    task = reader.get(root)
    while True:
        current = closure(task)
        polls.append({'atUTC': utc(), 'state': task.get('state'), 'events': len(task.get('events', [])), 'closure': current})
        if current['verified'] or time.monotonic() >= deadline or task.get('collectorError'):
            break
        time.sleep(min(2, max(0, deadline-time.monotonic())))
        task = reader.get(root)
    save(args.output / 'task-before.json', task)
    data = {}
    telemetry_path = '/v1/telemetry'
    if task.get('adapter'):
        telemetry_path += '?' + urllib.parse.urlencode({'adapter': task['adapter']})
    for name, path in [('trace',root+'/trace'), ('experience',root+'/experience'), ('recovery',root+'/recovery'),
                       ('telemetry',telemetry_path), ('runtime','/v1/runtime'), ('metrics','/v1/orchestration/metrics')]:
        data[name] = reader.get(path)
        save(args.output / (name+'.json'), data[name])
    records, cursor, pagination_errors = [], 0, []
    for page in range(100):
        value = reader.get(root+f'/observations?limit=200&before={cursor}')
        save(args.output / f'observations-page-{page+1:03d}.json', value)
        if value.get('collectorError') or value.get('taskId') != args.task_id:
            pagination_errors.append('observation page unavailable or wrong task')
            break
        batch = value.get('records', [])
        records.extend(batch)
        following = value.get('nextBefore')
        if following is None:
            break
        if type(following) is not int or following <= 0 or (cursor and following >= cursor):
            pagination_errors.append('observation cursor did not move backward')
            break
        cursor = following
    else:
        pagination_errors.append('collector observation page limit reached')
    observations = {'taskId': args.task_id, 'records': records, 'paginationErrors': pagination_errors}
    save(args.output / 'observations.json', observations)
    journal, captures = {'available': False, 'exactMatch': None}, {}
    if args.journal and task.get('id') == args.task_id:
        try:
            journal, captures = read_journal(args.journal, task, args.output, args.images)
        except (OSError, sqlite3.Error, ValueError) as error:
            journal = {'available': False, 'errorType': type(error).__name__, 'exactMatch': False}
    after = reader.get(root)
    save(args.output / 'task-after.json', after)
    preflight = json.loads(args.preflight_telemetry.read_text()) if args.preflight_telemetry else {}
    if args.preflight_telemetry:
        save(args.output / 'preflight-telemetry.json', preflight)
    patrol = patrol_audit(task, args.expected_room, preflight, captures)
    trace = data['trace']
    trace_matches = (trace.get('taskId') == args.task_id and trace.get('source', {}).get('stable') is True
                     and trace.get('events') == task.get('events') and trace.get('state') == task.get('state')
                     and trace.get('currentRevision') == task.get('currentRevision')
                     and trace.get('currentRequest') == task.get('request'))
    original = trace.get('originalRequest') or {}
    original_hash_valid = (isinstance(original.get('text'), str) and bool(original['text'])
                           and digest(original['text'].encode()) == original.get('sha256'))
    required = required_capabilities(task, trace, args.required_capability)
    trace_integrity = trace.get('integrity', {}).get('status')
    capture_index = {(r.get('stepId'),r.get('captureId')):r for r in records}
    metadata_equal = (len(capture_index) == len(records) == len(captures) and
                      all(all(capture_index.get(key, {}).get(field) == entry['metadata'].get(field)
                              for field in ('id','taskId','taskRevision','robotId','stepId','captureId','observedAtUnixMs','snapshotSha256','rgbSha256','depthSha256','expired'))
                          for key, entry in captures.items())) if args.journal else None
    usage = model_usage({'task': task, 'trace': trace})
    failures = []
    for passed, label in [(task.get('id') == args.task_id,'task identity'), (task == after,'API snapshot stable'),
                          (closure(task)['verified'],'explicit execution closure'), (trace_matches,'trace/task matching'),
                          (original_hash_valid,'original natural-language request hash'),
                          (trace_integrity == 'consistent','trace integrity'), (not pagination_errors,'complete observation index'),
                          (journal.get('exactMatch') is True,'SQLite task/events matching'), (metadata_equal is True,'API/SQLite observation identity')]:
        if not passed:
            failures.append(label)
    rpc = trace.get('rpcCalls', [])
    steps = trace.get('steps', [])
    result = {'schemaVersion':'user.simulation.audit.v1', 'taskId':args.task_id, 'collectedAtUTC':utc(),
              'taskState':task.get('state'), 'taskSucceeded':task.get('state') == 'SUCCEEDED',
              'closure':closure(task), 'apiStable':task == after, 'traceMatchesTask':trace_matches,
              'originalRequestSHA256Valid':original_hash_valid, 'requiredCapabilities':required,
              'traceIntegrity':trace_integrity, 'traceFindings':trace.get('integrity',{}).get('findings',[]),
              'journal':journal, 'observationMetadataMatches':metadata_equal, 'observationCount':len(records),
              'rpcCount':len(rpc), 'rpcStates':dict(collections.Counter(r.get('status','UNKNOWN') for r in rpc)),
              'stepCount':len(steps), 'stepStates':dict(collections.Counter(s.get('status','UNKNOWN') for s in steps)),
              'atomicSteps':[{'stepId':s.get('stepId'),'commandId':s.get('commandId'), 'tool':s.get('tool'),
                              'status':s.get('status'),'robotId':s.get('robotId'),'taskRevision':s.get('taskRevision'),
                              'outcomeUnknown':s.get('outcomeUnknown'),'evidenceIds':s.get('evidenceIds')} for s in steps],
              'modelUsage':usage, 'patrol':patrol, 'auditFailures':failures, 'recordAuditPassed':not failures,
              'acceptancePassed':not failures and task.get('state') == 'SUCCEEDED' and patrol['passed'] is True and required['passed'],
              'scope':'GET-only collection plus optional task-scoped SQLite read. No robot commands. Physical acceptance requires explicitly registered route and independent evidence; no generic safety or collision guarantee.'}
    save(args.output / 'summary.json', result)
    save(args.output / 'polls.json', polls)
    save(args.output / 'http-get-log.json', reader.log)
    save(args.output / 'manifest.json', {'collectorSHA256':digest(Path(__file__).read_bytes()),
         'files':{p.name:{'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size} for p in sorted(args.output.iterdir()) if p.is_file()}})
    print(json.dumps({'output':str(args.output),'taskState':result['taskState'],'recordAuditPassed':result['recordAuditPassed'],
                      'acceptancePassed':result['acceptancePassed'],'auditFailures':failures,'patrolErrors':patrol.get('errors',[])},ensure_ascii=False))
    return 0 if result['acceptancePassed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
