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
