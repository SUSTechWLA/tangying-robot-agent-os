"""Explicit simulation-only contracts; fixed joints never fabricate force data."""
from __future__ import annotations

import time

import numpy as np

from .gazebo_perception import DESTINATION_MODELS, OBJECT_MODELS
from .grounded.model import ActionContract, Expression, canonical
from .grounded.verifier import load_contracts

TOOLS = {'manipulation.pick', 'verify_grasp', 'manipulation.place', 'verify_placement'}


def contracts():
    catalog = load_contracts()
    for tool in TOOLS:
        holding = tool in {'manipulation.pick', 'verify_grasp'}
        catalog[tool] = ActionContract(name=tool, timeout_s=60., window_s=1., max_gap_s=.3,
            postconditions=[Expression(op='stable', frames=3, children=[Expression(
                predicate='SimSuctionHolding' if holding else 'SimSuctionPlaced',
                args={'object': '$object'} if holding else {'object': '$object', 'container': '$container'})])])
    catalog['navigation.pre_position'] = catalog['navigation.navigate'].model_copy(
        update={'name': 'navigation.pre_position'})
    return catalog


def parameters(backend, command):
    result = {}
    if command.capability == 'manipulation.pick':
        result['objectId'] = command.target_ref
    elif command.capability == 'manipulation.place':
        acquisition = backend.manipulation.acquisition or {}
        result.update(objectId=acquisition.get('object', '') if acquisition.get('task') == command.task_id else '',
                      destinationId=command.target_ref)
    elif command.capability == 'navigation.pre_position':
        import math

        from .gazebo_runtime import leveled_base_pose
        goal = leveled_base_pose(backend.node.runtime.base_pose)
        yaw = command.parameters.get('alignYaw', 2*math.atan2(goal[6], goal[3]))
        goal[3:] = [math.cos(yaw/2), 0., 0., math.sin(yaw/2)]
        result['goalPose'] = goal
    return result


def collect(backend, *, command, action_id, start_ns, edge_boot_id, store, phase):
    if phase != 'post':
        return []
    obj = command.parameters.get('objectId', command.target_ref)
    dest = command.parameters.get('destinationId', '')
    if obj not in OBJECT_MODELS:
        return []
    holding = command.capability in {'manipulation.pick', 'verify_grasp'}
    acquisition = backend.manipulation.acquisition or {}
    if holding and (acquisition.get('object') != obj or acquisition.get('task') != command.task_id):
        return []
    rows, seen, previous = [], set(), None
    deadline = time.monotonic()+.9
    while time.monotonic() < deadline and len(rows) < 4:
        state, received = backend.node.suction_evidence_snapshot()
        if received <= start_ns or state['sequence'] in seen:
            time.sleep(.015)
            continue
        seen.add(state['sequence'])
        p = np.asarray(state['objects'][OBJECT_MODELS[obj]][:3])
        tracked = p-np.asarray(state['tips']['left'][:3]) if holding else p
        values = {'mode': state['mode'], 'attached': state['attached']}
        if holding:
            values.update(held_object_id=next((key for key, model in OBJECT_MODELS.items() if model == state['held']), ''),
                          lift_m=float(p[2]-acquisition['z']))
        elif dest in DESTINATION_MODELS:
            delta = p-np.asarray(state['objects'][DESTINATION_MODELS[dest]][:3])
            pose = state['objects'][OBJECT_MODELS[obj]]
            values.update(container_id=dest, xy_error_m=float(np.max(np.abs(delta[:2]))),
                          height_error_m=float(abs(delta[2]-.075)), upright_cos=float(1-2*(pose[4]**2+pose[5]**2)))
        if previous is not None:
            values['displacement_m'] = float(np.linalg.norm(tracked-previous))
        previous = tracked
        pose_ref = store.put(canonical(state).encode(), 'pose', {'source': 'gazebo_physics', 'mode': 'sim_suction'})
        grip_ref = store.put(canonical({key: state[key] for key in ('attached', 'held', 'side', 'commandId', 'sequence')}).encode(),
                             'gripper', {'source': 'gazebo_detachable_joint', 'mode': 'sim_suction'})
        rows.append(store.record_sample(sample_id=str(state['sequence']), source_id='gazebo/suction',
            edge_boot_id=edge_boot_id, edge_monotonic_ts_ns=received, action_id=action_id,
            object_id=obj, confidence=1., values=values, evidence_refs=[pose_ref, grip_ref]))
    return rows
