import threading
from types import SimpleNamespace

from tangying_robot_gateway.gazebo_actuation import execute_chunk


def test_joint_receipt_requires_measured_convergence_and_stops_on_cancellation():
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
                           clock=lambda: state['time'], sleep=tick)
    assert result.success and abs(state['q']-.2) <= .04 and state['hold'] == 0
    assert abs(state['last']-.2) < 1e-9  # final controller target remains the requested goal
    event.set()
    assert execute_chunk(node, [{key+'.pos': .4}], event).code == 'CANCELLED'
    assert state['hold'] == 1 and not node._command_lock.locked()


def test_frozen_feedback_never_becomes_success_and_out_of_range_never_sends():
    sent, held = [], []
    clock = [0.]
    key = 'left_arm_shoulder_pan'
    def tick(seconds):
        clock[0] += seconds
    node = SimpleNamespace(_command_lock=threading.Lock(),
        joint_snapshot=lambda: ({key: 0.}, 0., 1),
        send_joint_targets=sent.append, hold_joints=lambda: held.append(True), motion_allowed=lambda: True)
    event = threading.Event()
    assert execute_chunk(node, [{key+'.pos': 100.}], event).code == 'TOOL_PARAMETERS_INVALID'
    assert not sent
    # A repeated pose with a repeated sensor stamp cannot satisfy four fresh samples.
    result = execute_chunk(node, [{key+'.pos': 0.}], event, clock=lambda: clock[0], sleep=tick)
    assert result.code == 'JOINT_TARGET_TIMEOUT' and held
    node.joint_snapshot = lambda: ({key: 0.}, 1., 2)
    assert execute_chunk(node, [{key+'.pos': .1}], event).code == 'JOINT_FEEDBACK_STALE'
