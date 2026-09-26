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


def test_home_aliases_resolve_the_same_xlerobot_house(tmp_path):
    scenes = module('robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_scenes.py')
    base = ROOT / 'robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf'
    paths = [scenes.compose_scene(name,base,tmp_path/(name+'.sdf')) for name in scenes.SCENES]
    assert len({path.read_bytes() for path in paths}) == 1
    world = ET.parse(paths[0]).getroot().find('world')
    robot = world.find("model[@name='tangying_robot']")
    assert robot.find("link[@name='left_wheel']") is None
    assert robot.find("link[@name='right_wheel']") is None
    assert robot.find("joint[@name='base_slide_x']") is not None
    assert robot.find("joint[@name='base_slide_y']") is not None
    assert robot.find("joint[@name='base_yaw']") is not None
    wheels = [link for link in robot.findall('link') if 'VersaHub' in link.get('name')]
    assert len(wheels) == 3
    assert world.find("model[@name='ceramic_mug']/static").text == 'false'
    assert world.find("model[@name='red_cup']") is None
    for name in ('living_room','home_corridor','kitchen','bedroom','bathroom','kitchen_tray'):
        assert world.find(f"model[@name='{name}']") is not None
    assert len(robot.findall("plugin[@name='gz::sim::systems::JointPositionController']")) == 14


def test_scene_resolution_refuses_uncommissioned_robot_and_retired_tabletop(tmp_path):
    import pytest
    scenes = module('robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_scenes.py')
    base = ROOT / 'robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf'
    source = tmp_path/'wrong.sdf'
    source.write_text('<sdf version="1.9"><world name="home"><model name="different_robot"/></world></sdf>')
    with pytest.raises(ValueError,match='PROTOTYPE_MISMATCH'):
        scenes.compose_scene('home_furnished',base,tmp_path/'result.sdf',furnished_world=source)
    with pytest.raises(ValueError,match='retired'):
        scenes.compose_scene('tabletop',base,tmp_path/'result.sdf')


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
