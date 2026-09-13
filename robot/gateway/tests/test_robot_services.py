import threading
import time

import numpy as np
import pytest
from tangying_robot_gateway.dense_slam import DenseSLAM, register_depth, transform
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
        move=lambda g,c:{"ok":True},reserve=reserve,release=release,survey_goals=list,semantic_workspaces=lambda t:[])
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
        capture=capture or frame,move=lambda g,c:{"ok":True},reserve=reserve,release=release,
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


def test_cancel_at_motion_return_cannot_publish_recording(tmp_path):
    workflow,owner=workflow_fixture(tmp_path)
    returned,resume=threading.Event(),threading.Event()
    workflow._reservation=workflow.reserve();workflow.state="moving"
    def motion(goal):returned.set();resume.wait(2)
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
