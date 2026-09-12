import time
from pathlib import Path

import mujoco
from tangying_robot_proto.robot.v1 import robot_pb2
from tangying_sim.home_scene import (
    HOME_MODEL_PATH,
    HOME_ROOMS,
    HOME_TASK_OBJECT_PLACEMENTS,
    HOME_TASK_OBJECTS,
    HOME_TASK_SCENE_REVISION,
    HOME_TASK_WORK_VOLUME,
    HOME_WAYPOINTS,
    PERCEPTION_PENDING,
    load_home_model,
    validate_home_model,
)
from tangying_sim.rgbd_navigation import load_navigation_model
from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld


def test_home_scene_descriptor_has_four_rooms_and_connected_waypoints():
    assert HOME_MODEL_PATH == Path(__file__).resolve().parents[1] / "assets" / "xlerobot_home.xml"
    assert HOME_ROOMS == ("living_room", "home_corridor", "kitchen", "bedroom", "bathroom")
    assert tuple(HOME_WAYPOINTS) == HOME_ROOMS
    assert all(len(pose) == 7 for pose in HOME_WAYPOINTS.values())


def test_home_scene_compiles_with_room_bodies_and_robot_cameras():
    model = load_home_model()
    validate_home_model(model)
    for name in ("living_room", "bedroom", "bathroom", "kitchen", "home_corridor", "chassis"):
        assert model.body(name).id >= 0
    for name in ("head_depth", "overview"):
        assert model.camera(name).id >= 0


def test_home_navigation_model_adds_bottom_rgbd_without_tabletop_commissioning():
    model = load_navigation_model(HOME_MODEL_PATH, scene="home")
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "table") < 0
    assert model.camera("base_depth").id >= 0
    assert model.body("living_room").id >= 0


def test_home_task_model_adds_rgbd_visible_kitchen_fixtures_without_truth_entities():
    model = load_navigation_model(HOME_MODEL_PATH, scene="home_task")
    assert HOME_TASK_SCENE_REVISION.startswith("home-task-")
    # The catalogue is pinned so it cannot drift from the scene. Both objects are
    # advertised and both must exist as free bodies, because an object that exists
    # physically but is not advertised (or the reverse) is the failure this list
    # prevents.
    assert HOME_TASK_OBJECTS == (
        ("red-cup", "red_cup", "red_cup_free", "cup", "red"),
        ("blue-cup", "blue_cup", "blue_cup_free", "cup", "blue"),
        ("green-cup", "green_cup", "green_cup_free", "cup", "green"),
    )
    for name in ("home_task_table", "red_cup", "blue_cup", "green_cup", "kitchen_bin", "chassis"):
        assert model.body(name).id >= 0
    for joint in ("red_cup_free", "blue_cup_free", "green_cup_free"):
        assert model.joint(joint).id >= 0


def test_home_task_objects_start_clear_of_each_other_and_inside_the_work_volume():
    """Both failure modes are silent at runtime and expensive to diagnose there.

    Objects that overlap start the task already colliding, and an object outside the
    work volume is simply never perceived - which reads as a perception bug rather
    than a placement mistake.
    """
    joints = {joint for _id, _slug, joint, _category, _colour in HOME_TASK_OBJECTS}
    assert set(HOME_TASK_OBJECT_PLACEMENTS) == joints, "every advertised object needs a placement"
    for joint, position in HOME_TASK_OBJECT_PLACEMENTS.items():
        for axis, index in (("x", 0), ("y", 1), ("z", 2)):
            low, high = HOME_TASK_WORK_VOLUME[axis]
            assert low <= position[index] <= high, (
                f"{joint} starts at {axis}={position[index]:.3f}, outside the work "
                f"volume ({low}, {high}); perception could never see it"
            )
    positions = list(HOME_TASK_OBJECT_PLACEMENTS.values())
    for index, first in enumerate(positions):
        for second in positions[index + 1:]:
            gap = sum((a - b) ** 2 for a, b in zip(first, second, strict=True)) ** 0.5
            assert gap > 0.05, f"objects start only {gap:.3f} m apart"


def test_home_task_runtime_observes_only_rgbd_task_fixtures(monkeypatch):
    monkeypatch.delenv("TANGYING_NAVIGATION_URL", raising=False)
    world = RgbdTabletopWorld.seeded(7, scene="home_task")
    service = RgbdRuntimeService(world, robot_id="home-task-test")
    try:
        monkeypatch.setattr("tangying_sim.rgbd_navigation.time.sleep", lambda _seconds: None)
        assert service.navigation.navigate(HOME_WAYPOINTS["kitchen"]).success
        observation = next(service.Observe(robot_pb2.ObserveRequest(), None))
        ids = {entity.entity_id for entity in observation.entities}
        assert "red-cup" in ids and "kitchen-bin" in ids
        # Every advertised object has to be observable. An object that is in the
        # catalogue but that perception cannot see is worse than one that is absent:
        # the agent is told the object exists and then fails to find it, and the
        # failure looks like a perception bug rather than a missing detector.
        for item_id, _slug, _joint, _category, colour in HOME_TASK_OBJECTS:
            assert item_id in ids or item_id in PERCEPTION_PENDING, (
                f"{item_id} ({colour}) is advertised but perception never reports it"
            )
        assert "blue-bottle" not in ids and "right-bin" not in ids
        assert observation.robot_state["perception"]["ground_truth_fallback"] is False
    finally:
        service.close()


def test_home_runtime_exposes_head_and_base_rgbd_only_navigation():
    world = RgbdTabletopWorld.seeded(7, scene="home")
    service = RgbdRuntimeService(world, robot_id="home-test")
    try:
        info = service.GetRuntimeInfo(None, None)
        assert "navigation.navigate" in info.skills
        head = next(service.Observe(robot_pb2.ObserveRequest(streams=["rgbd_raw"]), None))
        base = next(service.Observe(robot_pb2.ObserveRequest(source_id="home-test/base-rgbd", streams=["rgbd_raw"]), None))
        assert head.rgbd_frame.width > 0 and head.rgbd_frame.height > 0
        assert base.rgbd_frame.width == head.rgbd_frame.width
        assert base.robot_state["navigation"]["scene"] == "home"
    finally:
        service.close()


def test_home_verify_arrival_uses_fresh_base_rgbd_and_pose_evidence():
    from google.protobuf.json_format import ParseDict

    world = RgbdTabletopWorld.seeded(7, scene="home")
    service = RgbdRuntimeService(world, robot_id="home-verify")
    try:
        goal = list(HOME_WAYPOINTS["living_room"])
        command = robot_pb2.SkillCommand(
            schema_version="robot.v1", command_id="verify-arrival", task_id="home-task",
            skill="verify_arrival", deadline_unix_ms=int(time.time() * 1000) + 5_000,
            lease_ms=2_000, idempotency_key="verify-arrival", safety_profile="simulation",
        )
        ParseDict({"goalPose": goal}, command.parameters)
        event = list(service.execute_for_test(command))[-1]
        assert event.type == robot_pb2.SKILL_EVENT_SUCCEEDED
        assert event.code == "NAV_ARRIVAL_CONFIRMED"
        assert event.evidence_observation.reconstruction["sourceId"] == "home-verify/base-rgbd"
        assert event.evidence_observation.robot_state["verification"]["pose_source"] == "sim_proprioceptive_odom"
    finally:
        service.close()
