"""Default engine deployment contracts independent of an installed Docker daemon."""
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def module(path):
    spec = importlib.util.spec_from_file_location('gazebo_test_module', ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_gazebo_trial_uses_only_its_selected_asset_root(tmp_path, monkeypatch):
    runner = module('scripts/gazebo_process.py')
    repo = tmp_path / 'repo'
    shared = repo / 'artifacts/sim-assets'
    shared.mkdir(parents=True)
    sentinel = shared / 'existing-world'
    sentinel.write_text('existing world stays untouched')
    isolated = tmp_path / 'trial-assets'
    trial = tmp_path / 'trial'
    calls = []

    class Owner:
        def run(self, args, **kwargs):
            calls.append((args, kwargs))
            return 0

    monkeypatch.setattr(runner, 'ROOT', repo)
    monkeypatch.setattr(runner, 'ProcessOwner', Owner)
    monkeypatch.setattr(runner, 'ensure_image', lambda owner, image: calls.append((['image', image], {})))
    monkeypatch.setattr(runner.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setenv('SIM_STACK_GAZEBO_ASSETS_ROOT', str(isolated))
    monkeypatch.setenv('SIM_STACK_GAZEBO_IMAGE', 'tangying-navigation:isolated-test')
    monkeypatch.setenv('SIM_STACK_GAZEBO_NAVIGATION_PORT', '18942')
    monkeypatch.setenv('ROS_DOMAIN_ID', '92')
    monkeypatch.setenv('TANGYING_NAVIGATION_TOKEN', 'private-parent-token-fixture-should-not-be-serialized')
    monkeypatch.setenv('TANGYING_NAVIGATION_URL', 'http://127.0.0.1:18942')
    monkeypatch.setattr(sys, 'argv', ['gazebo_process.py', '--listen', '127.0.0.1:50462',
                                      '--scene', 'home_furnished', '--artifacts-dir', str(trial)])
    assert runner.main() == 0
    commands = [args for args, _ in calls]
    prepare = next(c for c in commands if any('prepare_home_world.py' in a for a in c))
    furnish = next(c for c in commands if any('prepare_furnished_home.py' in a for a in c))
    export = next(c for c in commands if any('export_gazebo_home.py' in a for a in c))
    assert prepare[-1] == str(isolated)
    assert furnish[-4:] == ['--source', str(isolated / 'aws-small-house'), '--output', str(isolated / 'furnished-home')]
    assert export[-4:] == ['--output', str(isolated / 'xlerobot-home'), '--pack', str(isolated / 'furnished-home')]
    compose, options = next((c, o) for c, o in calls if c[0:2] == ['docker', 'compose'])
    assert options['env']['TANGYING_GAZEBO_ASSETS'] == str(isolated)
    assert options['env']['TANGYING_NAVIGATION_PORT'] == '18942'
    assert options['env']['TANGYING_GAZEBO_RUNTIME_PORT'] == '50462'
    assert options['env']['ROS_DOMAIN_ID'] == '92'
    assert options['env']['TANGYING_NAVIGATION_IMAGE'] == 'tangying-navigation:isolated-test'
    assert compose[3].startswith('tangying-gazebo-')
    override = json.loads((trial / 'compose.json').read_text())
    assert 'TANGYING_NAVIGATION_TOKEN' not in override['services']['gazebo-house']['environment']
    assert 'TANGYING_NAVIGATION_URL' not in override['services']['gazebo-house']['environment']
    assert 'private-parent-token-fixture-should-not-be-serialized' not in (trial / 'compose.json').read_text()
    assert str(trial / 'maps') + ':/data/maps' in override['services']['gazebo-house']['volumes']
    assert sentinel.read_text() == 'existing world stays untouched'
    assert list(shared.iterdir()) == [sentinel]


def test_gazebo_default_assets_stay_under_the_original_repository_root(tmp_path, monkeypatch):
    runner = module('scripts/gazebo_process.py')
    repo = tmp_path / 'repo'
    assets = repo / 'artifacts/sim-assets'
    (assets / 'furnished-home').mkdir(parents=True)
    (assets / 'furnished-home/manifest.json').write_text('{}')
    calls = []

    class Owner:
        def run(self, args, **kwargs):
            calls.append((args, kwargs))
            return 0

    monkeypatch.setattr(runner, 'ROOT', repo)
    monkeypatch.setattr(runner, 'ProcessOwner', Owner)
    monkeypatch.setattr(runner, 'ensure_image', lambda *args: None)
    monkeypatch.setattr(runner.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.delenv('SIM_STACK_GAZEBO_ASSETS_ROOT', raising=False)
    monkeypatch.setattr(sys, 'argv', ['gazebo_process.py', '--listen', '127.0.0.1:50462',
                                      '--scene', 'home_furnished', '--artifacts-dir', str(tmp_path / 'trial')])
    assert runner.main() == 0
    commands = [args for args, _ in calls]
    assert not any('prepare_home_world.py' in a for c in commands for a in c)
    export = next(c for c in commands if any('export_gazebo_home.py' in a for a in c))
    assert export[-4:] == ['--output', str(assets / 'xlerobot-home'), '--pack', str(assets / 'furnished-home')]
    _, options = next((c, o) for c, o in calls if c[0:2] == ['docker', 'compose'])
    assert options['env']['TANGYING_GAZEBO_ASSETS'] == str(assets)


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
