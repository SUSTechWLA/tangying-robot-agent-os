"""Recorded inputs and deterministic controller tests; no physical success claim."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
from tangying_robot_gateway import gazebo_manipulation as module
from tangying_robot_gateway.gazebo_manipulation import GazeboManipulation, pose_matrix
from tangying_robot_gateway.runtime import Command, Result


def fixture():
    return json.loads((Path(__file__).parent/'fixtures/gazebo-grasp-plan-run006.json').read_text())['observation']


def setup_controller(monkeypatch, *, recorded=None, home=True, expected_success=True):
    original = recorded or fixture()
    clock = [float(original['wall_time_unix_ms'])/1000, 100.]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[1])
    monkeypatch.setattr(module.time, 'monotonic_ns', lambda: int(clock[1]*1e9))
    state = original['robot_state']
    joints = dict(state['joint_positions'])
    node = NS(runtime=NS(base_pose=pose_matrix(state['base_pose']), robot_id='gazebo-home_furnished',
                        calibration_revision=state['perception']['calibration_revision']),
              joint_snapshot=lambda: (dict(joints), 0., 1))
    backend = NS(node=node, home=home, cancel_event=threading.Event())
    control = GazeboManipulation(backend)
    control.state = lambda: {'attached': False, 'toolCommissioning': control.tool}

    def capture(position=None, *, category='cup', identity='ceramic-mug', robot='gazebo-home_furnished', blank=False):
        raw = json.loads(json.dumps(original))
        raw['observation_id'] = (original['observation_id'] if clock[1] == 100. else f"{original['observation_id']}/test-new-{clock[1]}")
        raw['wall_time_unix_ms'] = int(clock[0]*1000)
        raw['robot_state']['perception']['sensor_stamp_ns'] = str(int(original['robot_state']['perception']['sensor_stamp_ns'])+int((clock[1]-100.)*1e9))
        raw['reconstruction']['robotId'] = robot
        entities = [NS(**entry) for entry in raw['entities']]
        for e in entities:
            if e.entity_id == 'ceramic-mug':
                e.entity_id, e.category = identity, category
                if position is not None:
                    e.pose_xyz_quat[:3] = list(position)
        frame = NS(**raw)
        frame.entities = entities
        return frame, {} if blank else {e.entity_id: e for e in entities}

    control.capture = capture
    plan_command = Command(schema_version='robot.v1', command_id='original-plan-command', task_id='task',
        capability='plan_grasp', robot_id=node.runtime.robot_id, catalog_revision='original-catalog',
        parameters={'objectId': 'ceramic-mug', 'destinationId': 'kitchen-tray'})
    result = control.execute(plan_command)
    assert result.success is expected_success, result.message
    pick = Command(schema_version='robot.v1', command_id='one-owned-pick', task_id='task',
        capability='manipulation.pick', target_ref='ceramic-mug', robot_id=node.runtime.robot_id,
        catalog_revision='original-catalog', deadline_unix_ms=int(clock[0]*1000)+60000)
    return control, pick, clock, joints, capture, json.loads(result.message)


def test_unreachable_first_calibrated_view_keeps_three_complete_routes(monkeypatch):
    record = json.loads((Path(__file__).parent/'fixtures/gazebo-grasp-resolve-focus002.json').read_text())
    c, command, clock, joints, capture, message = setup_controller(monkeypatch, recorded=record['observation'],
                                              home=record['commissionedHome'])
    assert c.side == 'left' and len(c.plan['views']) == 3
    rejected = [p for p in message['phases'] if p['phase'] == 'view_rejected']
    assert rejected[0]['candidate'] == 0 and rejected[0]['stage'] == 'empty_arm_approach'
    assert rejected[0]['rejectReason'] == 'GRASP_TARGET_UNREACHABLE'
    accepted = [p for p in message['phases'] if p['phase'] == 'view_preflight_complete']
    assert [p['candidate'] for p in accepted] == [1, 2, 3]
    assert message['planSource']['captureId'] == record['observation']['observation_id']
    assert message['physicalStarted'] is False
    assert c.plan['viewIndices'] == [1, 2, 3]
    sent, descents = install_views(monkeypatch, c, clock, joints, capture, [capture])
    result = c.execute(command)
    assert result.code == 'TEST_DESCENT_BOUNDARY' and len(sent) == len(descents) == 1
    phases = json.loads(result.message)['phases']
    assert next(p for p in phases if p['phase'] == 'empty_arm_view')['candidate'] == 1
    assert next(p for p in phases if p['phase'] == 'view_capture')['candidate'] == 1


def test_loaded_route_rejection_preserves_previous_accepted_start(monkeypatch):
    original = module.plan_empty_arm_approach
    starts, ends = [], []
    def approach(*args, **kwargs):
        starts.append(dict(args[1]))
        result = original(*args, **kwargs)
        ends.append(result[1])
        return result
    tip = module.plan_tip_chunk
    calls = [0]
    def reject_first_loaded(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 2:
            raise ValueError('WORKCELL_MOTION_LIMIT')
        return tip(*args, **kwargs)
    monkeypatch.setattr(module, 'plan_empty_arm_approach', approach)
    monkeypatch.setattr(module, 'plan_tip_chunk', reject_first_loaded)
    c, _, _, _, _, message = setup_controller(monkeypatch)
    assert len(c.plan['views']) == 3
    assert starts[1] == starts[0]
    assert all(starts[2][name] == value for name, value in ends[1].items())
    assert any(p.get('stage') == 'loaded_segment_0' and p.get('rejectReason') == 'WORKCELL_MOTION_LIMIT'
               for p in message['phases'])


def test_all_view_paths_rejected_retains_source_and_never_dispatches(monkeypatch):
    def reject(*_args, **_kwargs):
        raise ValueError('GRASP_APPROACH_CLEARANCE')
    monkeypatch.setattr(module, 'plan_empty_arm_approach', reject)
    monkeypatch.setattr(module, 'execute_chunk', lambda *_args: pytest.fail('Preflight must not actuate'))
    c, _, _, _, _, message = setup_controller(monkeypatch, expected_success=False)
    assert c.plan is None and message['physicalStarted'] is False
    assert message['outcome']['code'] == 'GRASP_TARGET_UNREACHABLE'
    assert len(message['sourceToken']) == 64
    assert message['planSource']['captureId'] == fixture()['observation_id']
    assert len([p for p in message['phases'] if p['phase'] == 'view_rejected']) == 4


def test_failed_owned_replan_invalidates_old_same_task_plan(monkeypatch):
    c, pick, _, _, _, _ = setup_controller(monkeypatch)
    old = c.plan
    def reject(*_args, **_kwargs):
        raise ValueError('GRASP_APPROACH_CLEARANCE')
    monkeypatch.setattr(module, 'plan_empty_arm_approach', reject)
    monkeypatch.setattr(module, 'execute_chunk', lambda *_args: pytest.fail('Failed replan cannot actuate'))
    replan = Command(schema_version='robot.v1', command_id='new-owned-plan', task_id=pick.task_id,
        robot_id=pick.robot_id, catalog_revision=pick.catalog_revision, capability='plan_grasp',
        parameters={'objectId': 'ceramic-mug', 'destinationId': 'kitchen-tray'})
    assert c.execute(replan).code == 'GRASP_TARGET_UNREACHABLE'
    assert c.plan is None
    assert c._pick_guard(pick, old) == 'GRASP_PLAN_REPLACED'
    assert c.execute(pick).code == 'GRASP_PLAN_REQUIRED'


def test_busy_replan_cannot_invalidate_another_owned_plan(monkeypatch):
    c, pick, _, _, _, _ = setup_controller(monkeypatch)
    old = c.plan
    replan = Command(schema_version='robot.v1', command_id='busy-plan', task_id=pick.task_id,
        capability='plan_grasp', parameters={'objectId': 'ceramic-mug', 'destinationId': 'kitchen-tray'})
    with c._plan_lock:
        assert c.execute(replan).code == 'ROBOT_BUSY'
    assert c.plan is old


def install_views(monkeypatch, control, clock, joints, capture, observations, *, after_motion=None):
    dispatched, descents = [], []
    capture_index = [0]
    original_capture = capture

    def fresh_capture():
        capture_index[0] += 1
        if capture_index[0] == 1:
            return original_capture()
        index = capture_index[0]-2
        return observations[min(index, len(observations)-1)]()

    def execute(_node, chunk, cancel):
        if cancel.is_set():
            return Result(False, 'CANCELLED')
        dispatched.append(chunk)
        joints.update({key.removesuffix('.pos'): value for key, value in chunk[-1].items()})
        clock[0] += .25
        clock[1] += .25
        if after_motion:
            after_motion()
        return Result(True)

    def wait(*_args):
        clock[0] += .01
        clock[1] += .01
        return True

    control.capture = fresh_capture
    control.backend.wait_command_capture = wait
    monkeypatch.setattr(module, 'execute_chunk', execute)
    control.move_tip = lambda target, **_kw: descents.append(target) or Result(False, 'TEST_DESCENT_BOUNDARY')
    control.suction = lambda *_args, **_kw: pytest.fail('No test may attach a physical payload')
    return dispatched, descents


def test_real_recorded_geometry_preflights_four_views_and_binds_exact_input(monkeypatch):
    c, _, _, _, _, message = setup_controller(monkeypatch)
    source = message['planSource']
    record = fixture()
    assert source['captureId'] == record['observation_id']
    assert source['sensorStampNs'] == record['robot_state']['perception']['sensor_stamp_ns']
    assert source['position'] == record['entities'][0]['pose_xyz_quat'][:3]
    assert source['calibrationRevision'] == record['robot_state']['perception']['calibration_revision']
    assert source['base'] == pose_matrix(record['robot_state']['base_pose']).tolist()
    assert source['commandId'] == 'original-plan-command'
    assert source['category'] == 'cup' and source['sourceOriginalBBoxAvailable'] is False
    assert message['physicalStarted'] is False
    assert len(c.plan['views']) == 4
    offsets = [np.linalg.norm(view-c.plan['position']) for view in c.plan['views']]
    assert offsets == pytest.approx([.12, .08, .08, .12])
    assert len(message['sourceToken']) == 64
    with pytest.raises(ValueError):
        c.plan['position'][0] += 1.


def test_wrong_far_cup_is_rejected_then_fresh_original_is_only_descent_target(monkeypatch):
    c, command, clock, joints, capture, source = setup_controller(monkeypatch)
    anchor = c.plan['position'].copy()
    sent, descents = install_views(monkeypatch, c, clock, joints, capture,
        [lambda: capture(anchor+[.337, .308, .092]), lambda: capture(anchor+[.003, 0., 0.])])
    result = c.execute(command)
    assert result.code == 'TEST_DESCENT_BOUNDARY'
    assert len(sent) == 2 and len(descents) == 1
    assert descents[0] == pytest.approx(anchor+[.003, 0., c.tool['contactHeightM']])
    details = json.loads(result.message)
    assert details['sourceToken'] == source['sourceToken']
    assert details['planSource'] == source['planSource']
    frames = [p for p in details['phases'] if p['phase'] == 'view_capture']
    assert frames[0]['rejectReason'] == 'ORIGINAL_TARGET_POSITION_MISMATCH'
    assert frames[1]['associated'] is True
    # The second trajectory starts from newly measured first-view joints.
    assert sent[0] != sent[1] and len(sent[1]) < len(sent[0])


@pytest.mark.parametrize('kind', ['far', 'missing', 'wrong_kind', 'wrong_id'])
def test_all_four_unassociated_views_stop_without_descent_or_attach(monkeypatch, kind):
    c, command, clock, joints, capture, _ = setup_controller(monkeypatch)
    kwargs = {'far': {'position': c.plan['position']+[.337, .308, .092]},
              'missing': {'blank': True}, 'wrong_kind': {'category': 'vase'},
              'wrong_id': {'identity': 'different-cup'}}[kind]
    sent, descents = install_views(monkeypatch, c, clock, joints, capture, [lambda: capture(**kwargs)])
    result = c.execute(command)
    assert result.code == 'GRASP_PLAN_STALE'
    assert len(sent) == 4 and not descents
    assert json.loads(result.message)['physicalStarted'] is True


@pytest.mark.parametrize('change', ['expiry', 'cancel', 'calibration', 'base', 'robot', 'deadline'])
def test_change_after_motion_stops_before_any_next_view_or_descent(monkeypatch, change):
    c, command, clock, joints, capture, _ = setup_controller(monkeypatch)
    def mutate():
        if change == 'expiry': clock[1] += 30.001
        if change == 'cancel': c.backend.cancel_event.set()
        if change == 'calibration': c.node.runtime.calibration_revision = 'different'
        if change == 'base':
            c.node.runtime.base_pose = c.node.runtime.base_pose.copy()
            c.node.runtime.base_pose[0, 3] += .031
        if change == 'robot': c.node.runtime.robot_id = 'different'
        if change == 'deadline': clock[0] += 60.
    sent, descents = install_views(monkeypatch, c, clock, joints, capture,
                                  [capture], after_motion=mutate)
    result = c.execute(command)
    assert not result.success and len(sent) == 1 and not descents
    assert any('rejectReason' in p for p in json.loads(result.message)['phases'])


def test_budget_exhausted_inside_ik_has_no_physical_dispatch(monkeypatch):
    c, command, clock, joints, capture, _ = setup_controller(monkeypatch)
    sent, descents = install_views(monkeypatch, c, clock, joints, capture, [capture])
    planner = module.plan_empty_arm_approach
    def delayed(*args, **kwargs):
        value = planner(*args, **kwargs)
        clock[1] += 30.001
        return value
    monkeypatch.setattr(module, 'plan_empty_arm_approach', delayed)
    result = c.execute(command)
    assert result.code == 'GRASP_PLAN_STALE' and not sent and not descents
    assert json.loads(result.message)['physicalStarted'] is False


def test_per_tick_cancellation_becomes_true_when_original_budget_expires(monkeypatch):
    c, command, clock, _, _, _ = setup_controller(monkeypatch)
    cancel = c._pick_cancel(command, c.plan)
    assert not cancel.is_set()
    clock[1] += 30.001
    assert cancel.is_set()


def test_duplicate_entity_ids_are_not_silently_collapsed(monkeypatch):
    c, _, _, _, capture, _ = setup_controller(monkeypatch)
    frame, _ = capture()
    frame.entities.append(frame.entities[0])
    c.backend.observe = lambda _request: frame
    with pytest.raises(ValueError, match='OBJECT_IDENTITY_AMBIGUOUS'):
        GazeboManipulation.capture(c)


def test_fresh_capture_from_other_robot_cannot_authorize_descent(monkeypatch):
    c, command, clock, joints, capture, _ = setup_controller(monkeypatch)
    sent, descents = install_views(monkeypatch, c, clock, joints, capture,
                                  [lambda: capture(robot='other-robot')])
    result = c.execute(command)
    assert result.code == 'GRASP_PLAN_STALE' and len(sent) == 1 and not descents


def test_actual_joint_loop_stops_publishing_on_budget_expiry(monkeypatch):
    from tangying_robot_gateway.gazebo_actuation import execute_chunk
    c, command, clock, joints, _, _ = setup_controller(monkeypatch)
    c.plan['created'] = clock[1]-29.91
    sent, held = [], []
    node = c.node
    node._command_lock = threading.Lock()
    node.motion_allowed = lambda: True
    node.send_joint_targets = lambda targets: sent.append((clock[1], targets))
    node.hold_joints = lambda: held.append(True)
    def sleep(seconds):
        clock[1] += seconds
    target = {'left_arm_shoulder_lift.pos': joints['left_arm_shoulder_lift']-.5}
    result = execute_chunk(node, [target], c._pick_cancel(command, c.plan),
                           clock=lambda: clock[1], sleep=sleep)
    assert result.code == 'CANCELLED' and len(sent) == 2 and held == [True]
    assert all(stamp-c.plan['created'] <= 30 for stamp, _ in sent)


def test_capture_base_uses_capture_convention_and_confidence_is_absolute(monkeypatch):
    from scipy.spatial.transform import Rotation
    c, command, _, _, capture, _ = setup_controller(monkeypatch)
    frame, entities = capture()
    # Raw IMU tilt is deliberately different from the leveled capture pose.
    tilted = c.plan['base'].copy()
    tilted[:3, :3] = tilted[:3, :3] @ Rotation.from_euler('x', .04).as_matrix()
    c.plan['base'] = tilted
    c.node.runtime.base_pose = tilted.copy()
    source = json.loads(c.plan['sourceJSON'])
    source['confidence'] = 1.
    c.plan['sourceJSON'] = json.dumps(source)
    assert c._pick_guard(command, c.plan, frame) == ''
    position, detail = c._associate_target(c.plan, frame, entities)
    assert position is not None and detail['associated']
    entities['ceramic-mug'].confidence = .899
    position, detail = c._associate_target(c.plan, frame, entities)
    assert position is None and detail['rejectReason'] == 'ORIGINAL_TARGET_KIND_MISMATCH'
