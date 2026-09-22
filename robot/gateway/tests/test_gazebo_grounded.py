import copy
import time
from types import SimpleNamespace

import pytest
from tangying_robot_gateway.gazebo_grounded import collect, contracts, parameters
from tangying_robot_gateway.grounded.store import EvidenceStore
from tangying_robot_gateway.grounded.verifier import RuntimeVerifier
from tangying_robot_gateway.runtime import Command, validate_result


def command(name='manipulation.pick', params=None, target='red-cup'):
    return Command(schema_version='robot.v1', command_id='act', task_id='task', robot_id='gz',
                   capability=name, target_ref=target, parameters=params or {'objectId': 'red-cup'})


def evidence(tmp_path, *, attached=True, held='red_cup', lift=.1):
    state = {'mode': 'sim_suction', 'sequence': 0, 'attached': attached, 'held': held,
             'side': 'left', 'commandId': 1, 'objects': {'red_cup': [0., 0., 1.+lift, 1., 0., 0., 0.],
             'tray_floor': [0., 0., 1.025, 1., 0., 0., 0.]}, 'tips': {'left': [0., 0., 1.2]}}
    def snapshot():
        state['sequence'] += 1
        return copy.deepcopy(state), time.monotonic_ns()
    backend = SimpleNamespace(node=SimpleNamespace(suction_evidence_snapshot=snapshot),
        manipulation=SimpleNamespace(acquisition={'object': 'red-cup', 'task': 'task', 'z': 1.}))
    store = EvidenceStore(tmp_path)
    return backend, store


@pytest.mark.parametrize('attached,held,lift,verdict', [
    (True, 'red_cup', .1, 'VERIFIED'), (True, 'red_cup', 0., 'FALSIFIED'),
    (True, 'blue_bottle', .1, 'FALSIFIED'), (False, '', .1, 'FALSIFIED'),
])
def test_suction_gvf_needs_identity_lift_and_physics_not_ack(tmp_path, attached, held, lift, verdict):
    backend, store = evidence(tmp_path, attached=attached, held=held, lift=lift)
    start = time.monotonic_ns()
    stream = collect(backend, command=command(), action_id='act', start_ns=start,
                     edge_boot_id='boot', store=store, phase='post')
    assert len(stream) == 4
    assert all(ref.kind != 'force' for sample in stream for ref in sample.evidence_refs)
    args = {'action_id': 'act', 'edge_boot_id': 'boot', 'start_ns': start, 'end_ns': time.monotonic_ns(),
            'params': {'object': 'red-cup'}}
    verifier = RuntimeVerifier(store.exists)
    assert verifier.verify(contracts()['manipulation.pick'], stream, **args).verdict == verdict
    # A repeated frame or missing evidence cannot establish a stable grasp.
    assert verifier.verify(contracts()['manipulation.pick'], [stream[-1]]*4, **args).verdict == 'UNKNOWN'


def test_place_contract_binds_payload_not_destination_as_object(tmp_path):
    backend, store = evidence(tmp_path, attached=False)
    c = command('manipulation.place', {}, 'right-bin')
    bound = parameters(backend, c)
    assert bound == {'objectId': 'red-cup', 'destinationId': 'right-bin', 'gripper': 'left'}
    c = command('manipulation.place', bound, 'right-bin')
    start = time.monotonic_ns()
    stream = collect(backend, command=c, action_id='act', start_ns=start,
                     edge_boot_id='boot', store=store, phase='post')
    report = RuntimeVerifier(store.exists).verify(contracts()['manipulation.place'], stream,
        action_id='act', edge_boot_id='boot', start_ns=start, end_ns=time.monotonic_ns(),
        params={'object': 'red-cup', 'container': 'right-bin'})
    assert report.verdict == 'VERIFIED'
    backend.manipulation.acquisition['task'] = 'other'
    assert parameters(backend, c)['objectId'] == ''


def test_controller_result_is_valid_at_public_tool_boundary():
    from robot.gateway.tests.test_gazebo_manipulation import controller
    state = {'attached': True, 'held': 'red_cup', 'objects': {'red_cup': [0., 0., 1.1]},
             'tips': {'left': [0., 0., 1.15]}}
    result = controller(state).verify_grasp('red-cup')
    validate_result(result)
    assert result.success and 'physicsJson' in result.payload
