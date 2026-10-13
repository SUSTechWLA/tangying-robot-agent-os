"""Offline receipt checks use retained data, never Runtime signing secrets."""
import copy
import hashlib
import json
from datetime import UTC, datetime

import pytest

from scripts.semantic_execution_evidence import (
    canonical_observation,
    validate_grounding_checkpoints,
    validate_semantic_route,
)


def iso(stamp):
    return datetime.fromtimestamp(stamp/1000, UTC).isoformat()


def fixture(*, count=1, mode='arrival', revision=1, cap='cap1'):
    identity = {'robotId': 'r', 'frameId': 'map', 'mapId': 'm', 'mapRevision': 'a'*64,
                    'calibrationRevision': 'b'*64, 'cloudSha256': 'c'*64}
    targets = [{'annotationId': f'a{i}', 'name': f'巡检点{i}', 'maxDistanceM': .65,
                   'allowStaleSearch': False, 'verificationMode': mode} for i in range(count)]
    route = {key: value for key, value in identity.items() if key not in ('robotId', 'frameId')}
    route.update(semanticRevision='d'*64, targets=targets)
    sequence, arrivals, events = [], [], []
    for index, target in enumerate(targets):
        stamp = 1000+index*4000
        stage = f'task{index+1:02d}-' if count > 1 else ''
        scope = (f'revision-{revision}/' if revision > 1 else '')+cap+'-'+stage
        frozen = dict(identity=identity, semanticRevision=route['semanticRevision'], **target)
        intent = {'action': 'home_route', 'robotId': 'r', 'routeRooms': [target['name']], 'semanticTarget': frozen}
        sequence.append(intent)
        token = f'opaque-runtime-binding-{index}'
        digest = hashlib.sha256(token.encode()).hexdigest()
        execution = {'identity': identity, 'semanticRevision': route['semanticRevision'], 'annotationId': target['annotationId'],
            'worldFrameRevision': 'world-rev', 'verificationMode': mode, 'allowStaleSearch': False, 'targetFresh': True}
        args = {'semanticBinding': token, 'goalPose': [0, 1, 0, 1, 0, 0, 0]}
        command = f'task/revision/{revision}/step/{scope}navigate_00'
        verify_command = f'task/revision/{revision}/step/{scope}verify_arrival_00'
        nav_event = {'type': 'TOOL_ACTIVITY', 'stepId': scope+'navigate_00', 'occurredAt': iso(stamp+700),
            'payload': {'arguments': copy.deepcopy(args), 'commandId': command, 'taskRevision': revision,
                         'toolName': 'navigation.navigate', 'activityStatus': 'CONFIRMED'}}
        nav = {'index': len(events)+3, 'event': nav_event, 'detail': {'observedAtUnixMs': stamp+600},
            'state': {'map_route': {'commandId': command, 'goalPose': args['goalPose'],
                'semanticBindingSha256': digest, 'semanticExecution': copy.deepcopy(execution)}}}
        verification = {'commandId': verify_command, 'navigationCommandId': command, 'semanticBindingSha256': digest,
            'semanticExecution': copy.deepcopy(execution), 'arrivalVerified': True,
            'freshCaptureAtUnixMs': stamp+850, 'semanticConfirmed': False}
        verify_event = {'type': 'TOOL_ACTIVITY', 'stepId': scope+'verify_arrival_00', 'occurredAt': iso(stamp+1200),
            'payload': {'arguments': copy.deepcopy(args), 'commandId': verify_command,
                         'toolName': 'verify_arrival', 'taskRevision': revision, 'activityStatus': 'CONFIRMED'}}
        row = {'navigation': nav, 'index': len(events)+5, 'event': verify_event,
            'state': {'semantic_verification': verification}, 'detail': {'observedAtUnixMs': stamp+1100,
                'snapshot': {'robotProfile': {'sensors': [{'sourceId': 'r/head-rgbd', 'sourceType': 'rgbd_camera',
                    'frameId': 'head_optical', 'transformRevision': identity['calibrationRevision'], 'maxAgeMs': 2000}]}}}}
        if mode == 'object':
            nested = {'schemaVersion': 'semantic.cloud.observation.v1', 'identity': identity,
                'activeMap': {key: identity[key] for key in ('mapId','mapRevision','calibrationRevision')},
                'mapRevision': identity['mapRevision'], 'semanticRevision': route['semanticRevision'],
                'annotationId': target['annotationId'], 'frameId': 'map', 'worldFrameRevision': 'world-rev',
                'observationId': f'capture-{index}', 'sourceId': 'r/head-rgbd', 'sourceType': 'rgbd_camera',
                'sourceFrameId': 'head_optical', 'captureBasePose': [0.,1.,0.,1.,0.,0.,0.], 'captureBaseFrameId': 'world',
                'observedAtUnixMs': stamp+900, 'sequence': 1, 'targetFresh': True, 'targetObservedAtUnixMs': stamp-100,
                'semanticConfirmed': True, 'geometryObserved': True, 'geometryPointCount': 16, 'matchCount': 1,
                'matches': [{'entityId': 'cup','category': 'cup','mapPosition': [1.,1.,.5],'confidence': 1.0,'distanceToBoundsM': 0.}],
                'matchesTruncated': False, 'reason': 'matched', 'associationToleranceM': .15, 'perceptionDigest': 'f'*64,
                'executionAuthorized': False, 'manipulationReachVerified': False}
            nested['evidenceHash'] = hashlib.sha256(canonical_observation(nested)).hexdigest()
            verification.update(observationEvidence=nested, semanticConfirmed=True,
                                observationId=nested['observationId'], sourceId=nested['sourceId'],
                                observedAtUnixMs=nested['observedAtUnixMs'])
        grounded = {'taskId': 'task', 'robotId': 'r', 'action': 'home_route', 'stepIdPrefix': stage,
            'routeRooms': [target['name']], 'routeGoals': [args['goalPose']], 'semanticBindings': [token],
            'goalEvidence': {'semanticTarget': frozen, 'candidate': args}}
        def sending(event, occurred):
            result = copy.deepcopy(event)
            result['occurredAt'] = iso(occurred)
            result['payload']['activityStatus'] = 'SENDING'
            return result
        events.extend([
            {'type': 'SEMANTIC_GROUNDING_FROZEN', 'stepId': scope+'semantic-grounding',
                'payload': {'taskRevision': revision, 'intentBinding': 'e'*64, 'grounded': grounded}},
            {'type': 'TOOL_ACTIVITY', 'stepId': scope+'pre_position', 'occurredAt': iso(stamp+50),
                'payload': {'activityStatus': 'SENDING', 'toolName': 'navigation.pre_position'}},
            sending(nav_event, stamp+100), nav_event, sending(verify_event, stamp+800), verify_event])
        arrivals.append(row)
    intent = sequence[0] if count == 1 else {'action': 'home_route', 'robotId': 'r', 'sequence': sequence}
    call = {'arguments': {'semanticRoute': route}, 'legacyIntent': intent}
    task = {'id': 'task', 'currentRevision': revision, 'events': events}
    return copy.deepcopy((call, arrivals, task))


def rows(arrivals):
    return [item for row in arrivals for item in (row['navigation'], row)]


def check(call, arrivals, task, cap='cap1'):
    result = validate_semantic_route(call, cap, arrivals)
    validate_grounding_checkpoints(task, call, cap, rows(arrivals))
    return result


@pytest.mark.parametrize('mode', ['arrival', 'object'])
@pytest.mark.parametrize('revision', [1, 2])
@pytest.mark.parametrize('count', [1, 3])
def test_route_and_checkpoints_pass_with_ordered_nested_evidence(mode, revision, count):
    call, arrivals, task = fixture(count=count, mode=mode, revision=revision, cap='rev-1-cap-02')
    result = check(call, arrivals, task, 'rev-1-cap-02')
    assert result['signatureIndependentlyVerified'] is False
    assert all(item['semanticConfirmed'] is (mode == 'object') for item in result['semanticRoute'])
    if revision == 2:
        check(call, arrivals, task, 'revision-2/rev-1-cap-02')


@pytest.mark.parametrize('mutation', [
    lambda c, r: r['event']['payload'].update(arguments={'semanticBinding': 'changed'}),
    lambda c, r: r['state']['semantic_verification'].update(navigationCommandId='other'),
    lambda c, r: r['state']['semantic_verification'].update(commandId='other'),
    lambda c, r: r['state']['semantic_verification'].update(freshCaptureAtUnixMs=2200),
    lambda c, r: r['state']['semantic_verification'].update(freshCaptureAtUnixMs=True),
    lambda c, r: r['state']['semantic_verification'].update(freshCaptureAtUnixMs=1850.5),
    lambda c, r: r['state']['semantic_verification'].update(arrivalVerified=False),
    lambda c, r: r['navigation']['state']['map_route'].update(semanticBindingSha256='wrong'),
    lambda c, r: c['arguments']['semanticRoute']['targets'].append(copy.deepcopy(c['arguments']['semanticRoute']['targets'][0])),
    lambda c, r: r['state']['semantic_verification']['semanticExecution'].update(targetFresh=False),
    lambda c, r: c['legacyIntent'].update(routeRooms=['另一个点']),
    lambda c, r: r['event'].update(stepId='cap10-verify_arrival_00'),
    lambda c, r: r['event']['payload'].update(taskRevision=2),
    lambda c, r: r['navigation'].update(index=r['index']),
    lambda c, r: r['event']['payload']['arguments'].update(goalPose=[9,9,0,1,0,0,0]),
])
def test_missing_reordered_or_misbound_route_evidence_is_rejected(mutation):
    call, arrivals, _ = fixture()
    mutation(call, arrivals[0])
    with pytest.raises(AssertionError):
        validate_semantic_route(call, 'cap1', arrivals)


@pytest.mark.parametrize('mutation', [
    lambda t: t.update(events=t['events'][1:]),
    lambda t: t['events'].reverse(),
    lambda t: t['events'].insert(0, copy.deepcopy(t['events'][0])),
    lambda t: t['events'][0]['payload']['grounded'].update(semanticBindings=['changed']),
    lambda t: t['events'][0]['payload']['grounded'].update(routeGoals=[[9,9,0,1,0,0,0]]),
    lambda t: t['events'][0]['payload']['grounded'].update(action='home_manipulation'),
    lambda t: t['events'][0]['payload'].update(taskRevision=2),
    lambda t: t['events'][0]['payload'].update(intentBinding='missing-digest'),
    lambda t: t['events'].__setitem__(slice(0,2), list(reversed(t['events'][:2]))),
])
def test_checkpoint_cannot_be_missing_late_or_replaced(mutation):
    call, arrivals, task = fixture()
    mutation(task)
    with pytest.raises(AssertionError):
        validate_grounding_checkpoints(task, call, 'cap1', rows(arrivals))


@pytest.mark.parametrize('mode', ['arrival', 'object'])
def test_reordered_visits_are_rejected_even_when_each_receipt_is_valid(mode):
    call, arrivals, task = fixture(count=3, mode=mode)
    arrivals[0], arrivals[1] = arrivals[1], arrivals[0]
    with pytest.raises(AssertionError):
        validate_semantic_route(call, 'cap1', arrivals)
    # Move complete event blocks while preserving their contents and IDs.
    task['events'] = task['events'][6:12]+task['events'][:6]+task['events'][12:]
    with pytest.raises(AssertionError):
        validate_grounding_checkpoints(task, call, 'cap1', rows(arrivals))


def test_wrong_revision_prefix_is_rejected():
    call, arrivals, task = fixture(revision=2)
    with pytest.raises(AssertionError):
        check(call, arrivals, task, 'revision-3/cap1')
    task['events'][0]['stepId'] = 'cap1-semantic-grounding'
    with pytest.raises(AssertionError):
        validate_grounding_checkpoints(task, call, 'cap1', rows(arrivals))


def test_integer_float_json_spellings_do_not_break_object_digest():
    call, arrivals, task = fixture(mode='object')
    raw = json.dumps((call, arrivals, task))
    def go_number(value):
        number = float(value)
        return int(number) if number.is_integer() else number
    call, arrivals, task = json.loads(raw, parse_float=go_number)
    assert type(arrivals[0]['state']['semantic_verification']['observationEvidence']['matches'][0]['confidence']) is int
    check(call, arrivals, task)
    assert canonical_observation({'b': True, 'n': [1., -.0, .5], 's': '1.0'}) == b'{"b":true,"n":[1,0,0.5],"s":"1.0"}'


def nested(row):
    return row['state']['semantic_verification']['observationEvidence']


@pytest.mark.parametrize('mutation', [
    lambda r: nested(r).update(frameId='world'),
    lambda r: nested(r).update(captureBaseFrameId='map'),
    lambda r: nested(r).update(sourceFrameId='other_optical'),
    lambda r: nested(r).update(sourceId='unknown-camera'),
    lambda r: nested(r).update(sourceType='sim_ground_truth'),
    lambda r: nested(r)['identity'].update(robotId='wrong-robot'),
    lambda r: nested(r).update(observationId='other-frame'),
    lambda r: nested(r).update(observedAtUnixMs=2101),
    lambda r: nested(r).update(observedAtUnixMs=1),
    lambda r: nested(r).update(observedAtUnixMs=True),
    lambda r: nested(r).update(matchCount=True),
    lambda r: nested(r).update(matchCount=2),
    lambda r: nested(r).update(semanticConfirmed=False),
    lambda r: nested(r).update(evidenceHash='0'*64),
    lambda r: nested(r).update(captureBasePose=[0,0,0,0,0,0,0]),
    lambda r: r['state']['semantic_verification'].update(semanticConfirmed=False),
    lambda r: r['state']['semantic_verification'].update(observationEvidence={}),
    lambda r: r['detail']['snapshot']['robotProfile']['sensors'][0].update(transformRevision='wrong'),
    lambda r: r['detail']['snapshot']['robotProfile']['sensors'][0].update(sourceId='wrong-source'),
    lambda r: r['detail']['snapshot']['robotProfile']['sensors'][0].update(maxAgeMs=1),
])
def test_object_evidence_independently_rejects_wrong_frames_sources_times_and_digest(mutation):
    call, arrivals, _ = fixture(mode='object')
    mutation(arrivals[0])
    with pytest.raises(AssertionError):
        validate_semantic_route(call, 'cap1', arrivals)


def test_rehashed_source_frame_tampering_still_fails_against_sensor_declaration():
    call, arrivals, _ = fixture(mode='object')
    evidence = nested(arrivals[0])
    evidence['sourceFrameId'] = 'invented-frame'
    evidence.pop('evidenceHash')
    evidence['evidenceHash'] = hashlib.sha256(canonical_observation(evidence)).hexdigest()
    with pytest.raises(AssertionError, match='independently declared'):
        validate_semantic_route(call, 'cap1', arrivals)


def test_rehashed_predispatch_object_observation_is_rejected():
    call, arrivals, task = fixture(mode='object')
    evidence = nested(arrivals[0])
    evidence['observedAtUnixMs'] = 1700  # after navigation capture, before verify dispatch at 1800.
    arrivals[0]['state']['semantic_verification']['observedAtUnixMs'] = 1700
    evidence.pop('evidenceHash')
    evidence['evidenceHash'] = hashlib.sha256(canonical_observation(evidence)).hexdigest()
    validate_semantic_route(call, 'cap1', arrivals)
    with pytest.raises(AssertionError, match='predates its own verification'):
        validate_grounding_checkpoints(task, call, 'cap1', rows(arrivals))
