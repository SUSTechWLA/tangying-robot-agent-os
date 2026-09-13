import math
import threading

import mujoco
import numpy as np
from tangying_sim.home_scene import HOME_WAYPOINTS
from tangying_sim.motion import HOME
from tangying_sim.rgbd_runtime import RgbdRuntimeService, RgbdTabletopWorld

ARM_STEMS = ("Rotation", "Pitch", "Elbow", "Wrist_Pitch", "Wrist_Roll")
STOWED = np.array([0.0, 3.1, 1.0, 0.0, 0.0])


def _arm_pose(world, suffix):
    return np.array([world.joint_positions()[f"{stem}_{suffix}"] for stem in ARM_STEMS])


def _set_right_home_left_stowed(world):
    for name, value in world.motion.target_for("right", "HOME").items():
        if name.startswith("Jaw_"):
            continue
        joint = world.model.joint(name).id
        world.data.qpos[world.model.jnt_qposadr[joint]] = value
    mujoco.mj_forward(world.model, world.data)


def test_completed_kitchen_task_stows_home_arm_and_returns_to_living_room(monkeypatch):
    monkeypatch.delenv("TANGYING_NAVIGATION_URL", raising=False)
    world = RgbdTabletopWorld.seeded(7, scene="home_task")
    service = RgbdRuntimeService(world, robot_id="return-stow-test")
    service.navigation.sleep_scale = 0.0
    try:
        assert world.prepare_navigation(threading.Event()).success
        assert service.navigation.navigate(HOME_WAYPOINTS["kitchen"]).success
        assert world.select_arm("red-cup") == "right"
        assert world.pick("red-cup").success
        assert world.verify_grasp("red-cup").success
        assert world.place("kitchen-bin").success
        assert world.verify_inside("red-cup", "kitchen-bin").success

        np.testing.assert_allclose(_arm_pose(world, "L"), [HOME[stem] for stem in ARM_STEMS])
        np.testing.assert_allclose(_arm_pose(world, "R"), STOWED)
        before_base = world.robot_state()["base_pose"]
        before_jaws = {
            name: value for name, value in world.joint_positions().items()
            if name.startswith("Jaw_")
        }

        stow = world.prepare_navigation(threading.Event())
        assert stow.success, stow
        assert stow.code == "NAV_ARMS_STOWED"
        assert world.robot_state()["base_pose"] == before_base
        np.testing.assert_allclose(_arm_pose(world, "L"), STOWED)
        np.testing.assert_allclose(_arm_pose(world, "R"), STOWED)
        assert {
            name: value for name, value in world.joint_positions().items()
            if name.startswith("Jaw_")
        } == before_jaws

        returned = service.navigation.navigate(HOME_WAYPOINTS["living_room"])
        assert returned.success, returned
        actual = world.robot_state()["base_pose"]
        expected = HOME_WAYPOINTS["living_room"]
        np.testing.assert_allclose(actual[:3], expected[:3], atol=1e-6)
        actual_yaw = 2 * math.atan2(actual[6], actual[3])
        expected_yaw = 2 * math.atan2(expected[6], expected[3])
        yaw_error = math.atan2(
            math.sin(actual_yaw - expected_yaw), math.cos(actual_yaw - expected_yaw)
        )
        assert abs(yaw_error) <= service.navigation.limits.yaw_tolerance_rad + 1e-12
    finally:
        service.close()


def test_stow_preflight_rejects_intermediate_contact_without_mutating_live_pose(monkeypatch):
    monkeypatch.delenv("TANGYING_NAVIGATION_URL", raising=False)
    world = RgbdTabletopWorld.seeded(7, scene="home_task")
    service = RgbdRuntimeService(world, robot_id="stow-contact-test")
    try:
        assert world.prepare_navigation(threading.Event()).success
        _set_right_home_left_stowed(world)

        names = [f"{stem}_{suffix}" for suffix in ("L", "R") for stem in ARM_STEMS]
        joints = np.array([world.model.joint(name).id for name in names])
        addresses = world.model.jnt_qposadr[joints]
        start = world.data.qpos[addresses].copy()
        finish = np.tile(STOWED, 2)
        probe = mujoco.MjData(world.model)
        mujoco.mj_copyData(probe, world.model, world.data)
        probe.qpos[addresses] = start + (finish - start) * 0.5
        mujoco.mj_forward(world.model, probe)
        jaw_geoms = np.flatnonzero(
            world.model.geom_bodyid == world.model.body("Fixed_Jaw").id
        )
        obstacle_position = probe.geom_xpos[jaw_geoms[0]].copy()
        world._set_free_body_position("red_cup_free", obstacle_position)
        mujoco.mj_forward(world.model, world.data)

        midpoint = mujoco.MjData(world.model)
        mujoco.mj_copyData(midpoint, world.model, world.data)
        midpoint.qpos[addresses] = start + (finish - start) * 0.5
        mujoco.mj_forward(world.model, midpoint)

        red_geom = world.model.geom("red_cup_visual").id
        robot_bodies = {world.model.body("chassis").id}
        for body in range(world.model.body("chassis").id + 1, world.model.nbody):
            if int(world.model.body_parentid[body]) in robot_bodies:
                robot_bodies.add(body)

        def red_touches_robot(data):
            return any(
                contact.dist < -1e-5
                and red_geom in {contact.geom1, contact.geom2}
                and (
                    int(world.model.geom_bodyid[contact.geom1]) in robot_bodies
                    or int(world.model.geom_bodyid[contact.geom2]) in robot_bodies
                )
                for contact in data.contact
            )

        endpoint = mujoco.MjData(world.model)
        mujoco.mj_copyData(endpoint, world.model, world.data)
        endpoint.qpos[addresses] = finish
        mujoco.mj_forward(world.model, endpoint)
        mujoco.mj_forward(world.model, midpoint)
        assert not red_touches_robot(world.data)
        assert red_touches_robot(midpoint)
        assert not red_touches_robot(endpoint)

        before = world.data.qpos.copy()
        result = world.prepare_navigation(threading.Event())
        assert not result.success
        assert result.code == "NAV_STOW_CONTACT"
        np.testing.assert_array_equal(world.data.qpos, before)
    finally:
        service.close()


def test_stow_preserves_unknown_start_rejection_without_mutating_pose(monkeypatch):
    monkeypatch.delenv("TANGYING_NAVIGATION_URL", raising=False)
    world = RgbdTabletopWorld.seeded(7, scene="home_task")
    service = RgbdRuntimeService(world, robot_id="stow-unknown-test")
    try:
        joint = world.model.joint("Pitch_L").id
        world.data.qpos[world.model.jnt_qposadr[joint]] = 0.1
        mujoco.mj_forward(world.model, world.data)
        before = world.data.qpos.copy()

        result = world.prepare_navigation(threading.Event())
        assert not result.success
        assert result.code == "NAV_STOW_START_UNSUPPORTED"
        np.testing.assert_array_equal(world.data.qpos, before)
    finally:
        service.close()
