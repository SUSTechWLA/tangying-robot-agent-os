import math
import threading
import time

import numpy as np
import pytest
from tangying_robot_gateway.dense_slam import (
    DenseSLAM,
    compose,
    register_depth,
    transform,
)
from tangying_robot_gateway.service_registry import (
    RegisteredService,
    ServiceRegistry,
    object_schema,
)
from tangying_robot_proto.robot.v1 import robot_pb2


def request(name="move", request_id="one", **parameters):
    result = robot_pb2.ServiceRequest(robot_id="unit-1",name=name,request_id=request_id)
    result.parameters.update(parameters)
    return result


def test_service_registry_rejects_wrong_robot_schema_and_conflicting_retry():
    calls=[]
    registry=ServiceRegistry("unit-1")
    registry.register(RegisteredService("move","move",object_schema({"distance":{"type":"number","maximum":.5}}),lambda args:calls.append(args) or {"done":True},True))
    wrong=request();wrong.robot_id="unit-2"
    assert registry.call(wrong).code=="ROBOT_ID_MISMATCH"
    assert not registry.call(request(distance=4.)).ok
    assert not registry.call(request(unexpected=True)).ok
    assert not calls
    assert registry.call(request(distance=.2)).ok
    assert registry.call(request(distance=.2)).ok
    assert registry.call(request(distance=.3)).code=="IDEMPOTENCY_CONFLICT"
    assert len(calls)==1


def test_service_registry_concurrent_duplicate_never_reexecutes():
    entered,release=threading.Event(),threading.Event()
    registry=ServiceRegistry("unit-1")
    registry.register(RegisteredService("move","move",object_schema(),lambda _:(entered.set(),release.wait(2),{})[-1],True))
    thread=threading.Thread(target=lambda:registry.call(request()))
    thread.start();assert entered.wait(1)
    assert registry.call(request()).code=="REQUEST_IN_PROGRESS"
    release.set();thread.join(2)
    assert registry.call(request()).ok


def test_depth_registration_recovers_pose_and_rejects_nonoverlap():
    rng=np.random.default_rng(42)
    local=rng.uniform([-1,-.5,.1],[1,1,2],(2500,3))
    expected=np.array([.2,.1,.05])
    registered=register_depth(local,transform(local,expected),[.18,.09,.04])
    assert registered is not None
    assert np.allclose(registered[0],expected,atol=.004)
    assert register_depth(local,transform(local,[20,20,0]),[0,0,0]) is None


def frame(x=0., stamp=None):
    width,height=80,60
    # Realistic planar floor/back wall fixture, explicitly unit-test sensor data.
    rows,cols=np.mgrid[:height,:width]
    depth=(2.+.1*np.sin(cols/7)+.15*np.cos(rows/9)).astype("<f4")
    observation=robot_pb2.Observation(observation_id=f"frame-{x}",wall_time_unix_ms=stamp or int(time.time()*1000))
    observation.robot_state.update({"base_pose":[x,0,.035,1,0,0,0]})
    observation.rgbd_frame.CopyFrom(robot_pb2.RGBDFrame(width=width,height=height,
        depth_metres_f32=depth.tobytes(),rgb=np.full((height,width,3),128,np.uint8).tobytes(),
        intrinsics=[65,0,width/2,0,65,height/2,0,0,1],base_from_camera=np.eye(4).ravel().tolist()))
    return observation


def test_slam_filters_keyframes_and_retains_capture_evidence():
    slam=DenseSLAM()
    first=frame(0.,1000)
    assert slam.add(first)
    assert not slam.add(first)
    assert not slam.add(frame(.01,1001))
    assert slam.add(frame(.2,1002))
    slam.optimize()
    assert slam.cloud().count>100
    assert [item["stamp"] for item in slam.provenance()["observations"]]==[1000,1002]
    invalid=frame(.4,1003);invalid.rgbd_frame.base_from_camera[0]=2
    with pytest.raises(ValueError,match="calibration"):
        slam.add(invalid)


def test_workflow_blocks_save_while_reserved_and_invalidates_old_map(tmp_path):
    from tangying_robot_gateway.robot_workflow import RobotWorkflow
    from tangying_robot_gateway.service_registry import ServiceError
    busy=False
    revision="a"*64
    def reserve():
        nonlocal busy
        if busy:raise ServiceError("ROBOT_BUSY","busy")
        busy=True
        return "token"
    def release(token):
        nonlocal busy
        busy=False
    def save(document,expected,algorithm):
        nonlocal revision
        if expected!=revision:raise ServiceError("REVISION_CONFLICT","stale")
        revision="b"*64
        return {"revision":revision}
    workflow=RobotWorkflow(robot_id="unit-1",root=tmp_path,calibration_get=lambda:{"revision":revision},
        calibration_run=lambda:{"revision":revision},calibration_save=save,capture=lambda:frame(),
        move=lambda g,c,bounded=False:{"ok":True},reserve=reserve,release=release,survey_goals=list,semantic_workspaces=lambda t:[])
    workflow.active={"calibrationRevision":"a"*64}
    reserve()
    with pytest.raises(ServiceError,match="busy"):
        workflow.save_calibration({"document":{},"expectedRevision":revision})
    release("token")
    workflow.save_calibration({"document":{},"expectedRevision":revision})
    assert workflow.active is None
    assert not busy


def workflow_fixture(tmp_path, *, capture=None, calibration_run=None):
    from tangying_robot_gateway.robot_workflow import RobotWorkflow
    from tangying_robot_gateway.service_registry import ServiceError
    owner=[None]
    def reserve():
        if owner[0] is not None:raise ServiceError("ROBOT_BUSY","busy")
        owner[0]=object();return owner[0]
    def release(token):
        if owner[0] is token:owner[0]=None
    result=RobotWorkflow(robot_id="unit-1",root=tmp_path,calibration_get=lambda:{"revision":"a"*64},
        calibration_run=calibration_run or (lambda:{"revision":"a"*64}),calibration_save=lambda *args:{"revision":"a"*64},
        capture=capture or frame,move=lambda g,c,bounded=False:{"ok":True},reserve=reserve,release=release,
        survey_goals=list,semantic_workspaces=lambda t:[],world_frame_revision="stable-driver-frame")
    return result,owner


def test_mapping_cancel_cannot_release_a_calibration_reservation(tmp_path):
    entered,finish=threading.Event(),threading.Event()
    def calibrate():
        entered.set();finish.wait(2);return {"revision":"a"*64}
    workflow,owner=workflow_fixture(tmp_path,calibration_run=calibrate)
    thread=threading.Thread(target=lambda:workflow.run_calibration({}));thread.start()
    assert entered.wait(1)
    held=owner[0];workflow.cancel({})
    assert owner[0] is held
    finish.set();thread.join(2)
    assert owner[0] is None


def test_cancel_during_first_capture_cannot_return_to_recording(tmp_path):
    entered,finish=threading.Event(),threading.Event()
    def capture():
        entered.set();finish.wait(2);return frame()
    workflow,owner=workflow_fixture(tmp_path,capture=capture)
    workflow.start({"mode":"manual"});assert entered.wait(1)
    workflow.cancel({});finish.set();workflow._worker.join(2)
    assert workflow.state=="cancelled"
    assert owner[0] is None
    from tangying_robot_gateway.service_registry import ServiceError
    with pytest.raises(ServiceError):workflow.move_step({"action":"forward"})


def test_grid_includes_robot_outside_camera_cloud_but_requires_clearance_evidence(tmp_path):
    workflow,_=workflow_fixture(tmp_path)
    grid={"width":10,"height":10,"resolution":.1,"origin":[0.,0.,0.],"cells":np.full((10,10),-1)}
    workflow.clearance_validator=lambda xy,radius:False
    workflow._observed_travel(grid,[[-1.,-1.,0.],[-1.,-.5,0.]],np.zeros(3))
    assert grid["origin"][0]<-1. and grid["origin"][1]<-1.
    assert np.all(grid["cells"]==-1)
    workflow.clearance_validator=lambda xy,radius:True
    workflow._observed_travel(grid,[[-1.,-1.,0.],[-1.,-.5,0.]],np.zeros(3))
    assert np.any(grid["cells"]==0)


def map_fixture(tmp_path, *, calibration="a"*64, epoch="stable-driver-frame"):
    from tangying_robot_gateway.map_pipeline import PointCloud, build_map
    cloud=PointCloud(np.array([[0.,0.,0.],[1.,1.,0.],[2.,2.,.5]]))
    return build_map(tmp_path/"map-one",map_id="map-one",robot_id="unit-1",cloud=cloud,source="rgbd_slam",
        calibration_revision="a"*64,slam_metadata={"schemaVersion":"slam.session.v1","robotId":"unit-1",
        "mapId":"map-one","calibrationRevision":calibration,"worldFrameRevision":epoch,"mapFromWorld":[0.,0.,0.],"navigationEvidenceVersion":2})


@pytest.mark.parametrize("operation",["run","save"])
def test_partial_calibration_commit_invalidates_old_map_even_on_error(tmp_path,operation):
    workflow,owner=workflow_fixture(tmp_path)
    revision=["a"*64]
    workflow.calibration_get=lambda:{"revision":revision[0]}
    workflow.active={"calibrationRevision":revision[0]};workflow.grid={"old":True}
    def partial(*args):
        revision[0]="b"*64
        raise OSError("snapshot write failed after calibration commit")
    workflow.calibration_run=workflow.calibration_save=partial
    with pytest.raises(OSError):
        if operation=="run":workflow.run_calibration({})
        else:workflow.save_calibration({"document":{},"expectedRevision":"a"*64})
    assert workflow.active is None and workflow.grid is None and owner[0] is None


@pytest.mark.parametrize("failure",["calibration","thread"])
def test_start_failure_releases_owned_reservation(tmp_path,monkeypatch,failure):
    workflow,owner=workflow_fixture(tmp_path)
    def fail(*args):raise OSError("provider unavailable")
    if failure=="calibration":workflow.calibration_get=fail
    else:monkeypatch.setattr(threading.Thread,"start",fail)
    with pytest.raises(OSError):workflow.start({})
    assert owner[0] is None


def test_manual_move_declares_itself_bounded_and_survey_does_not(tmp_path):
    """A scan nudge owns its path; a survey goal may be routed.

    The runtime cannot tell a 0.2 m nudge from a cross-room goal by geometry, so
    the distinction has to be carried by the request. Without it the household
    router answered a bounded nudge with a multi-metre detour through the
    corridor - the motion and the receipt disagreed about what was asked.
    """
    workflow,_=workflow_fixture(tmp_path)
    seen=[]
    workflow._move_and_sample=lambda goal,*,bounded:seen.append((list(goal),bounded))
    workflow._sample=lambda *args,**kwargs:None
    workflow._reservation=workflow.reserve()
    workflow._manual_move([1.,2.])
    assert seen==[([1.,2.],True)]
    workflow.survey_goals=lambda:[[3.,4.,0.,2**-.5,0.,0.,2**-.5]]
    workflow._build=lambda:None
    workflow._begin("survey")
    assert seen[-1]==([3.,4.,0.,2**-.5,0.,0.,2**-.5],False)


def test_cancel_at_motion_return_cannot_publish_recording(tmp_path):
    workflow,owner=workflow_fixture(tmp_path)
    returned,resume=threading.Event(),threading.Event()
    workflow._reservation=workflow.reserve();workflow.state="moving"
    def motion(goal,*,bounded):returned.set();resume.wait(2)
    workflow._move_and_sample=motion
    workflow._spawn(lambda:workflow._manual_move([0,0]))
    assert returned.wait(1)
    workflow.cancel({});resume.set();workflow._worker.join(2)
    assert workflow.state=="cancelled" and owner[0] is None


def test_sparse_keyframes_without_swept_clearance_do_not_open_unknown_cells(tmp_path):
    workflow,_=workflow_fixture(tmp_path)
    grid={"width":20,"height":20,"resolution":.1,"origin":[-.5,-.5,0.],"cells":np.full((20,20),-1)}
    workflow._observed_travel(grid,[[0.,0.,0.],[1.,1.,0.]],np.zeros(3))
    assert np.all(grid["cells"]==-1)


def test_cancel_releases_paused_recording_even_while_wrapper_thread_is_returning(tmp_path):
    workflow,owner=workflow_fixture(tmp_path)
    release=threading.Event()
    workflow._reservation=workflow.reserve();workflow.state="recording"
    workflow._worker=threading.Thread(target=lambda:release.wait(2));workflow._worker.start()
    workflow.cancel({})
    assert workflow.state=="cancelled" and owner[0] is None
    release.set();workflow._worker.join(2)


@pytest.mark.parametrize("bad_field",["calibration","epoch"])
def test_activation_rejects_rehashed_package_with_foreign_localization(tmp_path,bad_field):
    from tangying_robot_gateway.service_registry import ServiceError
    map_fixture(tmp_path,**{bad_field:"foreign"})
    workflow,_=workflow_fixture(tmp_path)
    with pytest.raises(ServiceError,match="坐标系"):
        workflow.activate({"mapId":"map-one"})
    assert workflow.active is None


def test_restore_never_silently_adopts_another_map_revision(tmp_path):
    import json
    map_fixture(tmp_path)
    (tmp_path/"active-map.json").write_text(json.dumps({"mapId":"map-one","mapRevision":"b"*64,"calibrationRevision":"a"*64}))
    workflow,_=workflow_fixture(tmp_path)
    assert workflow.active is None


def _write_base_map(root, *, map_id="scan-base00000001", robot_id="unit-1",
                    calibration="a"*64, world_frame="stable-driver-frame", anchor=(1.5,-.25,.7)):
    """A minimal but genuinely valid map package, so the reader is tested against the
    same integrity checks a real map passes rather than a stub that skips them."""
    import hashlib
    import json as _json

    from tangying_robot_gateway.map_manifest import build_manifest, save_manifest

    directory = root / map_id
    directory.mkdir(parents=True)
    session = {"schemaVersion": "slam.session.v1", "navigationEvidenceVersion": 2,
               "robotId": robot_id, "mapId": map_id, "calibrationRevision": calibration,
               "worldFrameRevision": world_frame, "mapFromWorld": list(anchor)}
    from tangying_robot_gateway.map_pipeline import PointCloud as _PointCloud
    from tangying_robot_gateway.map_pipeline import encode_lod

    cloud = _PointCloud(xyz=np.array([[0., 0., .1]], dtype=np.float32))
    payloads = {
        "slam_session": ("slam-session.json", _json.dumps(session).encode()),
        "cloud": ("cloud.bin", encode_lod(cloud, level=0)),
        "grid": ("grid.bin", b"grid-bytes"),
    }
    artifacts = {}
    for role, (name, data) in payloads.items():
        (directory / name).write_bytes(data)
        artifacts[role] = {"href": name, "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}
    manifest = build_manifest(map_id=map_id, robot_id=robot_id, source="rgbd_slam",
        mode="mapping", artifacts=artifacts,
        bounds={"min": [-1., -1., 0.], "max": [1., 1., 1.]},
        point_count=10, lod_levels=1,
        floors=[{"id": "ground", "zMin": 0., "zMax": 2.}],
        calibration_revision=calibration)
    save_manifest(directory, manifest)
    return map_id


def test_continuation_reads_the_base_maps_anchor(tmp_path):
    # The anchor is the whole mechanism: reusing the base map's world-to-map transform
    # is what lets a new session start anywhere - including a region the base map never
    # saw - and still land in the base map's coordinates, with no overlapping view and
    # no retracing. Deriving a fresh anchor instead would place the new cloud beside
    # the old one rather than inside it.
    workflow, _owner = workflow_fixture(tmp_path)
    map_id = _write_base_map(tmp_path, anchor=(1.5, -.25, .7))
    assert workflow._continuation_anchor(map_id) == pytest.approx([1.5, -.25, .7])


def test_continuation_refuses_a_map_from_a_different_world_frame(tmp_path):
    # Same robot and same calibration is not enough: if the world frame differs, the
    # anchor describes a different coordinate system and the merged cloud would be
    # misaligned in a way that reads as poor mapping rather than a mismatched pair.
    from tangying_robot_gateway.service_registry import ServiceError

    workflow, _owner = workflow_fixture(tmp_path)
    map_id = _write_base_map(tmp_path, world_frame="some-other-driver-frame")
    with pytest.raises(ServiceError) as failure:
        workflow._continuation_anchor(map_id)
    assert failure.value.code == "CONTINUATION_FRAME_MISMATCH"


def test_continuation_refuses_a_map_without_pose_history(tmp_path):
    # A map imported from elsewhere has geometry but no session, so there is no
    # anchor to continue from and saying so is better than starting a scan that
    # cannot be joined to it.
    import hashlib

    from tangying_robot_gateway.map_manifest import build_manifest, save_manifest
    from tangying_robot_gateway.service_registry import ServiceError

    workflow, _owner = workflow_fixture(tmp_path)
    directory = tmp_path / "scan-noanchor001"
    directory.mkdir()
    artifacts = {}
    for role, name in (("cloud", "cloud.bin"), ("grid", "grid.bin")):
        (directory / name).write_bytes(b"x")
        artifacts[role] = {"href": name, "bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest()}
    save_manifest(directory, build_manifest(map_id="scan-noanchor001", robot_id="unit-1",
        source="import", mode="mapping", artifacts=artifacts,
        bounds={"min": [-1., -1., 0.], "max": [1., 1., 1.]}, point_count=1, lod_levels=1,
        floors=[{"id": "ground", "zMin": 0., "zMax": 2.}], calibration_revision="a"*64))
    with pytest.raises(ServiceError) as failure:
        workflow._continuation_anchor("scan-noanchor001")
    assert failure.value.code == "CONTINUATION_UNAVAILABLE"


def test_continuation_geometry_is_read_from_the_base_map(tmp_path):
    # A continuation must produce the union of both surveys. Inheriting the anchor is
    # what puts them in one frame; this is what puts them in one map. Without it a
    # "full scan" holds only the last leg and the earlier sessions look lost.
    import hashlib as _hashlib
    import json as _json

    from tangying_robot_gateway.map_manifest import build_manifest, save_manifest
    from tangying_robot_gateway.map_pipeline import PointCloud, encode_lod

    workflow, _owner = workflow_fixture(tmp_path)
    map_id = "scan-base00000002"
    directory = tmp_path / map_id
    (directory / "cloud").mkdir(parents=True)
    (directory / "traj").mkdir(parents=True)

    points = PointCloud(xyz=np.array([[1., 2., .3], [1.1, 2.1, .3]], dtype=np.float32))
    cloud_bytes = encode_lod(points, level=0)
    (directory / "cloud" / "lod0.bin").write_bytes(cloud_bytes)
    trail = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
             "geometry": {"type": "LineString", "coordinates": [[1., 2.], [1.1, 2.1]]}}]}
    trail_bytes = _json.dumps(trail).encode()
    (directory / "traj" / "trail.geojson").write_bytes(trail_bytes)

    session = {"schemaVersion": "slam.session.v1", "navigationEvidenceVersion": 2,
               "robotId": "unit-1", "mapId": map_id, "calibrationRevision": "a"*64,
               "worldFrameRevision": "stable-driver-frame", "mapFromWorld": [1.5, -.25, .7]}
    session_bytes = _json.dumps(session).encode()
    (directory / "slam-session.json").write_bytes(session_bytes)

    artifacts = {
        "slam_session": {"href": "slam-session.json", "bytes": len(session_bytes),
                         "sha256": _hashlib.sha256(session_bytes).hexdigest()},
        "cloud": {"href": "cloud/lod0.bin", "bytes": len(cloud_bytes),
                  "sha256": _hashlib.sha256(cloud_bytes).hexdigest()},
        "trajectory": {"href": "traj/trail.geojson", "bytes": len(trail_bytes),
                       "sha256": _hashlib.sha256(trail_bytes).hexdigest()},
        "grid": {"href": "traj/trail.geojson", "bytes": len(trail_bytes),
                 "sha256": _hashlib.sha256(trail_bytes).hexdigest()},
    }
    save_manifest(directory, build_manifest(map_id=map_id, robot_id="unit-1", source="rgbd_slam",
        mode="mapping", artifacts=artifacts, bounds={"min": [0., 0., 0.], "max": [2., 3., 1.]},
        point_count=2, lod_levels=1, floors=[{"id": "ground", "zMin": 0., "zMax": 2.}],
        calibration_revision="a"*64))

    geometry, inherited_trail = workflow._base_geometry(map_id)
    assert geometry is not None and geometry.count == 2
    assert geometry.xyz[0] == pytest.approx([1., 2., .3])
    assert inherited_trail == [[1., 2., 0.], [1.1, 2.1, 0.]]


def test_a_corrupt_base_cloud_is_reported_rather_than_silently_skipped(tmp_path):
    # Manifest integrity covers hash and size, not whether the bytes decode. If the
    # base map's cloud cannot be read, merging nothing would hand back a map missing
    # the region the operator already surveyed while reporting success.
    import hashlib as _hashlib
    import json as _json

    from tangying_robot_gateway.map_manifest import build_manifest, save_manifest

    workflow, _owner = workflow_fixture(tmp_path)
    map_id = "scan-corrupt000001"
    directory = tmp_path / map_id
    directory.mkdir()
    session = {"schemaVersion": "slam.session.v1", "navigationEvidenceVersion": 2,
               "robotId": "unit-1", "mapId": map_id, "calibrationRevision": "a"*64,
               "worldFrameRevision": "stable-driver-frame", "mapFromWorld": [0., 0., 0.]}
    blobs = {"slam_session": ("s.json", _json.dumps(session).encode()),
             "cloud": ("cloud.bin", b"not-a-chunk"), "grid": ("grid.bin", b"g")}
    artifacts = {}
    for role, (name, data) in blobs.items():
        (directory / name).write_bytes(data)
        artifacts[role] = {"href": name, "bytes": len(data),
                           "sha256": _hashlib.sha256(data).hexdigest()}
    save_manifest(directory, build_manifest(map_id=map_id, robot_id="unit-1", source="rgbd_slam",
        mode="mapping", artifacts=artifacts, bounds={"min": [0., 0., 0.], "max": [1., 1., 1.]},
        point_count=1, lod_levels=1, floors=[{"id": "ground", "zMin": 0., "zMax": 2.}],
        calibration_revision="a"*64))

    with pytest.raises(ValueError):
        workflow._base_geometry(map_id)


def test_the_anchor_converts_world_geometry_into_map_geometry():
    # This is the property the continuation feature rests on, and it is exact rather
    # than statistical. DenseSLAM keeps poses in the driver's world frame, so cloud()
    # returns world coordinates; the map is in map coordinates; the anchor is the
    # transform between them. Applying it to the geometry must equal composing it with
    # the pose - which is precisely what _build relies on when it re-places a session's
    # cloud, and precisely what the trail code already did.
    #
    # Testing it through ICP would not work: the standard test fixture returns the same
    # depth pattern at every pose, which is a world with no parallax and therefore no
    # information for registration. The identity below needs no fixture at all.
    rng = np.random.default_rng(7)
    points = rng.uniform(-1., 1., size=(64, 3)).astype(np.float32)
    world_pose = np.array([.6, -.2, .35])          # where the session thinks it is
    anchor = np.array([2.5, 1.25, -1.1])           # world -> map, inherited from the base

    world_points = transform(points, world_pose)             # cloud() output
    re_placed = transform(world_points, anchor)              # what _build must do
    equivalent = transform(points, compose(anchor, world_pose))
    assert np.allclose(re_placed, equivalent, atol=1e-6)

    # And the anchor has to move the geometry, or a test written this way would pass
    # even with the transform omitted - which is how the omission survived.
    from tangying_robot_gateway.map_pipeline import PointCloud as _PC

    placed = _PC(xyz=re_placed)
    unplaced = _PC(xyz=world_points)
    assert not np.allclose(placed.xyz, unplaced.xyz, atol=1e-3)
    assert float(np.linalg.norm(placed.xyz.mean(axis=0) - unplaced.xyz.mean(axis=0))) > 1e-3


def test_a_continuation_inherits_the_base_maps_free_space(tmp_path):
    """The union of two surveys has to include the older one's verified free space.

    A survey's grid is not a pure function of its cloud: free corridors also come
    from the poses its own clearance validator certified. A continuation that
    rebuilt the grid from the merged cloud alone produced a map where the base
    survey's rooms were unknown again - the patrol route then failed with
    NO_KNOWN_PATH even though nothing had been lost from the point cloud.
    """
    import hashlib as _hashlib
    import json as _json

    from tangying_robot_gateway.map_manifest import build_manifest, save_manifest
    from tangying_robot_gateway.map_pipeline import PointCloud, encode_lod
    from tangying_robot_gateway.navigation_map import nav2_artifacts

    workflow, _owner = workflow_fixture(tmp_path)
    map_id = "scan-basegrid0001"
    directory = tmp_path / map_id
    (directory / "nav").mkdir(parents=True)
    session = {"schemaVersion": "slam.session.v1", "navigationEvidenceVersion": 2,
               "robotId": "unit-1", "mapId": map_id, "calibrationRevision": "a"*64,
               "worldFrameRevision": "stable-driver-frame", "mapFromWorld": [0., 0., 0.]}
    session_bytes = _json.dumps(session).encode()
    (directory / "slam-session.json").write_bytes(session_bytes)
    cloud_bytes = encode_lod(PointCloud(xyz=np.array([[0., 0., .1]], dtype=np.float32)), level=0)
    (directory / "cloud.bin").write_bytes(cloud_bytes)
    # A corridor the base survey certified but whose cloud holds no floor points:
    # exactly the evidence a rebuilt grid cannot recover.
    certified = {"width": 4, "height": 1, "resolution": 0.05, "origin": [0., 0., 0.],
                 "cells": np.zeros((1, 4), dtype=np.int16)}
    pgm, yaml = nav2_artifacts(certified)
    (directory / "nav" / "map.pgm").write_bytes(pgm)
    (directory / "nav" / "map.yaml").write_bytes(yaml)
    artifacts = {
        "slam_session": {"href": "slam-session.json", "bytes": len(session_bytes),
                         "sha256": _hashlib.sha256(session_bytes).hexdigest()},
        "cloud": {"href": "cloud.bin", "bytes": len(cloud_bytes),
                  "sha256": _hashlib.sha256(cloud_bytes).hexdigest()},
        "navigation_grid": {"href": "nav/map.pgm", "bytes": len(pgm),
                            "sha256": _hashlib.sha256(pgm).hexdigest()},
        "navigation": {"href": "nav/map.yaml", "bytes": len(yaml),
                       "sha256": _hashlib.sha256(yaml).hexdigest()},
        "grid": {"href": "nav/map.pgm", "bytes": len(pgm),
                 "sha256": _hashlib.sha256(pgm).hexdigest()},
    }
    save_manifest(directory, build_manifest(map_id=map_id, robot_id="unit-1", source="rgbd_slam",
        mode="mapping", artifacts=artifacts, bounds={"min": [0., 0., 0.], "max": [0.2, 0.2, 1.]},
        point_count=1, lod_levels=1, floors=[{"id": "ground", "zMin": 0., "zMax": 2.}],
        calibration_revision="a"*64))

    grid = workflow._base_grid(map_id)
    assert grid is not None and grid["width"] == 4
    np.testing.assert_array_equal(grid["cells"], np.zeros((1, 4), dtype=np.int16))


def test_a_base_map_without_navigation_artifacts_does_not_block_continuation(tmp_path):
    # Geometry is still worth merging even when the older map predates the nav2
    # artifacts; refusing the whole continuation would be the worse answer.
    from tangying_robot_gateway.service_registry import ServiceError  # noqa: F401  (fixture parity)

    workflow, _owner = workflow_fixture(tmp_path)
    map_id = _write_base_map(tmp_path, map_id="scan-nonav0000001")
    assert workflow._base_grid(map_id) is None


# ---------------------------------------------------------------------------
# Automatic exploration: the policy is in exploration.py, so what is tested
# here is the wiring - that the loop drives, publishes legs instead of dying at
# the frame cap, survives a locally refused step, and says why it stopped.
# ---------------------------------------------------------------------------

class _ExplorationWorld:
    """A walled room the robot observes as a disc around itself.

    Observation accumulates, the way a real map does: a cell the robot has seen
    stays seen when it drives away. Recomputing the disc from the current pose
    instead would make previously measured floor unknown again, and no policy
    can finish a map that keeps forgetting.
    """

    def __init__(self, workarea=6.0, resolution=0.25, sensor_radius=3.0):
        self.workarea = workarea
        self.resolution = resolution
        self.sensor_radius = sensor_radius
        self.pose = [1.0, 1.0, 0.035, 1.0, 0.0, 0.0, 0.0]
        self.moves = []
        self.travelled = 0.0
        self._observed = set()
        self.observe()

    def observe(self):
        reach = int(self.sensor_radius / self.resolution) + 1
        column, row = int(self.pose[0] / self.resolution), int(self.pose[1] / self.resolution)
        size = round(self.workarea / self.resolution)
        for r in range(max(1, row - reach), min(size - 1, row + reach + 1)):
            for c in range(max(1, column - reach), min(size - 1, column + reach + 1)):
                x, y = (c + .5) * self.resolution, (r + .5) * self.resolution
                if (x - self.pose[0]) ** 2 + (y - self.pose[1]) ** 2 <= self.sensor_radius ** 2:
                    self._observed.add((r, c))

    def blind(self):
        return np.zeros((round(self.workarea / self.resolution),
                         round(self.workarea / self.resolution)), dtype=bool)

    def grid(self):
        size = round(self.workarea / self.resolution)
        cells = np.full((size, size), -1, dtype=np.int16)
        cells[0, :] = cells[-1, :] = cells[:, 0] = cells[:, -1] = 100
        for r, c in self._observed:
            cells[r, c] = 0
        return {"width": size, "height": size, "resolution": self.resolution,
                "origin": [0.0, 0.0, 0.0], "cells": cells}


def _explore_workflow(tmp_path, world, *, refuse=(), leg_frames=None):
    from tangying_robot_gateway.service_registry import ServiceError

    workflow, _owner = workflow_fixture(tmp_path)
    workflow._reservation = workflow.reserve()
    workflow.travelled = 0.0
    workflow.built = []
    workflow.capture = lambda: type("Observation", (), {
        "robot_state": {"base_pose": list(world.pose)},
        "wall_time_unix_ms": int(time.time() * 1000), "observation_id": "obs"})()
    workflow._sample = lambda *args, **kwargs: None
    # This mock sensor has no blind spot - it measures a full disc around every
    # pose - so it reports no cells the camera can never see. The real runtime's
    # blind-spot mask is exercised against the simulator, not here.
    workflow._live_grid = lambda: (world.grid(), world.blind())

    def move_and_sample(goal, *, bounded, fatal=True):
        assert bounded, "an exploration step is a bounded step"
        assert not fatal, "an exploration step handles its own refusals"
        yaw = math.atan2(math.sin(2*math.atan2(goal[6], goal[3])
                                  - 2*math.atan2(world.pose[6], world.pose[3])),
                         math.cos(2*math.atan2(goal[6], goal[3])
                                  - 2*math.atan2(world.pose[6], world.pose[3])))
        assert abs(yaw) <= .5 + 1e-9, "the driver accepts at most half a radian per command"
        target = (round(goal[0], 3), round(goal[1], 3))
        if any(abs(target[0] - x) < 1e-6 and abs(target[1] - y) < 1e-6 for x, y in refuse):
            raise ServiceError("NAV_MODEL_COLLISION", "reference driver model predicts contact")
        world.moves.append(target)
        moved = ((goal[0] - world.pose[0]) ** 2 + (goal[1] - world.pose[1]) ** 2) ** .5
        world.travelled += moved
        workflow._summary["travelledM"] = world.travelled
        world.pose = list(goal)
        world.observe()

    workflow._move_and_sample = move_and_sample

    def build():
        workflow.built.append(workflow.map_id)
        workflow._leg_anchor = [0.0, 0.0, 0.0]

    workflow._build = build
    workflow.slam.frames = [object()] * 5
    if leg_frames is not None:
        workflow._leg_frame_limit = leg_frames
    return workflow


def test_exploration_drives_at_the_frontier_until_the_room_is_measured(tmp_path):
    world = _ExplorationWorld()
    workflow = _explore_workflow(tmp_path, world)
    workflow._begin("explore", {"maxTravelM": 30.0, "maxLegs": 1})
    assert world.moves, "the explorer must drive"
    assert workflow._explore_complete, "a fully observed room has no frontier left"
    assert workflow._exploration["stopReason"] == "complete"
    assert workflow._exploration["unknownFraction"] < 0.35
    assert workflow.built, "the survey publishes its result"


def test_exploration_reports_a_travel_budget_rather_than_claiming_completion(tmp_path):
    world = _ExplorationWorld(workarea=40.0)
    workflow = _explore_workflow(tmp_path, world)
    workflow._begin("explore", {"maxTravelM": 2.0, "maxLegs": 1})
    assert not workflow._explore_complete
    assert workflow._exploration["stopReason"] == "travel_budget"
    assert world.travelled <= 2.5, "the budget is a bound, not a suggestion"


def test_a_refused_step_is_routed_around_instead_of_ending_the_survey(tmp_path):
    world = _ExplorationWorld(workarea=40.0)
    # Refuse whatever the first chosen step is; the survey must not die on it.
    workflow = _explore_workflow(tmp_path, world)
    original = workflow._move_and_sample
    refusals = []

    def refuse_one(goal, *, bounded, fatal=True):
        world.pose[0] += 0.5  # stand somewhere else so the refused target stays behind
        refusals.append((round(goal[0], 3), round(goal[1], 3)))
        if len(refusals) == 1:
            from tangying_robot_gateway.service_registry import ServiceError
            raise ServiceError("NAV_MODEL_COLLISION", "reference driver model predicts contact")
        original(goal, bounded=bounded, fatal=fatal)

    workflow._move_and_sample = refuse_one
    workflow._begin("explore", {"maxTravelM": 6.0, "maxLegs": 1})
    assert workflow.state != "failed", workflow.message
    assert workflow._exploration["refused"] >= 1
    assert len(world.moves) >= 1, "the survey keeps driving after a local refusal"


def test_a_leg_that_runs_out_of_frames_is_published_and_continued(tmp_path):
    world = _ExplorationWorld(workarea=40.0)
    workflow = _explore_workflow(tmp_path, world)
    opened = []
    workflow._open_leg = lambda number: opened.append(number)
    original = workflow._move_and_sample

    def burn_frames(goal, *, bounded, fatal=True):
        original(goal, bounded=bounded, fatal=fatal)
        # Every few metres of driving the session approaches its 400-frame cap.
        workflow.slam.frames.extend([object()] * 12)

    workflow._move_and_sample = burn_frames
    workflow._begin("explore", {"maxTravelM": 60.0, "maxLegs": 2})
    assert workflow.built, "the exhausted leg is published, not lost"
    assert workflow._exploration["stopReason"] == "frame_budget"
    assert opened == [2], "exploration continues from the map it just saved"


def test_a_leg_that_measured_nothing_is_reported_and_not_published(tmp_path):
    # Publishing an empty revision would be noise, and calling it "explored"
    # would be a lie: this leg simply could not start.
    world = _ExplorationWorld(workarea=40.0)
    workflow = _explore_workflow(tmp_path, world)
    # Commands are accepted but the base never moves: the survey cannot start.
    workflow._move_and_sample = lambda goal, *, bounded, fatal=True: None
    workflow._begin("explore", {"maxTravelM": 8.0, "maxLegs": 1})
    assert workflow.built == []
    assert workflow.state == "failed"
    assert "未能开始" in workflow.message


def test_the_exploration_policy_is_declared_in_the_service_catalogue(tmp_path):
    workflow, _owner = workflow_fixture(tmp_path)
    from tangying_robot_gateway.service_registry import ServiceRegistry

    registry = ServiceRegistry("unit-1")
    workflow.register(registry)
    schema = registry.services["mapping.start"].schema
    assert "explore" in schema["properties"]["mode"]["enum"]
    assert {"maxTravelM", "maxLegs"} <= set(schema["properties"])
    # Service arguments cross the console as protobuf Struct values, where every
    # JSON number is a double. A schema that demanded a strict integer would be
    # unreachable through the only caller the console has.
    number = {"type": "number", "minimum": 1, "maximum": 6}
    assert schema["properties"]["maxLegs"] == number


def test_a_refused_exploration_step_does_not_cancel_the_session(tmp_path):
    """A blocked doorway is local news, not the end of the survey.

    The shared move helper cancels the session on any fault, which is right for
    an operator's move and wrong here: the explorer would abandon the rest of
    the house because one approach was refused.
    """
    workflow, _owner = workflow_fixture(tmp_path)
    workflow._reservation = workflow.reserve()
    calls = []

    def refusing(goal, cancel, bounded=False):
        calls.append(bounded)
        return {"ok": False, "code": "NAV_MODEL_COLLISION", "message": "predicts contact"}

    workflow.move = refusing
    workflow._sample = lambda *args, **kwargs: None
    from tangying_robot_gateway.service_registry import ServiceError

    workflow._cancel = threading.Event()
    with pytest.raises(ServiceError, match="predicts contact"):
        workflow._move_and_sample([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0], bounded=True, fatal=False)
    assert not workflow._cancel.is_set(), "a handled refusal must not cancel the survey"

    workflow._cancel = threading.Event()
    with pytest.raises(ServiceError, match="predicts contact"):
        workflow._move_and_sample([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0], bounded=True)
    assert workflow._cancel.is_set(), "an operator move still stops the scan when refused"
    assert calls == [True, True]
