import struct
import sys
import threading
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest
from google.protobuf.json_format import MessageToDict
from tangying_robot_gateway.contracts import RobotProfile, validate_reconstruction
from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld
from tangying_sim.workflow_services import static_world_revision


@pytest.fixture
def room_service(monkeypatch):
    # Keep the real home, robot and sensor paths. Only the optional large asset
    # conversion is replaced, so this contract test requires no download.
    monkeypatch.setitem(sys.modules, "tangying_sim.furnished_home",
                        SimpleNamespace(apply_furnished_home=lambda spec, path: None))
    monkeypatch.setenv("TANGYING_HOME_ASSET_PACK", "/unused-asset-fixture")
    monkeypatch.delenv("TANGYING_NAVIGATION_URL", raising=False)
    world = RgbdTabletopWorld.seeded(7, scene="home")
    service = RgbdRuntimeService(world, robot_id="room-test")
    try:
        yield service
    finally:
        service.close()


def test_fixed_views_are_registered_measured_cameras_without_semantic_truth(room_service):
    profile = RobotProfile.model_validate(room_service._profile_wire)
    for source in ("room-test/room-rgbd", "room-test/workspace-rgbd", "room-test/home-rgbd"):
        observation = next(room_service.Observe(robot_pb2.ObserveRequest(source_id=source), None))
        wire = MessageToDict(observation.reconstruction)
        wire["observedAtUnixMs"] = int(wire["observedAtUnixMs"])
        wire["sequence"] = int(wire["sequence"])
        wire["pointColors"] = [[int(value) for value in color] for color in wire["pointColors"]]
        reconstruction = validate_reconstruction(wire, profile)
        assert reconstruction.source_id == source
        assert reconstruction.source_type == "rgbd_camera"
        assert not observation.entities
        assert struct.unpack(">II", observation.compressed_image[16:24]) == (640, 480)
        assert len(observation.compressed_image) > 1000
    # Task and SLAM inputs remain the mounted cameras; room captures cannot
    # overwrite the primary task reconstruction.
    assert room_service._last_scene is None
    assert room_service.workflow_bindings.capture().reconstruction["sourceId"].endswith("/base-rgbd")


def test_unknown_room_source_rejected_and_stale_snapshot_not_retimed(room_service, monkeypatch):
    with pytest.raises(ValueError, match="unknown camera"):
        next(room_service.Observe(robot_pb2.ObserveRequest(source_id="other/room-rgbd"), None))
    world = room_service.world
    data, state, _ = world.sensor_snapshot
    monkeypatch.setattr(world, "_publish_sensor_snapshot", lambda: None)
    world.sensor_snapshot = (data, state, 1)
    with pytest.raises(ValueError, match="stale"):
        room_service.room_cameras.capture("room-test/room-rgbd")


def test_world_identity_covers_collected_surfaces_and_ignores_runtime_pose():
    xml = '<mujoco><asset><texture name="t" type="2d" builtin="checker" width="8" height="8" rgb1=".2 .3 .4" rgb2=".5 .6 .7"/><material name="m" texture="t"/></asset><worldbody><body><freejoint/><geom type="box" size=".2 .3 .4" material="m"/></body></worldbody></mujoco>'
    first = mujoco.MjModel.from_xml_string(xml)
    second = mujoco.MjModel.from_xml_string(xml)
    assert static_world_revision(first) == static_world_revision(second)
    data = mujoco.MjData(first)
    data.qpos[0] = 3
    mujoco.mj_forward(first, data)
    assert static_world_revision(first) == static_world_revision(second)
    first.tex_data[0] = np.uint8(int(first.tex_data[0]) ^ 1)
    assert static_world_revision(first) != static_world_revision(second)


def test_world_identity_covers_face_uv_bindings():
    xml = '''<mujoco><asset><mesh name="tetra"
        vertex="0 0 0 1 0 0 0 1 0 0 0 1"
        face="0 2 1 0 1 3 0 3 2 1 2 3"
        texcoord="0 0 1 0 0 1 1 1"/></asset>
        <worldbody><geom type="mesh" mesh="tetra"/></worldbody></mujoco>'''
    first = mujoco.MjModel.from_xml_string(xml)
    second = mujoco.MjModel.from_xml_string(xml)
    assert static_world_revision(first) == static_world_revision(second)
    first.mesh_facetexcoord[0, :2] = first.mesh_facetexcoord[0, :2][::-1]
    assert np.array_equal(first.mesh_vert, second.mesh_vert)
    assert np.array_equal(first.mesh_texcoord, second.mesh_texcoord)
    assert static_world_revision(first) != static_world_revision(second)


def test_survey_observes_both_sides_and_restores_bounded_travel_headings():
    from tangying_sim.home_scene import HOME_WAYPOINTS
    from tangying_sim.workflow_services import WorkflowBindings
    bindings = object.__new__(WorkflowBindings)
    bindings.service = SimpleNamespace(world=SimpleNamespace(scene="home_task"))
    goals = bindings.survey_goals()
    assert len(goals) == 24
    previous = HOME_WAYPOINTS["living_room"]
    for goal in goals:
        assert np.isfinite(goal).all()
        angle = 2*np.arctan2(goal[6], goal[3])
        prior_angle = 2*np.arctan2(previous[6], previous[3])
        delta = np.arctan2(np.sin(angle-prior_angle), np.cos(angle-prior_angle))
        assert abs(delta) <= .5
        previous = goal
    assert np.allclose(goals[-1], HOME_WAYPOINTS["living_room"])


def test_bounded_move_reaches_the_controller_as_a_direct_step(monkeypatch):
    """The provider's bounded flag must survive all the way to the controller.

    A bounded operator step and a commissioned survey goal are the same skill
    with the same geometry; only the request itself says which one it is. If the
    flag is dropped anywhere between them, the household router takes over and a
    short nudge becomes a multi-metre detour.
    """
    from tangying_sim.home_scene import HOME_WAYPOINTS
    from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld
    from tangying_sim.tools import ToolResult

    monkeypatch.delenv("TANGYING_NAVIGATION_URL", raising=False)
    runtime = RgbdRuntimeService(RgbdTabletopWorld.seeded(7, scene="home_task"),
                                 robot_id="bounded-provider-test")
    runtime.navigation.sleep_scale = 0.0
    seen = []

    def record(goal, cancel=None, *, route=True):
        seen.append(route)
        return ToolResult(True, "NAV_REACHED", "recorded", 1.0)

    monkeypatch.setattr(runtime.navigation, "navigate", record)
    bindings = runtime.workflow_bindings
    try:
        goal = HOME_WAYPOINTS["kitchen"]
        for bounded in (True, False, True):
            seen.clear()
            receipt = bindings.move(goal, threading.Event(), bounded=bounded)
            assert receipt["ok"], receipt
            assert seen == [not bounded], (bounded, seen)
            assert getattr(runtime._service_owner, "bounded", False) is False
            assert runtime._service_owner.enabled is False
    finally:
        runtime.close()
