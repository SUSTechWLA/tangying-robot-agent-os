import math
from dataclasses import replace

import numpy as np
import pytest
from tangying_robot_gateway.map_catalog import MapCatalog
from tangying_robot_gateway.map_pipeline import PointCloud, build_map
from tangying_robot_gateway.tools.mapping import build_mapping_tools
from tangying_robot_gateway.workspace_planner import WorkspaceEnvelope


def test_map_bound_semantic_tool_detects_tamper_and_wrong_robot(tmp_path):
    package = tmp_path/'home'
    cloud = PointCloud(np.array([[0,0,0],[4,4,2]],dtype=np.float32), np.zeros((2,3),dtype=np.uint8))
    manifest = build_map(package, map_id='home',robot_id='r1',cloud=cloud,
        calibration_revision='a'*64, lod_levels=1,
        occupancy_grid={'width': 40,'height': 40,'resolution': .1,'origin': [0,0,0],'cells': np.zeros((40,40),dtype=int)},
        semantic_workspaces=[{'name': '厨房台面','aliases': ['kitchen counter'],'target': [3,3,.9]}])
    context = {'map_id': 'home','robot_id': 'r1','calibration_revision': 'a'*64,
        'map_revision': manifest['hash'],'start_xy': [1,1],'envelope': WorkspaceEnvelope(.12,.05,.2,.8,.7)}
    catalog = MapCatalog(tmp_path)
    tool = build_mapping_tools(catalog,lambda: {**context,'localization_fresh':True})[0]
    result = tool.execute(location_name='kitchen counter')
    assert result.success and result.data['candidates']
    assert not result.data['executionAuthorized']
    with pytest.raises(ValueError, match='mismatch'):
        catalog.plan(location_name='厨房台面',**{**context,'robot_id':'r2'})
    with pytest.raises(ValueError, match='mismatch'):
        catalog.plan(location_name='厨房台面',**{**context,'map_revision':'b'*64})
    with pytest.raises(ValueError, match='active map revision'):
        catalog.plan(location_name='厨房台面',**{**context,'map_revision':None})
    # Re-sign a structurally valid package whose Nav2 interpretation differs.
    # Integrity alone must not make the planner reinterpret occupied as free.
    import copy
    import json

    from tangying_robot_gateway.map_manifest import file_sha256, manifest_hash, save_manifest
    config_path = package/'navigation/map.yaml'
    original = config_path.read_bytes()
    config = json.loads(original)
    config['occupied_thresh'], config['free_thresh'] = .001, 0
    config_path.write_text(json.dumps(config))
    modified = copy.deepcopy(manifest)
    modified['artifacts']['navigation']['bytes'] = config_path.stat().st_size
    modified['artifacts']['navigation']['sha256'] = file_sha256(config_path)
    modified['hash'] = manifest_hash(modified)
    save_manifest(package, modified)
    with pytest.raises(ValueError, match='canonical'):
        catalog.plan(location_name='厨房台面', **{**context,'map_revision':modified['hash']})
    config_path.write_bytes(original)
    save_manifest(package, manifest)
    (package/'semantics.json').write_text('{}')
    assert not tool.execute(location_name='厨房台面').success


def _object_package(tmp_path, *, map_id='home', objects=None):
    import json as _json
    package = tmp_path / map_id
    cloud = PointCloud(np.array([[0, 0, 0], [4, 4, 2]], dtype=np.float32),
                       np.zeros((2, 3), dtype=np.uint8))
    document = {'schemaVersion': 'map.objects.v1', 'mapId': map_id, 'frameId': 'map',
                'calibrationRevision': 'a' * 64, 'associationGateM': .12, 'maxAgeMs': 900_000,
                'observedFrames': 3, 'sightings': 3, 'objects': objects or []}
    manifest = build_map(package, map_id=map_id, robot_id='r1', cloud=cloud,
                         calibration_revision='a' * 64, lod_levels=1,
                         occupancy_grid={'width': 40, 'height': 40, 'resolution': .1,
                                         'origin': [0, 0, 0], 'cells': np.zeros((40, 40), dtype=int)},
                         semantic_objects=document)
    assert _json.loads((package / 'objects.json').read_text())['schemaVersion'] == 'map.objects.v1'
    return manifest


def _mug(age_ms, *, map_id='home', category='cup', attributes=None, identifier='cup-000'):
    return {'id': identifier, 'category': category, 'attributes': attributes or {'color': 'white'},
            'pose': [2.0, 3.0, .85], 'confidence': .9, 'sightings': 2,
            'firstSeenUnixMs': 1_000_000, 'lastSeenUnixMs': 1_000_000,
            'evidenceFrameId': 'map', 'mapRevision': map_id, 'ageMs': age_ms}


def test_object_recall_is_ranked_by_age_and_filters_by_attribute(tmp_path):
    now = 1_000_000
    objects = [_mug(0, identifier='old'), _mug(0, identifier='new'),
               _mug(0, identifier='blue', attributes={'color': 'blue'})]
    objects[0]['lastSeenUnixMs'] = now - 60_000     # a minute old
    objects[1]['lastSeenUnixMs'] = now - 1_000      # a second old
    objects[2]['lastSeenUnixMs'] = now - 500
    _object_package(tmp_path, objects=objects)
    catalog = MapCatalog(tmp_path)

    found = catalog.recall_objects('home', robot_id='r1', calibration_revision='a' * 64,
                                   category='cup', now_unix_ms=now)
    assert [item['id'] for item in found] == ['blue', 'new', 'old']
    assert found[0]['ageMs'] == 500 and found[0]['frameId'] == 'map'

    filtered = catalog.recall_objects('home', robot_id='r1', calibration_revision='a' * 64,
                                      category='cup', attributes={'color': 'white'},
                                      now_unix_ms=now, max_age_ms=10_000)
    assert [item['id'] for item in filtered] == ['new'], "age and attributes both apply"

    assert catalog.recall_objects('home', robot_id='r1', calibration_revision='a' * 64,
                                  category='bottle', now_unix_ms=now) == []
    with pytest.raises(ValueError, match='category'):
        catalog.recall_objects('home', robot_id='r1', calibration_revision='a' * 64, category='  ')


def test_recall_refuses_a_foreign_layer_and_a_clock_from_the_future(tmp_path):
    _object_package(tmp_path, objects=[_mug(0)])
    catalog = MapCatalog(tmp_path)
    with pytest.raises(ValueError, match='different calibration|mismatch'):
        catalog.recall_objects('home', robot_id='r1', calibration_revision='b' * 64, category='cup')
    # A sighting stamped after "now" is a clock disagreement, not fresh evidence.
    future = [_mug(0, identifier='future')]
    future[0]['lastSeenUnixMs'] = 2_000_000
    _object_package(tmp_path, map_id='future', objects=future)
    assert catalog.recall_objects('future', robot_id='r1', calibration_revision='a' * 64,
                                  category='cup', now_unix_ms=1_000_000) == []


def test_a_map_published_before_the_object_layer_simply_recalls_nothing(tmp_path):
    package = tmp_path / 'old'
    cloud = PointCloud(np.array([[0, 0, 0], [4, 4, 2]], dtype=np.float32),
                       np.zeros((2, 3), dtype=np.uint8))
    build_map(package, map_id='old', robot_id='r1', cloud=cloud, calibration_revision='a' * 64,
              lod_levels=1,
              occupancy_grid={'width': 40, 'height': 40, 'resolution': .1, 'origin': [0, 0, 0],
                              'cells': np.zeros((40, 40), dtype=int)})
    catalog = MapCatalog(tmp_path)
    assert catalog.recall_objects('old', robot_id='r1', calibration_revision='a' * 64,
                                  category='cup') == []


def test_the_recall_tool_fails_closed_without_a_provider_and_reports_age_with_one(tmp_path):
    from tangying_robot_gateway.tools.objects import build_object_memory_tools

    tool = build_object_memory_tools(None)[0]
    refused = tool.execute(object_name='cup')
    assert not refused.success and refused.recoverable is True
    assert 'no map object provider' in refused.error_message

    now = 1_000_000
    _object_package(tmp_path, objects=[_mug(0)])
    catalog = MapCatalog(tmp_path)
    wired = build_object_memory_tools(
        lambda name, attributes, max_age_ms: catalog.recall_objects(
            'home', robot_id='r1', calibration_revision='a' * 64, category=name,
            attributes=attributes, max_age_ms=max_age_ms, now_unix_ms=now))[0]
    result = wired.execute(object_name='cup', max_age_s=300)
    assert result.success and result.data['found'] is True
    assert result.data['isCurrentObservation'] is False
    assert result.data['best']['ageMs'] == 0
    assert result.data['best']['pose'] == [2.0, 3.0, .85]
    missing = wired.execute(object_name='bottle')
    assert missing.success and missing.data['found'] is False
    for bad in ({'object_name': ''}, {'object_name': 'cup', 'max_age_s': 0},
                {'object_name': 'cup', 'attributes': {'color': 3}}):
        assert not wired.execute(**bad).success


def test_work_area_planning_refuses_without_a_provider_and_names_the_pose_it_drove_to(tmp_path):
    import sys
    from pathlib import Path

    from tangying_robot_gateway.semantic_map import SemanticMap
    from tangying_robot_gateway.tools import build_registry

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / "tool_layer"))
    from fake_adapter import FakeRobotAdapter

    bare = build_registry(FakeRobotAdapter(), SemanticMap.from_file())
    refusal = bare.require("plan_work_area").execute(location_name="kitchen")
    assert not refusal.success and "no deployment map provider" in refusal.error_message

    package = tmp_path / 'home'
    cloud = PointCloud(np.array([[0, 0, 0], [4, 4, 2]], dtype=np.float32),
                       np.zeros((2, 3), dtype=np.uint8))
    manifest = build_map(package, map_id='home', robot_id='r1', cloud=cloud,
                         calibration_revision='a' * 64, lod_levels=1,
                         occupancy_grid={'width': 40, 'height': 40, 'resolution': .1,
                                         'origin': [0, 0, 0], 'cells': np.zeros((40, 40), dtype=int)},
                         semantic_workspaces=[{'name': '厨房台面', 'aliases': ['kitchen counter'],
                                               'target': [3, 3, .9]}])
    context = {'map_id': 'home', 'robot_id': 'r1', 'calibration_revision': 'a' * 64,
               'map_revision': manifest['hash'], 'start_xy': [1, 1],
               'envelope': WorkspaceEnvelope(.12, .05, .2, .8, .7)}
    catalog = MapCatalog(tmp_path)
    adapter = FakeRobotAdapter()
    registry = build_registry(adapter, SemanticMap.from_file(), map_catalog=catalog,
                              planning_context=lambda: {**context, 'localization_fresh': True})
    planned = registry.require("plan_work_area").execute(location_name="kitchen counter")
    assert planned.success and planned.data["candidateCount"] >= 1
    candidate = planned.data['candidates'][0]['basePose']

    def robot_stops_at(pose):
        """A base that ends up at ``pose`` - i.e. one that did what it was told."""

        adapter.observation = replace(
            adapter.observation,
            robot_state={**dict(adapter.observation.robot_state), "base_pose": list(pose)})

    def drive(candidate_index=0):
        return registry.require("navigate_to_work_area").execute(
            location_name="kitchen counter", candidate_index=candidate_index)

    # The composite drives the *candidate* and reports that pose - no hidden goal
    # adjustment - and then judges the arrival itself from a fresh capture. Here
    # the base ends up exactly where it was sent, so both halves of the verdict
    # pass and the tool is allowed to say so.
    robot_stops_at(candidate)
    driven = drive()
    assert driven.success, driven.error_message
    assert driven.data["final_pose"] == candidate
    assert driven.data["steps"][0]["tool"] == "plan_work_area"
    assert driven.data["steps"][1]["tool"] == "navigate_to_pose"
    assert driven.data["steps"][2]["tool"] == "get_current_pose"
    assert driven.data["arrival_verified"] is True
    assert driven.data["workspace"] == "厨房台面"
    verdict = driven.data["operable_arrival"]
    assert verdict["passed"] is True and verdict["inReach"] is True
    assert verdict["reachM"] == pytest.approx(
        planned.data["candidates"][0]["reachMeters"], abs=1e-6)

    # A base that stops short is a *following* failure: the code is the runtime's
    # own arrival code and it is retryable, because re-driving is the repair.
    robot_stops_at([candidate[0] + 0.5, candidate[1], candidate[2], 1.0, 0.0, 0.0, 0.0])
    short = drive()
    assert not short.success
    # `error_code` is the shared standard word; the runtime's own code rides
    # alongside it, which is what a caller matches on for the specific repair.
    assert short.data["runtime_code"] == "NAV_ARRIVAL_MISMATCH"
    assert short.recoverable is True
    assert short.data["arrival_verified"] is False
    assert short.data["operable_arrival"]["poseMatched"] is False
    # The plan is still reported, because the caller retries against the same
    # candidate rather than re-planning a work area that was never the problem.
    assert short.data["final_pose"] == candidate

    out_of_range = drive(candidate_index=9)
    assert not out_of_range.success and "candidate_index" in out_of_range.error_message


def test_the_composite_commands_the_full_planned_heading(tmp_path):
    """A half-angle quaternion needs the factor of two, and this is where it bit.

    The planned pose faces its work target. Reading the heading as ``atan2(qz, qw)``
    commands *half* of it, so the base arrives at the right position facing the wrong
    way - and then the arrival check, which reads the yaw correctly, blames the
    controller for a heading the planner never asked for.
    """

    import sys
    from pathlib import Path

    from tangying_robot_gateway.semantic_map import SemanticMap
    from tangying_robot_gateway.tools import build_registry

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests" / "tool_layer"))
    from fake_adapter import FakeRobotAdapter

    package = tmp_path / 'home'
    cloud = PointCloud(np.array([[0, 0, 0], [4, 4, 2]], dtype=np.float32),
                       np.zeros((2, 3), dtype=np.uint8))
    manifest = build_map(package, map_id='home', robot_id='r1', cloud=cloud,
                         calibration_revision='a' * 64, lod_levels=1,
                         occupancy_grid={'width': 40, 'height': 40, 'resolution': .1,
                                         'origin': [0, 0, 0], 'cells': np.zeros((40, 40), dtype=int)},
                         semantic_workspaces=[{'name': '台面', 'aliases': ['counter'],
                                               'target': [3, 3, .9]}])
    context = {'map_id': 'home', 'robot_id': 'r1', 'calibration_revision': 'a' * 64,
               'map_revision': manifest['hash'], 'start_xy': [1, 1],
               'envelope': WorkspaceEnvelope(.12, .05, .2, .8, .7)}
    adapter = FakeRobotAdapter()
    registry = build_registry(adapter, SemanticMap.from_file(), map_catalog=MapCatalog(tmp_path),
                              planning_context=lambda: {**context, 'localization_fresh': True})
    planned = registry.require("plan_work_area").execute(location_name="counter")
    assert planned.success and planned.data["candidates"]
    pose = planned.data["candidates"][0]["basePose"]
    # The candidate faces *towards* the target at (3, 3), so the heading is the
    # direction from the base to the target, not the other way round.
    expected = math.atan2(3.0 - pose[1], 3.0 - pose[0])

    registry.require("navigate_to_work_area").execute(location_name="counter")
    commanded = [call.parameters for call in adapter.calls
                 if call.kind == "execute" and "goalPose" in call.parameters]
    assert commanded, "the composite never commanded a motion"
    # What the composite actually asked the base to face.
    goal = commanded[-1]["goalPose"]
    assert goal == pytest.approx(pose)
    # The adapter is handed the pose; the heading it encodes must be the planned one.
    yaw = 2.0 * math.atan2(pose[6], pose[3])
    assert yaw == pytest.approx(expected, abs=1e-6)
    # ...and the test only discriminates if the buggy reading differs from it. A
    # target dead ahead would make the two agree and the assertion above vacuous.
    half_angle = math.atan2(pose[6], pose[3])
    assert abs(half_angle - expected) > 0.2, (
        "pick a target the base does not already face: this heading hides the bug")


def test_a_map_under_a_group_directory_opens_from_the_catalog_root(tmp_path):
    """The reference scene ships its surveys under a group; a plain survey does not.

    Both are the same kind of package, and joining the root and the id found only the
    second kind - so a grouped map answered "manifest.json does not exist" while
    sitting right there on disk. From the console that reads as a lost map, and it was
    the reason every saved map of the reference house was unusable with the default
    map root.
    """

    group = tmp_path / "furnished-home"
    group.mkdir()
    cloud = PointCloud(np.array([[0, 0, 0], [4, 4, 2]], dtype=np.float32),
                       np.zeros((2, 3), dtype=np.uint8))
    grid = {'width': 40, 'height': 40, 'resolution': .1, 'origin': [0, 0, 0],
            'cells': np.zeros((40, 40), dtype=int)}
    # One package directly in the root and one one level down, so the test proves the
    # lookup searches rather than that it happens to find a single layout.
    flat = build_map(tmp_path / "flat", map_id='flat', robot_id='r1', cloud=cloud,
                     calibration_revision='a' * 64, lod_levels=1, occupancy_grid=grid)
    nested = build_map(group / "nested", map_id='nested', robot_id='r1', cloud=cloud,
                       calibration_revision='a' * 64, lod_levels=1, occupancy_grid=grid)

    catalog = MapCatalog(tmp_path)
    for map_id, manifest in (("flat", flat), ("nested", nested)):
        directory, loaded = catalog.open(map_id, robot_id='r1', calibration_revision='a' * 64,
                                         map_revision=manifest['hash'])
        assert loaded['mapId'] == map_id
        assert directory.is_dir()

    # An unknown id is a lookup miss, not a path-join accident.
    with pytest.raises(ValueError, match="no map package named"):
        catalog.open('absent', robot_id='r1', calibration_revision='a' * 64)
    # And an id that is not one path segment never reaches the filesystem.
    for bad in ('../flat', 'flat/..', 'a/b', '..', '', 'furnished-home/nested'):
        assert catalog.locate(bad) is None
        with pytest.raises(ValueError, match="no map package named"):
            catalog.open(bad, robot_id='r1', calibration_revision='a' * 64)
