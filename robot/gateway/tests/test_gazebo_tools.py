import copy
import hashlib
import json

import numpy as np
import pytest
from tangying_robot_gateway.arm_kinematics import arm_links, chain_poses
from tangying_robot_gateway.gazebo_manipulation import solve_tip
from tangying_robot_gateway.gazebo_tools import tool_commissioning, tool_pose


def test_fixed_finger_tool_pose_is_independent_of_moving_jaw():
    tool, revision = tool_commissioning()
    assert len(revision) == 64
    links = arm_links('left')
    joints = dict(zip([link.motor for link in links], (0., .98, 2.36, -1.38, 0., 0.), strict=True))
    closed = tool_pose(chain_poses(links, joints, base=np.eye(4)), tool)
    joints[links[-1].motor] = tool['gripperOpenRad']
    opened = tool_pose(chain_poses(links, joints, base=np.eye(4)), tool)
    assert np.allclose(opened, closed)
    fixed = chain_poses(links, joints, base=np.eye(4))[4]
    assert np.allclose(opened[:3, 3], (fixed @ np.r_[tool['offsetM'], 1.])[:3])
    solved = solve_tip(opened[:3, 3], joints, np.eye(4),
                       upright=opened[:3, :3].T @ [0., 0., 1.],
                       fixed_gripper=tool['gripperOpenRad'], tool=tool)
    actual = tool_pose(chain_poses(links, solved, base=np.eye(4)), tool)
    assert np.linalg.norm(actual[:3, 3]-opened[:3, 3]) < .008
    assert solved[links[-1].motor] == tool['gripperOpenRad']


@pytest.mark.parametrize('field,value', [('offsetM', [float('nan'), 0, 0]),
    ('maxAttachDistanceM', .091), ('contactHeightM', .1), ('gripperOpenRad', 2.1),
    ('parentLinkIndex', 6)])
def test_invalid_tool_commissioning_refuses_motion_configuration(tmp_path, monkeypatch, field, value):
    tool, _ = tool_commissioning()
    candidate = copy.deepcopy(tool)
    candidate[field] = value
    path = tmp_path/'tool.json'
    path.write_text(json.dumps(candidate))
    monkeypatch.setenv('TANGYING_GAZEBO_TOOL_COMMISSIONING', str(path))
    tool_commissioning.cache_clear()
    try:
        with pytest.raises(ValueError, match='TOOL_COMMISSIONING_'):
            tool_commissioning()
    finally:
        tool_commissioning.cache_clear()


def test_tool_content_revision_covers_whole_source_bytes(tmp_path, monkeypatch):
    tool, _ = tool_commissioning()
    raw = json.dumps(tool).encode()
    path = tmp_path/'tool.json'
    path.write_bytes(raw)
    monkeypatch.setenv('TANGYING_GAZEBO_TOOL_COMMISSIONING', str(path))
    tool_commissioning.cache_clear()
    try:
        assert tool_commissioning()[1] == hashlib.sha256(raw).hexdigest()
    finally:
        tool_commissioning.cache_clear()


def test_physics_plugin_tool_configuration_must_match_controller():
    from robot.gateway.tests.test_gazebo_manipulation import controller
    tool, _ = tool_commissioning()
    state = {'attached': False, 'toolCommissioning': copy.deepcopy(tool)}
    c = controller(state)
    c.tool = tool
    assert c.state() is state
    state['toolCommissioning']['offsetM'][0] += .001
    with pytest.raises(ValueError, match='TOOL_COMMISSIONING_MISMATCH'):
        c.state()


@pytest.mark.parametrize('destination,success', [([2.46, 3.335, .73], True), ([8., 8., .73], False)])
def test_grasp_planning_checks_destination_workspace_before_acquisition(destination, success):
    import threading
    from types import SimpleNamespace

    from tangying_robot_gateway.gazebo_manipulation import GazeboManipulation, pose_matrix
    from tangying_robot_gateway.runtime import Command, Observation, SceneEntity
    base_pose = [2.05, 3., .035, .7581022795354195, 0., 0., .6521356712856614]
    base = pose_matrix(base_pose)
    joints = {link.motor: value for side in ('left', 'right')
              for link, value in zip(arm_links(side), (0., 3.1, 1., 0., 0., 0.), strict=True)}
    node = SimpleNamespace(joint_snapshot=lambda: (joints, 0., 1),
                           runtime=SimpleNamespace(base_pose=base, robot_id='test',
                                                   calibration_revision='recorded-calibration'))
    c = GazeboManipulation(SimpleNamespace(node=node, home=True, cancel_event=threading.Event()))
    entities = [SceneEntity('ceramic-mug', 'cup', {'recognition': 'rgbd_metric_shape'},
                            [2.27, 3.41, .79104, 1., 0., 0., 0.], .9),
                SceneEntity('kitchen-tray', 'storage_bin', {}, [*destination, 1., 0., 0., 0.], .9)]
    frame = Observation('current', 1000, 1_000_000_000,
        robot_state={'base_pose': base_pose, 'perception': {
            'calibration_revision': 'recorded-calibration', 'sensor_stamp_ns': '1000000000'}},
        entities=entities, reconstruction={'robotId': 'test', 'entities': []})
    c.capture = lambda: (frame, {entity.entity_id: entity for entity in entities})
    result = c.execute(Command(schema_version='robot.v1',command_id='plan',task_id='task',
        capability='plan_grasp', robot_id='test', catalog_revision='recorded-catalog',
        parameters={'objectId':'ceramic-mug','destinationId':'kitchen-tray'}))
    assert result.success is success
    assert (c.plan is not None) is success
    if not success:
        assert result.code == 'GRASP_TARGET_UNREACHABLE'


def test_payload_planning_rotates_the_acquired_offset_with_the_tool():
    from scipy.spatial.transform import Rotation

    from robot.gateway.tests.test_gazebo_manipulation import controller
    links = arm_links('left')
    tool,_ = tool_commissioning()
    joints = dict(zip([l.motor for l in links],(.1,2.,2.,0.,0.,1.5),strict=True))
    tip = tool_pose(chain_poses(links,joints,base=np.eye(4)),tool)
    local = np.array([.012,-.006,.078])
    cup = tip[:3,3]-tip[:3,:3]@local
    quat = Rotation.from_matrix(tip[:3,:3]).as_quat()
    c = controller({})
    c.tool = tool
    payload = c.payload_frame({'tips':{'left':[*tip[:3,3],quat[3],*quat[:3]]},
                               'objects':{'red_cup':cup.tolist()}},'red-cup')
    assert np.allclose(tool_pose(chain_poses(links,joints,base=np.eye(4)),payload)[:3,3],cup)
    joints[links[0].motor] += .8
    rotated = tool_pose(chain_poses(links,joints,base=np.eye(4)),tool)
    expected = rotated[:3,3]-rotated[:3,:3]@local
    assert np.allclose(tool_pose(chain_poses(links,joints,base=np.eye(4)),payload)[:3,3],expected)
    naive = rotated[:3,3]-(tip[:3,3]-cup)
    assert np.linalg.norm(expected-naive) > .005
