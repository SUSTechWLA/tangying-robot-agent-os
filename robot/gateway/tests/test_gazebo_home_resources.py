"""Deployment geometry must match the kinematics used to command real joints."""
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from tangying_robot_gateway.arm_kinematics import arm_link_poses, arm_links
from tangying_robot_gateway.gazebo_commissioning import commissioning

ROOT = Path(__file__).resolve().parents[3]


def test_deployed_xlerobot_joint_frames_axes_and_limits_match_controller():
    robot = ET.parse(ROOT/'robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf').getroot().find("world/model[@name='tangying_robot']")
    for side in ('left', 'right'):
        expected = arm_link_poses(side, {link.motor:0. for link in arm_links(side)}, base=np.eye(4))
        for link in arm_links(side):
            values = np.fromstring(robot.find(f"link[@name='{link.link}']/pose").text, sep=' ')
            actual = np.eye(4)
            actual[:3,:3] = Rotation.from_euler('xyz', values[3:]).as_matrix()
            actual[:3,3] = values[:3]
            np.testing.assert_allclose(actual, expected[link.link], atol=2e-5)
            joint = robot.find(f"joint[child='{link.link}']")
            np.testing.assert_allclose(np.fromstring(joint.findtext('axis/xyz'), sep=' '), link.axis)
            assert float(joint.findtext('axis/limit/lower')) == link.range_min
            assert float(joint.findtext('axis/limit/upper')) == link.range_max


def test_commissioning_refuses_changed_geometry_and_path_escape(tmp_path, monkeypatch):
    world = tmp_path/'home.sdf'
    world.write_text('<sdf/>')
    mesh = tmp_path/'mesh.obj'
    mesh.write_text('v 0 0 0')
    document = {'schemaVersion':'robot.home.commissioning.v1',
                'worldRevision':hashlib.sha256(world.read_bytes()).hexdigest(),
                'files':{'mesh.obj':hashlib.sha256(mesh.read_bytes()).hexdigest()}}
    manifest = tmp_path/'commissioning.json'
    manifest.write_text(json.dumps(document))
    monkeypatch.setenv('TANGYING_HOME_COMMISSIONING', str(manifest))
    monkeypatch.setenv('TANGYING_GAZEBO_WORLD', str(world))
    try:
        commissioning.cache_clear()
        assert commissioning()['worldRevision'] == document['worldRevision']
        mesh.write_text('changed')
        commissioning.cache_clear()
        with pytest.raises(ValueError, match='ASSET_REVISION_MISMATCH'):
            commissioning()
        document['files'] = {'../outside.obj':'invalid'}
        manifest.write_text(json.dumps(document))
        commissioning.cache_clear()
        with pytest.raises(ValueError, match='ASSET_REVISION_MISMATCH'):
            commissioning()
    finally:
        commissioning.cache_clear()


def test_position_servo_remains_physics_driven_with_bounded_effort_and_integral():
    robot = ET.parse(ROOT/'robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf').getroot().find("world/model[@name='tangying_robot']")
    controllers = [p for p in robot.findall('plugin') if p.get('name') == 'gz::sim::systems::JointPositionController']
    assert len(controllers) == 14
    for controller in controllers:
        assert controller.findtext('use_velocity_commands', 'false') == 'false'
        assert float(controller.findtext('cmd_max')) == 20
        assert float(controller.findtext('cmd_min')) == -20
        assert float(controller.findtext('i_max')) == 1
        assert float(controller.findtext('i_min')) == -1
        assert float(controller.findtext('i_gain')) == 2
