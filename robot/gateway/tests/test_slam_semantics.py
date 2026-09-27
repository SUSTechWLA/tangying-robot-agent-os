import math
import time
from types import SimpleNamespace

import numpy as np
import pytest
from tangying_robot_gateway.contracts import validate_pose
from tangying_robot_gateway.slam_semantics import certify_locations, navigation_contract


def grid():
    cells = np.zeros((40,60),dtype=np.int16)
    cells[0,:] = cells[-1,:] = 100
    cells[:,0] = cells[:,-1] = 100
    return {"width":60,"height":40,"resolution":.1,"origin":[0.,0.,0.],"cells":cells}


def location(name="lab",pose=None,kind="room",aliases=None):
    return {"name":name,"aliases":aliases or [],"kind":kind,"target":[2.,2.,.8],
            "navigationPose":pose or [2.,2.,0.,1.,0.,0.,0.],"annotationSource":"operator"}


def test_unknown_space_and_blocked_work_dock_are_not_executable():
    measured = grid()
    measured["cells"][15:25,15:25] = -1
    locations = certify_locations(measured,[location(kind="work_area")],radius=.32)
    assert locations[0]["navigationReady"] is False
    assert locations[0]["navigationPose"] == location()["navigationPose"]
    contract = navigation_contract({"mapId":"scan-1","mapRevision":"a"*64,"calibrationRevision":"c"},
        locations,[0.,0.,0.],robot_id="r")
    assert contract["goals"] == {}
    assert contract["blockedLocations"][0]["blockers"] == ["SEMANTIC_GOAL_NOT_CLEAR"]


def test_room_seed_adjustment_is_bounded_and_does_not_move_work_docks():
    point = [0.41,2.,0.,1.,0.,0.,0.]
    rooms = certify_locations(grid(),[location(pose=point)],radius=.32)
    assert rooms[0]["navigationReady"]
    assert math.dist(point[:2],rooms[0]["navigationPose"][:2]) <= .25
    dock = certify_locations(grid(),[location(pose=point,kind="work_area")],radius=.32)[0]
    assert not dock["navigationReady"]


def test_goals_are_converted_from_saved_map_frame_and_bound_to_exact_revision():
    locations = certify_locations(grid(),[location(aliases=["实验台"])],radius=.32)
    active = {"mapId":"scan-1","mapRevision":"b"*64,"calibrationRevision":"cal"}
    contract = navigation_contract(active,locations,[1.,2.,math.pi/2],robot_id="r")
    goal = contract["goals"]["lab"]
    assert goal[:2] == pytest.approx([0.,-1.])
    validate_pose(goal)
    assert contract["mapRevision"] == active["mapRevision"]
    assert contract["aliases"]["实验台"] == "lab"


def test_alias_collision_rejects_activation_and_regions_keep_unnamed_identity():
    with pytest.raises(ValueError,match="ambiguous"):
        certify_locations(grid(),[location("a",aliases=["工作区"]),location("b",aliases=["工作区"])],radius=.32)
    locations = certify_locations(grid(),[],radius=.32,include_regions=True)
    assert locations and all(item["name"].startswith("region-") for item in locations)
    assert all(item["annotationSource"]=="slam_region_unlabelled" for item in locations)


def test_object_memory_uses_object_capture_time_and_vantage_not_another_slam_frame(tmp_path):
    from robot.gateway.tests.test_robot_services import frame, workflow_fixture
    workflow, _ = workflow_fixture(tmp_path)
    captured = int(time.time()*1000)-100
    entity = SimpleNamespace(entity_id="mug",category="cup",attributes={},
        pose_xyz_quat=[2.,2.,.8,1.,0.,0.,0.],confidence=.9)
    view = SimpleNamespace(wall_time_unix_ms=captured,robot_state={"base_pose":[1.,1.,0.,1.,0.,0.,0.]},
        reconstruction={"sourceId":"r/head"},entities=[entity])
    workflow.entity_source = lambda:view
    workflow._observe_objects(frame(0.,captured-1000),odometry=[0.,0.,0.])
    doc = workflow.object_memory.document(now_unix_ms=captured+100,map_id="scan",calibration_revision="cal")
    item = doc["objects"][0]
    assert item["lastSeenUnixMs"] == captured
    assert item["observedFrom"][:2] == [1.,1.]
    assert item["sourceId"] == "r/head"
    view.wall_time_unix_ms = captured-10_000
    workflow._last_object_poll_ms = 0
    before = workflow.object_memory.polls
    workflow._observe_objects(frame(0.,captured))
    assert workflow.object_memory.polls == before
    assert "stale" in workflow._object_errors[-1]


def test_saved_semantics_survive_activation_and_registered_resolution(tmp_path):
    from tangying_robot_gateway.map_pipeline import PointCloud, build_map
    from tangying_robot_gateway.service_registry import ServiceError

    from robot.gateway.tests.test_robot_services import workflow_fixture
    measured = grid()
    build_map(tmp_path/'map-lab',map_id='map-lab',robot_id='unit-1',
        cloud=PointCloud(np.array([[2.,2.,0.],[3.,3.,.5]])),source='rgbd_slam',
        calibration_revision='a'*64,occupancy_grid=measured,
        semantic_workspaces=[location('lab',kind='work_area',aliases=['实验台工作区'])],
        slam_metadata={'schemaVersion':'slam.session.v1','robotId':'unit-1',
            'mapId':'map-lab','calibrationRevision':'a'*64,
            'worldFrameRevision':'stable-driver-frame','mapFromWorld':[1.,2.,math.pi/2],
            'navigationEvidenceVersion':2})
    workflow,_ = workflow_fixture(tmp_path)
    with pytest.raises(ServiceError):
        workflow.resolve_location({'name':'实验台工作区'})
    workflow.activate({'mapId':'map-lab'})
    restored,_ = workflow_fixture(tmp_path)
    from tangying_robot_gateway.service_registry import ServiceRegistry
    from tangying_robot_proto.robot.v1 import robot_pb2
    registry = ServiceRegistry('unit-1')
    restored.register(registry)
    request = robot_pb2.ServiceRequest(robot_id='unit-1',name='semantic.resolve',request_id='resolve-lab')
    request.parameters.update({'name':'实验台工作区'})
    assert registry.call(request).ok
    result = restored.resolve_location({'name':'实验台工作区'})
    assert result['goalPose'][:2] == pytest.approx([0.,-1.])
    assert result['mapRevision'] == restored.active['mapRevision']
    assert restored.locations()['locations'][0]['kind'] == 'work_area'
    with pytest.raises(ServiceError):
        restored.resolve_location({'name':'另一张图的地点'})
    # A sensor calibration change must invalidate resolution, not reuse cached labels.
    restored.calibration_get = lambda:{'revision':'b'*64}
    with pytest.raises(ServiceError):
        restored.resolve_location({'name':'实验台工作区'})
