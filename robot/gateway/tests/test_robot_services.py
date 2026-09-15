import math
import threading
import time
from typing import ClassVar

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


def room_surface(rng, *, size=3.0, per_wall=900, height=1.0):
    """Points on the walls of a room, which is what a depth camera actually sees.

    A cloud of uniformly random points in a box is not a surface: it has no
    normals to speak of and no geometry to constrain a pose with. Scoring a
    registration against one measures the fixture, not the estimator.
    """
    walls = []
    for axis, offset in ((0, size), (0, -size), (1, size), (1, -size)):
        along = rng.uniform(-size, size, per_wall)
        depth = rng.normal(0.0, 0.004, per_wall)
        height_noise = rng.uniform(0.1, height, per_wall)
        wall = np.zeros((per_wall, 3))
        wall[:, axis] = offset + depth
        wall[:, 1 - axis] = along
        wall[:, 2] = height_noise
        walls.append(wall)
    return np.concatenate(walls)


def test_depth_registration_recovers_a_planar_pose_on_real_surface_geometry():
    rng = np.random.default_rng(42)
    local = room_surface(rng)
    expected = np.array([.2, .1, .05])
    registered = register_depth(local, transform(local, expected), [.18, .09, .04])
    assert registered is not None
    assert np.allclose(registered[0], expected, atol=.004)
    assert registered[1]["conditioning"] > .02
    assert register_depth(local, transform(local, [20, 20, 0]), [0, 0, 0]) is None


def test_a_single_flat_wall_is_measured_only_in_the_direction_it_observes():
    """One wall constrains one direction, and the pose graph may only use that one.

    This is the measurement that quietly rotates a map: the fit is excellent,
    the overlap is perfect, and the along-wall position it reports is whatever
    the correspondence noise happened to say. Refusing the registration outright
    would throw away a real measurement of the wall's normal distance, so the
    measurement is kept and *weighted* - dead in the direction it never saw.
    """
    from tangying_robot_gateway.dense_slam import information_weight

    rng = np.random.default_rng(7)
    wall = np.zeros((1500, 3))
    wall[:, 0] = 2.0 + rng.normal(0, 0.004, 1500)
    wall[:, 1] = rng.uniform(-2, 2, 1500)
    wall[:, 2] = rng.uniform(0.1, 1.4, 1500)
    report = {}
    registered = register_depth(wall, transform(wall, [.1, .05, .02]), [.09, .04, .01], report=report)
    assert registered is not None, report
    # The along-wall component it reports is not a measurement, and the test
    # says so rather than pretending the estimator got it right.
    assert registered[1]["conditioning"] < .12, registered[1]
    weight = information_weight(registered[1]["information"], 0.0)
    across = float(np.linalg.norm(weight @ np.array([1.0, 0.0, 0.0])))
    along = float(np.linalg.norm(weight @ np.array([0.0, 1.0, 0.0])))
    # Five times, not a hundred: the wall is finite and its ends do carry a
    # little along-wall information through the moment arm. What matters is that
    # the unobserved direction is clearly the weaker one.
    assert across > 4 * along, (across, along)


def test_a_registration_reports_why_it_was_refused():
    # "No measurement" is not enough to debug a drifting survey with.
    rng = np.random.default_rng(3)
    local = room_surface(rng)
    report = {}
    assert register_depth(local, transform(local, [20., 20., 0.]), [0., 0., 0.], report=report) is None
    assert report["status"] in {"no_overlap", "too_flat_or_few_normals"}, report
    accepted = {}
    assert register_depth(local, transform(local, [.1, .05, .02]), [.09, .04, .01], report=accepted)
    assert accepted["status"] == "accepted" and accepted["conditioning"] > .02


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


def test_the_recorded_attempt_budget_matches_what_the_console_accepts():
    """The keyframe panel validates a per-frame attempt budget before it opens.

    It used to allow two per frame and only the words "accepted"/"rejected", so
    every published map was refused and the panel showed a format error where the
    keyframes belong. The vocabulary side is gone - the panel now keeps any
    reason-shaped status - but the count is still a bound the two sides share,
    and widening the search must not silently push a real session past it.
    """
    from tangying_robot_gateway.dense_slam import DenseSLAM

    per_frame = 1 + DenseSLAM.MAX_LOOP_CANDIDATES * DenseSLAM.LOOP_GUESSES
    assert per_frame <= 9, (
        f"{per_frame} attempts per keyframe exceeds the console's budget of 9; "
        "raise MAX_ATTEMPTS_PER_FRAME in web/map_keyframes.js in the same change")


def test_trail_certification_asks_for_exactly_the_planned_clearance(tmp_path):
    """A plan and its proof must be about the same number.

    The certified band was footprint + 0.08 m while the driver's guard was a flat
    0.40 m, so the driver proved it. When the guard became the measured CAD
    envelope plus a commissioned margin (0.355 m) the 0.40 m query was refused -
    correctly, a proof may not claim more than the check behind it - and the whole
    driven trail silently went uncertified, which is how an explorer ends up
    reporting no_reachable_frontier in a house it has barely entered.
    """
    workflow, _ = workflow_fixture(tmp_path)
    asked = []
    workflow.clearance_validator = lambda xy, radius: asked.append(radius) or True
    grid = {"width": 40, "height": 40, "resolution": .1, "origin": [0., 0., 0.],
            "cells": np.full((40, 40), -1, dtype=np.int16)}
    workflow._observed_travel(grid, [[1., 1., 0.], [1., 2., 0.]], np.zeros(3))
    assert asked, "the validator must be consulted for the driven trail"
    assert set(asked) == {workflow._planning_clearance()}, asked
    assert workflow._planning_clearance() == workflow.footprint_radius, (
        "with planningMarginM at zero the planner keeps the robot's own radius")
    # The band the driver must be able to prove may never be wider than the
    # planner asks for, and both stay at or below the robot's own footprint.
    assert workflow._planning_clearance() <= workflow.footprint_radius + 0.08


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


def test_a_survey_publishes_the_objects_it_actually_saw(tmp_path):
    """The map carries a semantic object layer, not only geometry.

    A task that needs "the cup" otherwise has to be standing where it can see one.
    The survey is the one moment the robot drives the whole house with perception
    running, so the layer belongs in the record it publishes - in the map frame,
    with the age of each sighting.
    """
    seen_mug = type("Entity", (), {
        "entity_id": "ceramic-mug", "category": "cup", "attributes": {"color": "white"},
        "pose_xyz_quat": [2.0, 3.5, .85, 1., 0., 0., 0.], "confidence": .9})()

    class Entities:
        source_id = "unit-1/head-rgbd"

        def __init__(self):
            self.entities = [seen_mug]

    workflow, _owner = workflow_fixture(tmp_path)
    polls = []

    def source():
        polls.append(1)
        return Entities()

    workflow.entity_source = source
    workflow._last_object_poll_ms = 0
    # The sampling loop asks at its own cadence: perception is heavier than a
    # capture, and an object does not move between two keyframes.
    workflow._observe_objects(frame(0.1, 10_000))
    workflow._observe_objects(frame(0.2, 10_100))
    assert len(polls) == 1, "a poll inside the interval is skipped"
    workflow._observe_objects(frame(0.3, 11_500))
    assert len(polls) == 2
    assert workflow.object_memory.polls == 2

    workflow.calibration_revision = workflow.calibration_get()["revision"]
    assert workflow.object_memory.reanchor([0., 0., 0.], [1., 0., 0.]) == 1
    document = workflow.object_memory.document(
        now_unix_ms=2_000, map_id="m", calibration_revision=workflow.calibration_revision)
    assert document["objects"][0]["pose"][:2] == [3.0, 3.5]
    assert document["objects"][0]["evidenceFrameId"] == "map"


def test_a_detector_fault_is_recorded_and_never_kills_a_survey(tmp_path):
    workflow, _owner = workflow_fixture(tmp_path)

    def broken():
        raise RuntimeError("detector offline")

    workflow.entity_source = broken
    workflow._last_object_poll_ms = 0
    observation = frame(0.4)
    workflow._observe_objects(observation)          # must not raise
    assert workflow._object_errors and "detector offline" in workflow._object_errors[-1]
    assert workflow.object_memory.instances == []
    # Without a source the layer is simply absent, which is not an error either.
    workflow.entity_source = None
    workflow._observe_objects(observation)
    assert workflow.object_memory.instances == []


def test_the_object_layer_is_read_from_a_base_map_and_revalidated(tmp_path):
    """A continuation adopts the previous layer, or starts a fresh one."""
    from tangying_robot_gateway.map_pipeline import PointCloud, build_map
    from tangying_robot_gateway.object_memory import ObjectMemory

    memory = ObjectMemory()
    memory.observe([type("E", (), {"entity_id": "mug", "category": "cup", "attributes": {},
                                   "pose_xyz_quat": [1., 1., .8], "confidence": .8})()],
                   map_from_world=[0., 0., 0.], stamp_unix_ms=1_000)
    document = memory.document(now_unix_ms=1_500, map_id="base-map",
                               calibration_revision="a" * 64)
    build_map(tmp_path / "base-map", map_id="base-map", robot_id="unit-1",
              cloud=PointCloud(np.array([[0., 0., 0.], [1., 1., .5]], dtype=np.float32)),
              calibration_revision="a" * 64, semantic_objects=document)
    workflow, _owner = workflow_fixture(tmp_path)
    adopted = workflow._base_objects("base-map")
    assert adopted["objects"][0]["category"] == "cup"
    assert workflow.object_memory.merge(adopted) == 1
    # A map published before this layer existed is a missing answer, not a fault.
    build_map(tmp_path / "old-map", map_id="old-map", robot_id="unit-1",
              cloud=PointCloud(np.array([[0., 0., 0.], [1., 1., .5]], dtype=np.float32)),
              calibration_revision="a" * 64)
    assert workflow._base_objects("old-map") is None
    assert workflow._base_objects("no-such-map") is None


def test_a_map_refuses_an_object_layer_that_belongs_to_another_map(tmp_path):
    from tangying_robot_gateway.map_pipeline import PointCloud, build_map
    from tangying_robot_gateway.object_memory import ObjectMemory

    memory = ObjectMemory()
    memory.observe([type("E", (), {"entity_id": "mug", "category": "cup", "attributes": {},
                                   "pose_xyz_quat": [1., 1., .8], "confidence": .8})()],
                   map_from_world=[0., 0., 0.], stamp_unix_ms=1_000)
    foreign = memory.document(now_unix_ms=1_500, map_id="other-map", calibration_revision="a" * 64)
    with pytest.raises(ValueError, match="object layer does not belong"):
        build_map(tmp_path / "m", map_id="this-map", robot_id="unit-1",
                  cloud=PointCloud(np.array([[0., 0., 0.]], dtype=np.float32)),
                  calibration_revision="a" * 64, semantic_objects=foreign)


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


def test_keyframes_open_only_the_cells_the_robot_actually_occupied(tmp_path):
    """Proprioception is evidence; interpolation between keyframes is not.

    The robot was standing at each keyframe, so those cells cannot contain an
    obstacle - without that, a forward camera's own blind spot leaves the cell
    the robot occupies unknown and the router refuses to plan from its own
    position. Everything *between* two keyframes still needs swept evidence: no
    validator, no corridor.
    """
    workflow,_=workflow_fixture(tmp_path)
    grid={"width":20,"height":20,"resolution":.1,"origin":[-.5,-.5,0.],"cells":np.full((20,20),-1)}
    workflow._observed_travel(grid,[[0.,0.,0.],[1.,1.,0.]],np.zeros(3))
    def cell(x,y):
        return grid["cells"][int((y-(-.5))/.1), int((x-(-.5))/.1)]
    assert cell(0.,0.) == 0 and cell(1.,1.) == 0
    assert cell(0.5,0.5) == -1, "the path between keyframes is not certified"


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
        # Mirrors the publisher's contract: it publishes and reports completion.
        # The survey overrides that between legs, which is what the leg-boundary
        # test checks.
        workflow.built.append(workflow.map_id)
        workflow._leg_anchor = [0.0, 0.0, 0.0]
        with workflow._lock:
            workflow.state, workflow.message = "completed", "扫描完成"

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


def test_surface_normals_find_walls_and_ignore_the_floor():
    """A wall is a line in plan; a floor is a filled region with no planar normal.

    The estimator solves for x, y and heading, so a floor - flat, but spread in
    both directions - says nothing about any of them. Treating it as a surface
    adds noise and no information.
    """
    from tangying_robot_gateway.dense_slam import surface_normals

    rng = np.random.default_rng(11)
    wall = np.zeros((600, 3))
    wall[:, 0] = 3.0 + rng.normal(0, 0.004, 600)
    wall[:, 1] = rng.uniform(-2, 2, 600)
    wall[:, 2] = rng.uniform(0.1, 1.4, 600)
    # The floor arrives voxel-downsampled, so it is a regular grid: every patch
    # of it spreads equally in both directions and carries no normal at all.
    axis = np.arange(-2.0, 2.0, .05)
    grid = np.stack(np.meshgrid(axis, axis, indexing="ij"), axis=-1).reshape(-1, 2)
    floor = np.column_stack([grid, np.full(len(grid), 0.02)])

    wall_normals = surface_normals(wall)
    assert np.mean(np.linalg.norm(wall_normals, axis=1) > .5) > .9
    np.testing.assert_allclose(np.abs(wall_normals[:, 0]).mean(), 1.0, atol=.05)

    floor_normals = surface_normals(floor)
    assert np.mean(np.linalg.norm(floor_normals, axis=1) > .5) < .1


def test_a_registration_may_not_invent_travel_the_odometry_never_saw():
    """Turning in place is where a depth fit lies about translation.

    A live survey pulled 124 keyframes of its last leg by 0.14 m, growing to
    0.38 m, with exact simulator odometry. The start of it is in the session
    record: while the robot rotated on the spot the ICP accepted a 0.0676 m
    translation, twice - each edge weighted (about 75) above the odometer edge it
    contradicted (40) - and the chain followed. An odometer is off by a few
    percent of the distance driven, so a correction is allowed to be a fraction
    of the motion this step actually observed and no more.
    """
    from tangying_robot_gateway.dense_slam import DenseSLAM

    allowance = DenseSLAM.correction_allowance_m
    assert allowance(np.array([0.0, 0.0, 0.40])) == pytest.approx(0.02), "rotation only"
    assert allowance(np.array([0.20, 0.0, 0.0])) == pytest.approx(0.05), "a fifth of a metre"
    assert allowance(np.array([0.0, 0.0, 0.0])) == pytest.approx(0.02)
    # The two real edges that started the pull, and the motion they claimed.
    assert allowance(np.array([0.0, 0.0, 0.18])) < 0.0676
    assert allowance(np.array([0.0, 0.0, 0.20])) < 0.0532
    assert DenseSLAM.correction_allowance_rad(np.array([0.0, 0.0, 0.40])) == pytest.approx(0.10)


def test_a_registration_that_claims_more_than_the_motion_allows_is_refused():
    from tangying_robot_gateway.dense_slam import register_depth

    rng = np.random.default_rng(11)
    room = room_surface(rng)
    # The same room, seen from 6 cm away: a fit that would happily report travel.
    shifted = transform(room, [.06, 0.0, 0.0])
    report = {}
    assert register_depth(shifted, room, [0.0, 0.0, 0.0], report=report,
                          max_correction_m=0.02) is None
    assert report["status"] == "correction_too_large", report
    assert report["correctionM"] > 0.02, report
    generous = register_depth(shifted, room, [0.0, 0.0, 0.0], max_correction_m=0.25)
    assert generous is not None, "a loop closure is allowed to move a pose a long way"


def test_an_underconstrained_registration_keeps_only_what_it_measured():
    """The pose graph has to see the difference, not just the fit quality.

    An ICP over one wall reports a perfect overlap and a pose whose along-wall
    component is noise. Weighting that direction away is what lets the
    measurement sharpen a room without being able to rotate it.
    """
    from tangying_robot_gateway.dense_slam import DenseSLAM, information_weight

    rng = np.random.default_rng(5)
    wall = np.zeros((900, 3))
    wall[:, 1] = -1.5 + rng.normal(0, 0.004, 900)
    wall[:, 0] = rng.uniform(-2, 2, 900)
    wall[:, 2] = rng.uniform(0.1, 1.4, 900)
    report = {}
    registered = register_depth(wall, transform(wall, [.06, .02, .01]), [.05, .01, 0.], report=report)
    assert registered is not None, report
    information = registered[1]["information"]
    weight = information_weight(information, 0.0)
    across = float(np.linalg.norm(weight @ np.array([0.0, 1.0, 0.0])))
    along = float(np.linalg.norm(weight @ np.array([1.0, 0.0, 0.0])))
    assert across > along, (across, along)
    assert DenseSLAM.LOOP_RADIUS_M > 0 and DenseSLAM.LOOP_MIN_GAP >= 20, (
        "a loop has to be far enough back in time to be a revisit, not a neighbour")


def test_the_slam_session_record_is_json_serialisable():
    """The provenance document is written to disk; an array in it is a crash.

    The information matrix that explains an edge's weight is a small array, and
    it is easy to leave one in a record that is later serialised. This session
    file is what an operator reads when a survey drifts, so it must survive the
    round trip.
    """
    import json

    from tangying_robot_gateway.dense_slam import DenseSLAM

    slam = DenseSLAM()
    for index in range(6):
        slam.add(frame(index * .3, 1000 + index))
    # A nested array is the shape that actually escaped: the information matrix
    # travels inside an edge record, not at its top level.
    slam.registration_attempts.append({"from": 0, "to": 1, "kind": "adjacent",
                                       "information": np.eye(3)})
    document = slam.provenance()
    json.dumps(document)          # must not raise
    assert isinstance(document["registrationAttempts"][-1]["information"], list)
    for record in document["registrations"] + document["registrationAttempts"]:
        assert not any(isinstance(value, np.ndarray) for value in record.values()), record


def test_a_published_leg_does_not_report_the_survey_as_finished(tmp_path):
    """A staged survey must not look finished between its legs.

    Between legs the map is published and the state was briefly "completed",
    which is indistinguishable from the end of the run: the CLI stopped after
    leg one and reported success while the robot went on exploring three more.
    """
    from tangying_robot_gateway.service_registry import ServiceError  # noqa: F401

    world = _ExplorationWorld(workarea=40.0)
    workflow = _explore_workflow(tmp_path, world)
    states = []
    opened = []

    def open_leg(number):
        # The state at the moment the survey moves on to the next leg: this is
        # what a polling client sees after the first map is published.
        states.append((workflow.state, workflow.message))
        opened.append(number)

    workflow._open_leg = open_leg
    original_move = workflow._move_and_sample

    def burn_frames(goal, *, bounded, fatal=True):
        original_move(goal, bounded=bounded, fatal=fatal)
        workflow.slam.frames.extend([object()] * 12)

    workflow._move_and_sample = burn_frames
    workflow._begin("explore", {"maxTravelM": 60.0, "maxLegs": 2})
    assert states, "the survey continued to a second leg"
    assert states[0][0] == "exploring", states[0]
    assert "继续" in states[0][1], states[0]
    assert opened == [2]
    assert workflow.state == "completed"


def _survey_keyframe(pose, points):
    from tangying_robot_gateway.dense_slam import Keyframe

    points = np.asarray(points, dtype=np.float32)
    return Keyframe(points=points, colors=np.full((len(points), 3), 128, np.uint8),
                    odometry=np.asarray(pose, dtype=float), pose=np.asarray(pose, dtype=float),
                    timestamp=1000, observation_id="obs", base_z=0.035, metadata={})


def test_the_planned_map_ignores_an_obstacle_only_one_keyframe_ever_saw(tmp_path):
    """Which keyframe saw a point decides whether it is an obstacle.

    The from-scratch survey published a handful of points at the commissioned
    kitchen goal - no scene geometry there, and the robot cannot drive into its
    own goal - which vetoed the goal as GOAL_NOT_CLEAR. One frame can put a
    mixed pixel at a depth edge anywhere it likes; two independent viewpoints
    agreeing is the cheapest honest bar, and it must reach the planner's grid,
    not only the saved map, or a route the map contains is still refused.
    """
    workflow, _owner = workflow_fixture(tmp_path)
    workflow._reservation = workflow.reserve()
    workflow.travelled = 0.0
    workflow._leg_anchor = None
    stray, wall = (1.00, 1.00), (2.00, 1.00)
    floor = np.array([[x, y, 0.0] for x in np.arange(.40, 2.21, .02)
                      for y in np.arange(.40, 1.21, .02)], dtype=np.float32)
    obstacle = np.array([[wall[0] + dx, wall[1] + dy, .50]
                         for dx in (-.02, .02) for dy in (-.02, .02)], dtype=np.float32)
    workflow.slam.frames = [
        _survey_keyframe([0., 0., 0.], np.vstack([floor, obstacle, [stray[0], stray[1], .50]])),
        # The same wall, seen again 30 cm later; the stray point is not repeated.
        _survey_keyframe([.30, 0., 0.], np.vstack([floor - [.30, 0., 0.], obstacle - [.30, 0., 0.]])),
    ]
    grid, _blind = workflow._live_grid()
    assert grid is not None

    def cell(x, y):
        return grid["cells"][int((y - grid["origin"][1]) / grid["resolution"]),
                             int((x - grid["origin"][0]) / grid["resolution"])]

    assert cell(*stray) == 0, "one frame's stray point must not become an obstacle"
    assert cell(*wall) == 100, "a wall two keyframes measured is still an obstacle"


def test_a_depth_starved_view_does_not_throw_the_survey_away(tmp_path):
    """Measured: a whole-house leg died at 74% mapped on one blank view.

    Too few measured depth points is a local sensor condition - a wall at arm's
    length, a dark corner - so the frame is skipped and the survey keeps driving.
    A run of them ends the leg and publishes what was mapped, with the reason
    named, instead of failing a session that was most of the way through a house.
    """
    from tangying_robot_gateway.robot_workflow import EXPLORATION

    workflow, _owner = workflow_fixture(tmp_path)
    workflow.calibration_revision = workflow.calibration_get()["revision"]

    class StarvingSLAM:
        frames: ClassVar[list] = []

        def add(self, _observation):
            raise ValueError("可用深度点不足，请调整相机朝向或检查标定。")

    workflow.slam = StarvingSLAM()
    workflow._sample()                       # must not raise
    assert workflow._depth_starved == 1

    class BrokenSLAM:
        frames: ClassVar[list] = []

        def add(self, _observation):
            raise ValueError("invalid metric RGB-D frame sizes")

    workflow.slam = BrokenSLAM()
    with pytest.raises(ValueError, match="frame sizes"):
        # A malformed frame is not local weather: it still fails loudly.
        workflow._sample()
    assert EXPLORATION["depthStarvedLimit"] >= 10


# ── robot-side session faults: recover, or at least keep the evidence ───────
# These are the faults an operator actually meets on a robot: a capture arrives
# stale, someone re-runs calibration mid-scan, a wall at arm's length measures
# almost no depth. Each one used to end the session and discard everything mapped
# so far - measured, a 74%-mapped house produced no map at all. A fault now
# publishes what exists and records why it stopped, because the geometry already
# measured does not stop being evidence.

def _faulted_workflow(tmp_path, *, frames=4, travelled=0.8):
    """A workflow mid-survey whose next capture raises a session fault."""
    workflow, _owner = workflow_fixture(tmp_path)
    workflow.calibration_revision = workflow.calibration_get()["revision"]
    workflow.name = "faulted"
    workflow.map_id = "scan-faulted"
    workflow._base_map_id = ""
    workflow._summary["travelledM"] = travelled

    class _Frame:
        def __init__(self, index):
            self.odometry = np.array([0.02 * index, 0.0, 0.0])
            self.timestamp = 1_700_000_000_000 + index
            self.pose = np.array([0.02 * index, 0.0, 0.0])
            self.points = np.zeros((4, 3), dtype=np.float32)
            self.colors = np.zeros((4, 3), dtype=np.uint8)

    class _SLAM:
        def __init__(self, count):
            self.frames = [_Frame(index) for index in range(count)]
            self.registrations = [object()] * max(1, count - 1)

        def provenance(self):
            return {"engine": "test"}

        def optimize(self):
            return None

    workflow.slam = _SLAM(frames)
    return workflow


def test_a_stale_capture_publishes_what_was_mapped_and_names_the_fault(tmp_path):
    """Stale is transient. The map already measured is still evidence."""
    from tangying_robot_gateway.service_registry import ServiceError

    workflow = _faulted_workflow(tmp_path)
    published = {}

    def build(fault=None):
        published["fault"] = fault
        # Stand in for the real publish: the test asserts the fault travels with it.
        workflow.state = "failed" if fault else "completed"
        workflow.message = f"扫描中断（{fault.get('code')}）" if fault else "ok"

    workflow._build = build
    error = ServiceError("STALE_CAPTURE", "RGB-D 与底盘位姿已过期，停止扫描。")
    workflow._handle_session_fault(error)
    assert published["fault"] == {"code": "STALE_CAPTURE",
                                  "message": "RGB-D 与底盘位姿已过期，停止扫描。"}
    assert workflow.state == "failed" and "STALE_CAPTURE" in workflow.message


def test_a_calibration_change_mid_scan_keeps_the_map(tmp_path):
    from tangying_robot_gateway.service_registry import ServiceError

    workflow = _faulted_workflow(tmp_path)
    published = {}
    workflow._build = lambda fault=None: published.update(fault=fault)
    workflow._handle_session_fault(ServiceError("CALIBRATION_CHANGED", "标定发生变化，请重新扫描。"))
    assert published["fault"]["code"] == "CALIBRATION_CHANGED"


def test_an_unexpected_fault_is_still_named_in_the_record(tmp_path):

    workflow = _faulted_workflow(tmp_path)
    published = {}
    workflow._build = lambda fault=None: published.update(fault=fault)
    workflow._handle_session_fault(RuntimeError("solver exploded"))
    assert published["fault"]["code"] == "WORKFLOW_FAULT"
    assert "solver exploded" in published["fault"]["message"]


def test_a_session_with_nothing_measured_is_not_published_as_a_map(tmp_path):
    """Publishing an empty revision would be noise dressed as progress."""
    from tangying_robot_gateway.service_registry import ServiceError

    workflow = _faulted_workflow(tmp_path, frames=0, travelled=0.0)
    published = {}
    workflow._build = lambda fault=None: published.update(fault=fault)
    workflow._handle_session_fault(ServiceError("STALE_CAPTURE", "过期"))
    assert published == {}, "nothing measured must not become a map"
    assert workflow.state == "failed"


def test_a_published_partial_map_says_so_in_its_provenance(tmp_path):
    """The durable record has to distinguish a partial survey from a complete one."""
    import json as _json

    from tangying_robot_gateway.map_pipeline import PointCloud, build_map

    directory = tmp_path / "scan-partial"
    cloud = PointCloud(np.array([[0., 0., 0.], [1., 1., .5]], dtype=np.float32))
    manifest = build_map(directory, map_id="scan-partial", robot_id="unit-1", cloud=cloud,
                         calibration_revision="a" * 64, slam_metadata={
                             "partial": True, "fault": {"code": "STALE_CAPTURE", "message": "过期"}})
    record = _json.loads((directory / manifest["artifacts"]["slam_session"]["href"]).read_text())
    assert record["partial"] is True, record.keys()
    assert record["fault"]["code"] == "STALE_CAPTURE"


# ── map conflicts: report, never rewrite ────────────────────────────────────
# The map is a surveyed navigation basis. "The route that worked yesterday is
# blocked today" has to stay explainable, so a task must not silently edit it.
# What was missing is the observation that map and measurements disagree.

def test_conflicts_reports_the_disagreement_without_touching_the_map(tmp_path):
    workflow = _faulted_workflow(tmp_path, frames=4)
    workflow.map_id = "scan-conflicts"
    workflow.active = {"mapId": "scan-map", "mapRevision": "rev-1"}
    # A small map whose centre is free, and a cloud that puts points there.
    cells = np.zeros((20, 20), dtype=np.int16)
    workflow.grid = {"width": 20, "height": 20, "resolution": .05, "origin": [0., 0., 0.],
                     "cells": cells}
    cells_before = cells.copy()
    import numpy as _np
    workflow.slam.cloud = lambda: type("Cloud", (), {
        "xyz": _np.array([[0.5, 0.5, 0.2], [0.6, 0.5, 0.2], [0.7, 0.5, 0.2],
                          [0.5, 0.6, 0.2], [9.9, 9.9, 0.2]], dtype=_np.float32)})()

    result = workflow.conflicts()
    assert result["available"] is True
    assert result["mapRevision"] == "rev-1"
    assert result["measuredPoints"] == 5
    # Four points land in cells the map calls free; the one outside the map is
    # ignored rather than counted as a conflict.
    assert result["occupiedInMapFreeInCloud"] == 4
    assert result["freeInMapOccupiedInCloud"] == 0
    assert result["conflictFraction"] == pytest.approx(0.8)
    # Enough conflict to suggest a re-survey, and it says how, not does it.
    assert result["suggestsRescan"] is True
    assert "baseMapId" in result["note"]
    assert np.array_equal(cells, cells_before), "the map must not be rewritten"


def test_conflicts_declines_when_there_is_nothing_to_compare(tmp_path):
    workflow = _faulted_workflow(tmp_path, frames=0)
    assert workflow.conflicts()["available"] is False
    workflow = _faulted_workflow(tmp_path, frames=4)
    workflow.active = None
    assert workflow.conflicts()["available"] is False
