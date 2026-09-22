"""Default engine deployment contracts independent of an installed Docker daemon."""
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def module(path):
    spec = importlib.util.spec_from_file_location('gazebo_test_module', ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_source_revision_tracks_changes_even_under_hidden_worktree_parent(tmp_path, monkeypatch):
    runner = module('scripts/gazebo_process.py')
    root = tmp_path / '.codex' / 'repo'
    source = root / 'robot/gateway/tangying_robot_gateway/backend.py'
    source.parent.mkdir(parents=True)
    source.write_text('old')
    monkeypatch.setattr(runner, 'ROOT', root)
    first = runner.source_revision()
    source.write_text('new')
    assert runner.source_revision() != first
    second = runner.source_revision()
    cache = source.parent / '__pycache__' / 'backend.pyc'
    cache.parent.mkdir()
    cache.write_bytes(b'ignored')
    assert runner.source_revision() == second


def test_distinct_scenes_have_distinct_physics_and_camera_contract(tmp_path):
    scenes = module('robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_scenes.py')
    base = ROOT / 'robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf'
    paths = [scenes.compose_scene(name, base, tmp_path / (name+'.sdf')) for name in ('home', 'tabletop', 'home_task')]
    assert len({p.read_bytes() for p in paths}) == 3
    for path in paths:
        world = ET.parse(path).getroot().find('world')
        sensors = world.findall("model[@name='tangying_robot']/link[@name='base_link']/sensor[@type='rgbd_camera']")
        assert {s.get('name') for s in sensors} == {'base_rgbd', 'head_rgbd'}
        robot = world.find("model[@name='tangying_robot']")
        assert float(robot.findtext('pose').split()[2]) == .20
        initialized = {j.get('name'): float(j.get('position')) for j in
                       robot.findall("plugin[@name='tangying::InitialJointPose']/joint")}
        controllers = robot.findall("plugin[@name='gz::sim::systems::JointPositionController']")
        assert len(initialized) == len(controllers) == 12
        for controller in controllers:
            assert initialized[controller.findtext('joint_name')] == float(controller.findtext('initial_position'))
    tabletop = ET.parse(paths[1]).getroot().find('world')
    assert tabletop.find("model[@name='red_cup']/static") is None
    assert tabletop.find("model[@name='living_sofa']") is None
    assert ET.parse(paths[2]).getroot().find("world/model[@name='living_sofa']") is not None


def test_furnished_world_refreshes_robot_without_replacing_house(tmp_path):
    scenes = module('robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_scenes.py')
    base = ROOT / 'robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf'
    source = tmp_path / 'furnished.sdf'
    source.write_text('<sdf version="1.9"><world name="tangying_home"><model name="furniture"/><model name="tangying_robot"><pose>1 2 .2 0 0 0</pose></model></world></sdf>')
    path = scenes.compose_scene('home_furnished', base, tmp_path/'result.sdf', furnished_world=source)
    world = ET.parse(path).getroot().find('world')
    assert world.find("model[@name='furniture']") is not None
    robot = world.find("model[@name='tangying_robot']")
    assert [float(v) for v in robot.findtext('pose').split()] == [1., 2., .2, 0., 0., 0.]
    assert len(robot.findall('joint')) == 16
    assert robot.find("link[@name='front_caster']/pose").text.startswith('0.24 0 -0.11')


def test_map_padding_preserves_all_measurements_and_only_adds_unknown_cells():
    import numpy as np
    padding = module('robot/ros2_ws/src/tangying_navigation/tangying_navigation/map_padding.py')
    data = np.array([[0, 100], [-1, 40]], dtype=np.int8)
    padded, offset = padding.pad_grid(data.ravel(), 2, 2, .25)
    assert offset == .75
    np.testing.assert_array_equal(padded[3:5, 3:5], data)
    outside = padded.copy()
    outside[3:5, 3:5] = -1
    assert np.all(outside == -1)
    # Original cell centers retain their world position after the origin shift.
    assert -offset + 3*.25 == 0
