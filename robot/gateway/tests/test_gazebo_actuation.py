import threading
from types import SimpleNamespace

import pytest
from tangying_robot_gateway.gazebo_actuation import execute_chunk, joint_ramp_step


@pytest.mark.parametrize('coordinated', [False, True])
def test_joint_receipt_requires_measured_convergence_and_stops_on_cancellation(coordinated):
    state = {'q': 0., 'stamp': 1, 'time': 0., 'hold': 0, 'last': 0.}
    key = 'left_arm_shoulder_pan'
    def snapshot():
        return {key: state['q']}, 0., state['stamp']
    def send(target):
        state['last'] = target[key]
    def tick(seconds):
        state['time'] += seconds
        state['stamp'] += 1
        state['q'] = state['last']
    def hold():
        state['hold'] += 1
    node = SimpleNamespace(_command_lock=threading.Lock(), joint_snapshot=snapshot,
                           send_joint_targets=send, hold_joints=hold, motion_allowed=lambda: True)
    event = threading.Event()
    result = execute_chunk(node, [{key+'.pos': .2}], event,
                           clock=lambda: state['time'], sleep=tick, coordinated=coordinated)
    assert result.success and abs(state['q']-.2) <= .04 and state['hold'] == 0
    assert abs(state['last']-.2) < 1e-9  # final controller target remains the requested goal
    event.set()
    assert execute_chunk(node, [{key+'.pos': .4}], event, coordinated=coordinated).code == 'CANCELLED'
    assert state['hold'] == 1 and not node._command_lock.locked()


@pytest.mark.parametrize('coordinated', [False, True])
def test_frozen_feedback_never_becomes_success_and_out_of_range_never_sends(coordinated):
    sent, held = [], []
    clock = [0.]
    key = 'left_arm_shoulder_pan'
    def tick(seconds):
        clock[0] += seconds
    node = SimpleNamespace(_command_lock=threading.Lock(),
        joint_snapshot=lambda: ({key: 0.}, 0., 1),
        send_joint_targets=sent.append, hold_joints=lambda: held.append(True), motion_allowed=lambda: True)
    event = threading.Event()
    assert execute_chunk(node, [{key+'.pos': 100.}], event, coordinated=coordinated).code == 'TOOL_PARAMETERS_INVALID'
    assert not sent
    # A repeated pose with a repeated sensor stamp cannot satisfy four fresh samples.
    result = execute_chunk(node, [{key+'.pos': 0.}], event, clock=lambda: clock[0], sleep=tick,
                           coordinated=coordinated)
    assert result.code == 'JOINT_TARGET_TIMEOUT' and held
    node.joint_snapshot = lambda: ({key: 0.}, 1., 2)
    assert execute_chunk(node, [{key+'.pos': .1}], event, coordinated=coordinated).code == 'JOINT_FEEDBACK_STALE'


@pytest.mark.parametrize('coordinated', [False, True])
def test_passive_gripper_open_stop_has_distinct_tolerance_but_arm_axis_does_not(coordinated):
    clock = [0.]
    stamp = [0]
    target = {"left_arm_gripper.pos": 1.5, "left_arm_shoulder_pan.pos": .5}
    measured = {"left_arm_gripper": 1.457, "left_arm_shoulder_pan": .47}
    node = SimpleNamespace(_command_lock=threading.Lock(),
        joint_snapshot=lambda: (measured, 0., stamp[0]),
        send_joint_targets=lambda _: None, hold_joints=lambda: None,
        motion_allowed=lambda: True)
    def tick(seconds):
        clock[0] += seconds
        stamp[0] += 1
    result = execute_chunk(node, [target], threading.Event(),
                           clock=lambda: clock[0], sleep=tick, coordinated=coordinated)
    assert result.success
    measured["left_arm_shoulder_pan"] = .457
    clock[0] = 0.
    result = execute_chunk(node, [target], threading.Event(),
                           clock=lambda: clock[0], sleep=tick, coordinated=coordinated)
    assert result.code == "JOINT_TARGET_TIMEOUT"


def test_navigation_preparation_can_reenter_its_transaction_but_excludes_other_threads():
    key = 'left_arm_shoulder_pan'
    state = {'time': 0., 'stamp': 0}
    def tick(seconds):
        state['time'] += seconds
        state['stamp'] += 1
    node = SimpleNamespace(_command_lock=threading.RLock(),
        joint_snapshot=lambda: ({key: 0.}, 0., state['stamp']),
        send_joint_targets=lambda _: None, hold_joints=lambda: None, motion_allowed=lambda: True)
    with node._command_lock:
        result = execute_chunk(node, [{key+'.pos': 0.}], threading.Event(),
                               clock=lambda: state['time'], sleep=tick)
        assert result.success
        outcomes = []
        worker = threading.Thread(target=lambda: outcomes.append(
            execute_chunk(node, [{key+'.pos': 0.}], threading.Event())))
        worker.start()
        worker.join(1)
        assert not worker.is_alive() and outcomes[0].code == 'ROBOT_BUSY'


def test_coordinated_ramp_uses_same_fraction_with_each_axis_bounded():
    initial = {'large': .3, 'small': -.2, 'reverse': .1, 'still': .4}
    target = {'large': .51, 'small': -.158, 'reverse': -.005, 'still': .4}
    original = dict(initial)
    commanded = dict(initial)
    for _ in range(9):
        previous = commanded
        commanded = joint_ramp_step(previous, target, coordinated=True)
        assert max(abs(commanded[n]-previous[n]) for n in target) <= .025+1e-15
        fractions = [(commanded[n]-initial[n])/(target[n]-initial[n])
                     for n in ('large', 'small', 'reverse')]
        assert max(fractions)-min(fractions) < 1e-12
        assert commanded['still'] == .4
    assert commanded == target and initial == original
    assert joint_ramp_step(commanded, target, coordinated=True) == target


def test_default_ramp_preserves_independent_axis_path_exactly():
    commanded = {'large': 0., 'small': 0., 'reverse': .1}
    target = {'large': .2, 'small': .01, 'reverse': -.02}
    for _ in range(10):
        expected = {n: commanded[n]+max(-.025, min(.025, goal-commanded[n]))
                    for n, goal in target.items()}
        assert joint_ramp_step(commanded, target) == expected
        assert joint_ramp_step(commanded, target, coordinated=False) == expected
        commanded = expected
    assert commanded == target


@pytest.mark.parametrize('coordinated', [False, True])
@pytest.mark.parametrize('interruption', ['cancel', 'stale', 'motion_disallowed'])
def test_ramp_interruptions_stop_publication_hold_and_release_lock(coordinated, interruption):
    large, small = 'left_arm_shoulder_pan', 'left_arm_wrist_roll'
    state = {'time': 0., 'stamp': 1, 'q': {large: 0., small: 0.}}
    sent, held = [], []
    cancel = threading.Event()

    def snapshot():
        age = .501 if interruption == 'stale' and len(sent) == 2 else 0.
        return dict(state['q']), age, state['stamp']

    def send(target):
        sent.append(dict(target))
        state['q'] = dict(target)

    def tick(seconds):
        state['time'] += seconds
        state['stamp'] += 1
        if len(sent) == 2 and interruption == 'cancel':
            cancel.set()

    node = SimpleNamespace(_command_lock=threading.Lock(), joint_snapshot=snapshot,
                           send_joint_targets=send, hold_joints=lambda: held.append(True),
                           motion_allowed=lambda: not (interruption == 'motion_disallowed' and len(sent) == 2))
    result = execute_chunk(node, [{large+'.pos': .2, small+'.pos': .04}], cancel,
                           coordinated=coordinated, clock=lambda: state['time'], sleep=tick)
    assert result.code == ('JOINT_FEEDBACK_STALE' if interruption == 'stale' else 'CANCELLED')
    assert len(sent) == 2 and held == [True] and not node._command_lock.locked()
    if coordinated:
        assert sent[0][small] == pytest.approx(.005)
        assert sent[1][small] == pytest.approx(.010)
    else:
        assert sent[0][small] == .025
        assert sent[1][small] == .04
