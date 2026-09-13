import math

import numpy as np
import pytest
from tangying_robot_gateway.workspace_planner import WorkspaceEnvelope, plan_workspace

E = WorkspaceEnvelope(.12,.05,.2,.8,.7)

def grid(cells=None, origin=None):
    return {'width': 40,'height': 40,'resolution': .1,'origin': origin or [0,0,0],
                'cells': np.zeros((40,40), dtype=int) if cells is None else cells}

def test_candidates_have_free_connected_paths_but_no_implicit_ik_or_execution():
    result = plan_workspace(grid(), [1,1], [3,3,.9], E)
    assert result['candidates'] and result['requiresKinematicsValidation']
    assert not result['executionAuthorized']
    for candidate in result['candidates']:
        assert .2 <= candidate['reachMeters'] <= .8
        assert len(candidate['path']) > 2
        assert not candidate['kinematicsVerified']

def test_wall_and_unknown_space_cannot_be_crossed():
    cells = np.zeros((40,40),dtype=int)
    cells[:,20] = 100
    assert not plan_workspace(grid(cells),[1,1],[3,3,.9],E)['candidates']
    cells[:,20] = -1
    assert not plan_workspace(grid(cells),[1,1],[3,3,.9],E)['candidates']

def test_ik_rejection_and_rotated_map_origin():
    assert not plan_workspace(grid(),[1,1],[3,3,.9],E, validate_candidate=lambda *_: False)['candidates']
    result = plan_workspace(grid(origin=[10,20,math.pi/2]),[9,21],[7,23,.9],E,
                            validate_candidate=lambda *_: True)
    assert result['candidates'][0]['kinematicsVerified']
    assert not result['executionAuthorized']

def test_invalid_envelope_and_unknown_start_are_refused():
    with pytest.raises(ValueError): WorkspaceEnvelope(.1,0,0,float('nan'),1)
    with pytest.raises(ValueError): plan_workspace(grid(),[0,0],[2,2,1],E)
