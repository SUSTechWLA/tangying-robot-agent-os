#!/usr/bin/env python3
"""Read-only task closure/usage audit. GET only; never approve, resume, retry or reconcile.

Reads explicit task evidence files and the supplied local journal through a
read-only SQLite connection. Online backup is written only to a new output dir.
No console-session, environment, config, headers, or credential files are read.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
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
ALLOWED_TABLES = {'tasks', 'task_events', 'step_runs', 'task_revisions', 'task_revision_events',
                 'fleet_graph_states', 'fleet_domain_events', 'fleet_outbox', 'fleet_checkpoints',
                 'observation_evidence', 'sqlite_sequence'}
CALL_FIELDS = ('call_id', 'stage', 'model', 'started_at', 'duration_ms', 'http_status', 'outcome',
               'finish_reason', 'usage_status', 'usage', 'output_tokens', 'cache_mode',
               'request_bytes', 'response_bytes')


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unix_ms(value):
    return dt.datetime.fromisoformat(value).timestamp() * 1000


def closure_status(task):
    state, revision = task.get('state'), task.get('currentRevision')
    markers = [e for e in task.get('events', []) if e.get('type') == 'LOCAL_RUN_FINISHED']
    terminal_sequences = [e.get('sequence', 0) for e in task.get('events', [])
                          if e.get('type') == 'state.transition' and (e.get('payload') or {}).get('state') == state]
    latest_terminal = max(terminal_sequences, default=0)
    matching = [e for e in markers if e.get('payload', {}).get('state') == state
                and e.get('payload', {}).get('taskRevision') == revision
                and type(e.get('sequence')) is int and e['sequence'] > latest_terminal > 0]
    success_marker = any(e.get('type') == 'LOCAL_RUN_SUCCEEDED'
                         and e.get('payload', {}).get('taskRevision') == revision for e in task.get('events', []))
    if state not in TERMINAL:
        status = 'not_terminal'
    elif not markers:
        status = 'legacy_no_explicit_finish'
    elif not matching:
        status = 'finish_state_or_revision_mismatch'
    elif state == 'SUCCEEDED' and not success_marker:
        status = 'success_marker_missing'
    else:
        status = 'explicit_run_finished'
    return {'status': status, 'verified': status == 'explicit_run_finished',
            'state': state, 'revision': revision,
            'matchingFinishSequences': [e.get('sequence') for e in matching],
            'successMarkerPresent': success_marker}


def model_usage(task):
    unique, references, conflicts = {}, collections.Counter(), []

    def visit(value):
        if isinstance(value, dict):
            if value.get('schema_version') == 'agent.model-call.v1':
                call_id = value.get('call_id')
                if not isinstance(call_id, str) or not call_id:
                    conflicts.append('model call without identity')
                else:
                    projection = {k: value[k] for k in CALL_FIELDS if k in value}
                    if call_id in unique and unique[call_id] != projection:
                        conflicts.append('conflicting repeated call identity: ' + call_id)
                    unique[call_id] = projection
                    references[call_id] += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        # Embedded text/context snapshots are not parsed as new calls.

    visit(task)
    totals = dict.fromkeys(('prompt_tokens', 'completion_tokens', 'total_tokens', 'cached_tokens'), 0)
    valid, unknown, invalid = 0, 0, 0
    for call in unique.values():
        usage = call.get('usage')
        if call.get('usage_status') != 'valid' or not isinstance(usage, dict):
            unknown += 1
            continue
        values = [usage.get(k) for k in totals]
        if (any(type(v) is not int or v < 0 for v in values)
                or usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens']
                or usage['cached_tokens'] > usage['prompt_tokens']):
            invalid += 1
            continue
        valid += 1
        for k in totals:
            totals[k] += usage[k]
    calls = sorted(unique.values(), key=lambda c: (c.get('started_at', ''), c['call_id']))
    trace = task.get('plan', {}).get('capabilities', {}).get('planningTrace', [])
    statuses = collections.Counter(str(c.get('http_status', 'missing')) for c in calls)
    outcomes = collections.Counter(c.get('outcome', 'missing') for c in calls)
    durations = [c['duration_ms'] for c in calls if type(c.get('duration_ms')) in (int, float)
                 and math.isfinite(c['duration_ms']) and c['duration_ms'] >= 0]
    spans = [(unix_ms(c['started_at']), unix_ms(c['started_at']) + c['duration_ms']) for c in calls
             if c.get('started_at') and type(c.get('duration_ms')) in (int, float)
             and math.isfinite(c['duration_ms']) and c['duration_ms'] >= 0]
    return {'planningTraceRows': len(trace), 'planningRounds': len({r.get('round') for r in trace}),
            'planningVerdicts': dict(collections.Counter(r.get('verdict', 'missing') for r in trace)),
            'uniqueModelCallRecords': len(calls), 'recordReferences': sum(references.values()),
            'duplicateReferencesExcluded': sum(references.values()) - len(calls),
            'conflictingCallIdentities': conflicts,
            'recordedHTTPResponseCount': sum(type(c.get('http_status')) is int and c['http_status'] > 0 for c in calls),
            'httpStatuses': dict(statuses), 'modelOutcomes': dict(outcomes),
            'providerUsageValidCalls': valid, 'providerUsageUnknownCalls': unknown,
            'providerUsageInvalidCalls': invalid, 'providerReportedTotals': totals,
            'providerCachedInputFraction': totals['cached_tokens']/totals['prompt_tokens'] if totals['prompt_tokens'] else None,
            'estimatedTokenTotal': None,
            'usageBoundary': 'usage_status=valid is retained provider-reported telemetry, not invoice or packet-capture attestation; unknown usage is not zero; output_tokens is a cap; cached_tokens is part of prompt_tokens',
            'recordedModelDurationSumMs': sum(durations),
            'recordedModelWallSpanMs': max(e for _, e in spans)-min(s for s, _ in spans) if spans else None,
            'calls': calls}


def identity_and_observations(task, observations):
    errors, links = [], []
    task_id, revision = task['id'], task['currentRevision']
    plan = task.get('plan', {}).get('capabilities', {})
    robot = plan.get('robotId')
    records = observations.get('records', [])
    by_capture = {r.get('captureId'): r for r in records}
    if observations.get('taskId') != task_id:
        errors.append('observation collection task identity mismatch')
    for record in records:
        if (record.get('taskId') != task_id or record.get('robotId') != robot
                or record.get('taskRevision') != revision):
            errors.append('observation task/robot/revision mismatch')
    sent = {}
    for e in task.get('events', []):
        payload = e.get('payload') or {}
        if not isinstance(payload, dict):
            continue
        if payload.get('robotId') is not None and payload['robotId'] != robot:
            errors.append('event robot identity mismatch at sequence ' + str(e.get('sequence')))
        if payload.get('taskRevision') is not None and payload['taskRevision'] != revision:
            errors.append('event task revision mismatch at sequence ' + str(e.get('sequence')))
        if e.get('type') not in {'TOOL_ACTIVITY', 'CAPABILITY_CALL'}:
            continue
        step = e.get('stepId')
        command = payload.get('commandId')
        if command and not command.startswith(task_id + '/'):
            errors.append('command task identity mismatch')
        if e['type'] == 'CAPABILITY_CALL':
            match = re.fullmatch(r'rev-(\d+)-cap-(\d+)', step or '')
            if not match or int(match[1]) != revision or not 1 <= int(match[2]) <= len(plan.get('calls', [])):
                errors.append('capability call identity outside frozen plan')
            elif payload.get('tool') != plan['calls'][int(match[2])-1]['tool']:
                errors.append('capability call tool differs from frozen plan')
        if payload.get('activityStatus') == 'SENDING':
            sent[step] = unix_ms(e['occurredAt'])
        capture = payload.get('receiptObservationId')
        if payload.get('activityStatus') == 'CONFIRMED' and capture:
            record = by_capture.get(capture)
            valid = (record is not None and record.get('stepId') == step and not record.get('expired')
                     and step in sent and type(record.get('observedAtUnixMs')) in (int, float)
                     and sent[step] <= record['observedAtUnixMs'] <= unix_ms(e['occurredAt']))
            links.append({'stepId': step, 'captureId': capture, 'identityAndPostSendTimeVerified': valid})
            if not valid:
                errors.append('confirmed receipt lacks matching post-command observation')
    return {'robotId': robot, 'taskRevision': revision, 'observationRecords': len(records),
            'confirmedReceiptLinks': links, 'errors': errors,
            'scope': 'retained identity and timestamps only; not image interpretation or physical arrival certification'}


def event_projection(e):
    return {k: e.get(k, default) for k, default in (
        ('sequence', None), ('type', ''), ('stepId', ''), ('message', ''), ('payload', None), ('occurredAt', ''))}


def backup_and_compare(journal, output, task):
    snapshot = output/'journal-backup.sqlite'
    started = time.monotonic()
    with sqlite3.connect(journal.resolve().as_uri() + '?mode=ro', uri=True, timeout=10) as source:
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables <= ALLOWED_TABLES:
            raise ValueError('journal schema has unreviewed tables; refusing whole-database copy')
        with sqlite3.connect(snapshot) as destination:
            def progress(*_):
                if time.monotonic()-started > 30:
                    raise TimeoutError('online backup exceeded 30 seconds')
            source.backup(destination, pages=128, progress=progress, sleep=.01)
    with sqlite3.connect(snapshot.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
        if row is None:
            raise ValueError('task absent from online journal backup')
        database = dict(row)
        events = []
        for row in db.execute('SELECT sequence,type,step_id,message,payload_json,occurred_at FROM task_events WHERE task_id=? ORDER BY sequence', (task['id'],)):
            events.append({'sequence': row['sequence'], 'type': row['type'], 'stepId': row['step_id'], 'message': row['message'],
                           'payload': json.loads(row['payload_json']), 'occurredAt': row['occurred_at']})
        steps = [dict(r) for r in db.execute('SELECT step_id,status,reconcile_outcome FROM step_runs WHERE task_id=? ORDER BY step_id', (task['id'],))]
        evidence = []
        for row in db.execute('SELECT record_index,expired,expired_at,metadata_json FROM observation_evidence WHERE task_id=? ORDER BY record_index', (task['id'],)):
            record = json.loads(row['metadata_json'])
            record['recordIndex'], record['expired'] = row['record_index'], bool(row['expired'])
            if row['expired_at']:
                record['expiredAt'] = row['expired_at']
            evidence.append(record)
        checkpoint_count = db.execute('SELECT count(*) FROM fleet_checkpoints WHERE aggregate_id=?', (task['id'],)).fetchone()[0]
    task_fields = {'id': database['id'], 'request': database['request'], 'adapter': database['adapter'],
                   'state': database['state'], 'currentRevision': database['current_revision'],
                   'approved': bool(database['approved']), 'plan': json.loads(database['plan_json'])}
    mismatches = [k for k, value in task_fields.items() if task.get(k) != value]
    api_events = [event_projection(e) for e in task.get('events', [])]
    db_events = [event_projection(e) for e in events]
    event_differences = [i+1 for i in range(max(len(api_events), len(db_events)))
                         if i >= len(api_events) or i >= len(db_events) or api_events[i] != db_events[i]]
    database_task = {**task_fields, 'events': events}
    return {'backupSHA256': sha(snapshot), 'backupSeconds': time.monotonic()-started,
            'apiEventCount': len(api_events), 'databaseEventCount': len(db_events),
            'taskFieldMismatches': mismatches, 'eventDifferenceSequences': event_differences,
            'exactMatch': not mismatches and not event_differences,
            'databaseClosure': closure_status(database_task), 'stepRuns': steps,
            'fleetCheckpointRowsForTask': checkpoint_count,
            'checkpointBoundary': 'consistent SQLite online backup includes WAL; no PRAGMA checkpoint or writes to live journal',
            'observationRecords': evidence}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirects are forbidden for this read-only collector')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8897')
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--task-dir', type=Path, required=True)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wait-seconds', type=float, default=0)
    args = parser.parse_args()
    collector_sha_at_start = sha(Path(__file__))
    args.task_dir, args.journal, args.output = args.task_dir.resolve(), args.journal.resolve(), args.output.resolve()
    if args.base_url != 'http://127.0.0.1:8897' or not re.fullmatch(r'[A-Za-z0-9_-]+', args.task_id):
        parser.error('only the authorized loopback API and a plain task identifier are permitted')
    if not 0 <= args.wait_seconds <= 1800:
        parser.error('wait must be between 0 and 1800 seconds')
    args.output.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    requests = []
    def get(path):
        start = dt.datetime.now(dt.UTC).isoformat()
        try:
            with opener.open(urllib.request.Request(args.base_url+path, method='GET'), timeout=10) as response:
                value = json.load(response)
            requests.append({'method': 'GET', 'path': path, 'startedAtUTC': start, 'status': 200})
            return value
        except urllib.error.HTTPError as exc:
            requests.append({'method': 'GET', 'path': path, 'startedAtUTC': start, 'status': exc.code})
            raise RuntimeError('GET failed with HTTP '+str(exc.code)) from None
    path = '/v1/tasks/'+args.task_id
    deadline = time.monotonic()+args.wait_seconds
    polls = []
    while True:
        task = get(path)
        closure = closure_status(task)
        polls.append({'atUTC': dt.datetime.now(dt.UTC).isoformat(), 'state': task.get('state'),
                      'eventCount': len(task.get('events', [])), 'closure': closure})
        if closure['verified'] or time.monotonic() >= deadline:
            break
        time.sleep(min(1., max(0., deadline-time.monotonic())))
    save(args.output/'api-final-before-backup.json', task)
    journal = backup_and_compare(args.journal, args.output, task)
    after = get(path)
    save(args.output/'api-final-after-backup.json', after)
    stable = task == after
    observations = get(path+'/observations')
    recovery = get(path+'/recovery')
    save(args.output/'observations.json', observations)
    save(args.output/'recovery.json', recovery)
    retained = {}
    for name in ('draft.json', 'final.json', 'metrics-before.json', 'metrics-after.json', 'latency-after.json', 'user-actions.jsonl'):
        candidate = args.task_dir/name
        if candidate.is_file():
            retained[name] = {'sha256': sha(candidate), 'bytes': candidate.stat().st_size}
    retained_final = json.loads((args.task_dir/'final.json').read_text()) if (args.task_dir/'final.json').is_file() else None
    identity = identity_and_observations(task, observations)
    db_observations = {r['id']: r for r in journal.pop('observationRecords')}
    api_observations = {r['id']: r for r in observations.get('records', [])}
    observation_match = api_observations == db_observations
    usage = model_usage(task)
    transitions = [e for e in task.get('events', []) if e.get('type') == 'state.transition' and (e.get('payload') or {}).get('state') in TERMINAL]
    terminal_at = transitions[-1]['occurredAt'] if transitions else None
    unknown = any((e.get('payload') or {}).get('outcomeUnknown') is True for e in task.get('events', []) if isinstance(e.get('payload'), dict))
    coherent_unknown = not unknown or (recovery.get('requiresReconciliation') is True and recovery.get('canResume') is False)
    approved = task.get('approvedAt')
    times = {'createdAt': task.get('createdAt'), 'approvedAt': approved, 'terminalTransitionAt': terminal_at,
             'lastTaskUpdatedAt': task.get('updatedAt'),
             'approvedToTerminalMs': unix_ms(terminal_at)-unix_ms(approved) if terminal_at and approved else None,
             'createdToTerminalMs': unix_ms(terminal_at)-unix_ms(task['createdAt']) if terminal_at else None,
             'boundary': 'createdAt occurs after planning; browser action recording times are not HTTP request-start timestamps; updatedAt can include later ops events'}
    source_metrics = {}
    for name in ('metrics-before.json', 'metrics-after.json', 'latency-after.json'):
        candidate = args.task_dir/name
        if candidate.is_file():
            source_metrics[name] = json.loads(candidate.read_text())
    validated = closure['verified'] and journal['databaseClosure']['verified'] and journal['exactMatch'] and stable and observation_match and not identity['errors'] and coherent_unknown
    report = {'schemaVersion': 'task.closure-audit.v1', 'taskId': task['id'], 'state': task['state'],
              'collectorSHA256AtStart': collector_sha_at_start, 'collectorSHA256AtEnd': sha(Path(__file__)),
              'collectorSourceStable': collector_sha_at_start == sha(Path(__file__)),
              'apiClosure': closure, 'journal': journal, 'apiStableAcrossBackup': stable,
              'apiObservationsMatchBackup': observation_match, 'identity': identity,
              'closureVerified': validated, 'taskSucceeded': task['state'] == 'SUCCEEDED',
              'physicalOutcomeUnknown': unknown, 'unknownRecoveryContractCoherent': coherent_unknown,
              'automaticTaskRetryPerformed': False, 'modelUsage': usage, 'timing': times,
              'retainedSnapshotEventCount': len(retained_final.get('events', [])) if retained_final else None,
              'retainedSnapshotEqualsCollected': retained_final == task if retained_final else None,
              'retainedInputs': retained, 'snapshotMetrics': source_metrics,
              'metricBoundary': 'orchestration metrics are a global snapshot, not LLM HTTP or decision failure rates; capability latency p95 with n=1 is one sample, not a population percentile',
              'scope': 'read-only GET and consistent online SQLite backup; no credential file or environment read, no commands, approval, resume, retry or reconciliation'}
    save(args.output/'report.json', report)
    save(args.output/'polls.json', polls)
    save(args.output/'http-get-log.json', requests)
    save(args.output/'manifest.json', {p.name: {'sha256': sha(p), 'bytes': p.stat().st_size} for p in sorted(args.output.iterdir()) if p.is_file()})
    print(json.dumps({'output': str(args.output), 'state': task['state'], 'closureStatus': closure['status'],
                      'closureVerified': validated, 'taskSucceeded': report['taskSucceeded'],
                      'recordedModelCalls': usage['uniqueModelCallRecords'], 'providerReportedTokens': usage['providerReportedTotals']}))
    return 0 if validated else 2


if __name__ == '__main__':
    raise SystemExit(main())
