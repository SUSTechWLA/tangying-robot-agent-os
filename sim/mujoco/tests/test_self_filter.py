import time
from dataclasses import replace

import mujoco
import numpy as np
import pytest
from tangying_robot_gateway.rgbd import RgbdFrame
from tangying_sim.rgbd_navigation import NavigationController
from tangying_sim.rgbd_runtime import RgbdTabletopWorld
from tangying_sim.self_filter import RobotSelfFilter, robot_joint_positions


def frame(depth):
    transform = np.diag([1.0, -1.0, -1.0, 1.0])
    transform[:3, 3] = [0, 0, 2]
    return RgbdFrame("robot", "camera", "optical", "cal1", int(time.time()*1000), 1,
                     np.zeros((3, 3, 3), np.uint8), np.asarray(depth, dtype=float),
                     np.array([[4., 0, 1], [0, 4., 1], [0, 0, 1.]]), transform)


def build_filter(tmp_path, geometry):
    path = tmp_path / "robot.xml"
    path.write_text('<mujoco><worldbody><body name="chassis">'+geometry+'</body></worldbody></mujoco>')
    return RobotSelfFilter(robot_xml=path, model_revision="test-robot-cad-v1")


def apply(predictor, capture, joints=None, base=None):
    return predictor.filter(capture, joints or {}, base or [0, 0, 0, 1, 0, 0, 0],
                            joints_observed_at_unix_ms=capture.captured_at_unix_ms)


def test_only_matching_first_robot_surface_is_masked_and_input_is_untouched(tmp_path):
    predictor = build_filter(tmp_path, '<geom type="box" size=".5 .5 .01" pos="0 0 1"/>')
    capture = frame(np.full((3, 3), .99))
    capture.depth_m[0, 0] = .5  # Foreground object in front of the robot CAD.
    capture.depth_m[1, 1] = 1.3  # Surface disagrees with the expected robot front face.
    capture.depth_m[2, 2] = np.nan
    original = capture.depth_m.copy()
    result = apply(predictor, capture)
    assert result.mask.dtype == np.uint8
    assert result.mask.sum() == 6
    assert result.mask[0, 0] == result.mask[1, 1] == result.mask[2, 2] == 0
    np.testing.assert_array_equal(capture.depth_m, original)
    assert result.model_revision == "test-robot-cad-v1"


def test_external_surface_in_robot_cad_hole_is_never_removed_by_envelope(tmp_path):
    predictor = build_filter(tmp_path,
        '<geom type="box" size=".1 .5 .01" pos="-.3 0 1"/>'
        '<geom type="box" size=".1 .5 .01" pos=".3 0 1"/>')
    result = apply(predictor, frame(np.full((3, 3), .99)))
    np.testing.assert_array_equal(result.mask[:, 1], [0, 0, 0])
    assert result.mask[:, 0].sum() == result.mask[:, 2].sum() == 3


def test_broad_phase_matches_full_ray_reference_with_foreground_holes_and_background(tmp_path, monkeypatch):
    predictor = build_filter(tmp_path,
        '<geom type="box" size=".1 .5 .01" pos="-.3 0 1"/>'
        '<geom type="box" size=".1 .5 .01" pos=".3 0 1"/>')
    capture = frame(np.full((3, 3), .99))
    capture.depth_m[0, 0] = .5  # Closer external object, not the robot surface.
    capture.depth_m[1, 1] = 3.0  # Measured background outside robot bounds.
    capture.depth_m[2, 2] = np.nan
    row, col = np.indices((3, 3))
    rays = np.stack(((col - 1) / 4, (row - 1) / 4, np.ones((3, 3))), axis=-1).reshape(-1, 3)
    rays /= np.linalg.norm(rays, axis=1)[:, None]
    directions = np.ascontiguousarray(rays @ capture.world_from_camera[:3, :3].T).ravel()
    original = mujoco.mj_multiRay
    reference = np.full(9, -1., dtype=np.float64)
    checked = []

    def compare(model, data, origin, *args):
        original(model, data, origin, directions, predictor._geom_groups, True, -1,
                 np.full(9, -1, dtype=np.int32), reference, None, 9, 10.)
        checked.append(args[-2])
        return original(model, data, origin, *args)

    monkeypatch.setattr(mujoco, "mj_multiRay", compare)
    result = apply(predictor, capture)
    predicted = (reference * rays[:, 2]).reshape(3, 3)
    expected = np.isfinite(capture.depth_m) & (predicted > .02) & (np.abs(capture.depth_m - predicted) <= .002)
    np.testing.assert_array_equal(result.mask, expected.astype(np.uint8))
    assert checked and checked[0] < 9


def test_calibrated_joint_pose_and_base_transform_determine_self_surface(tmp_path):
    predictor = build_filter(tmp_path,
        '<body><joint name="arm" type="slide" axis="1 0 0"/>'
        '<geom type="box" size=".1 .5 .01" pos="0 0 1"/></body>')
    assert predictor.required_joint_names == ("arm",)
    capture = frame(np.full((3, 3), .99))
    initial = apply(predictor, capture, {"arm": 0})
    moved = apply(predictor, capture, {"arm": .25})
    assert initial.mask[:, 1].sum() == 3 and moved.mask[:, 2].sum() == 3
    assert moved.mask[:, 1].sum() == 0
    moved_frame = replace(capture, world_from_camera=capture.world_from_camera.copy())
    moved_frame.world_from_camera[0, 3] += 1
    shifted = apply(predictor, moved_frame, {"arm": .25}, [1, 0, 0, 1, 0, 0, 0])
    np.testing.assert_array_equal(shifted.mask, moved.mask)


@pytest.mark.parametrize("joint_type", ["hinge", "slide"])
def test_numpy_joint_types_keep_named_scalar_capture_encoders(tmp_path, joint_type):
    predictor = build_filter(tmp_path,
        f'<joint name="arm" type="{joint_type}" axis="1 0 0"/>'
        '<geom type="box" size=".1 .1 .1"/>')
    # MuJoCo exposes the model array as numpy scalars. Membership comparisons
    # against the 3.12 Python enums are asymmetric unless both become integers.
    assert isinstance(predictor.model.jnt_type[0], np.integer)
    state = mujoco.MjData(predictor.model)
    state.qpos[0] = .125
    assert predictor.required_joint_names == ("arm",)
    assert robot_joint_positions(predictor.model, state) == {"arm": .125}


@pytest.mark.parametrize("joint", [
    '<joint name="arm" type="free"/>',
    '<joint name="arm" type="ball"/>',
    '<joint type="hinge"/>',
])
def test_non_scalar_or_unnamed_robot_joints_remain_rejected(tmp_path, joint):
    with pytest.raises(ValueError, match="named scalar encoder joints"):
        build_filter(tmp_path, joint+'<geom type="box" size=".1 .1 .1"/>')


def test_missing_or_wrong_capture_joint_data_is_rejected(tmp_path):
    predictor = build_filter(tmp_path,
        '<body><joint name="arm" type="slide" axis="1 0 0"/>'
        '<geom type="box" size=".1 .5 .01" pos="0 0 1"/></body>')
    capture = frame(np.ones((3, 3)))
    for joints in ({}, {"arm": float("nan")}, {"arm": True}):
        with pytest.raises(ValueError):
            apply(predictor, capture, joints)
    with pytest.raises(ValueError, match="same capture"):
        predictor.filter(capture, {"arm": 0}, [0, 0, 0, 1, 0, 0, 0],
                         joints_observed_at_unix_ms=capture.captured_at_unix_ms-1)
    with pytest.raises(ValueError, match="stale"):
        apply(predictor, replace(capture, captured_at_unix_ms=capture.captured_at_unix_ms-3000), {"arm": 0})


def test_model_must_contain_only_attached_robot_geometry(tmp_path):
    path = tmp_path / "scene.xml"
    path.write_text('<mujoco><worldbody><body name="chassis"><geom size=".1"/></body>'
                    '<body name="table"><geom size=".1"/></body></worldbody></mujoco>')
    with pytest.raises(ValueError, match="robot-only"):
        RobotSelfFilter(robot_xml=path, model_revision="invalid-scene")


def test_actual_bottom_camera_self_returns_match_separate_robot_only_cad(monkeypatch):
    monkeypatch.setenv("TANGYING_NAVIGATION_URL", "http://127.0.0.1:18790")
    world = RgbdTabletopWorld.seeded(7)
    navigation = NavigationController(world, world.robot_id)
    predictor = RobotSelfFilter()
    try:
        acquired = navigation.capture_with_state()
        capture = acquired.frame
        assert set(acquired.joint_positions) == set(predictor.required_joint_names)
        assert len(acquired.joint_positions) == 17
        result = predictor.filter(capture, acquired.joint_positions, acquired.base_pose,
                                  joints_observed_at_unix_ms=acquired.joints_observed_at_unix_ms)
        assert result.mask.shape == capture.depth_m.shape and result.mask.sum() > 100
        # Original floor rays remain measured; no self filtering fabricates depth.
        assert np.count_nonzero(result.mask == 0) > result.mask.size*.7
        assert mujoco.mj_name2id(predictor.model, mujoco.mjtObj.mjOBJ_BODY, "table") == -1
        assert mujoco.mj_name2id(predictor.model, mujoco.mjtObj.mjOBJ_BODY, "red_cup") == -1
    finally:
        predictor.close()
        navigation.close()


def test_capture_keeps_original_encoder_snapshot_when_live_controller_advances(monkeypatch):
    world = RgbdTabletopWorld.seeded(7)
    navigation = NavigationController(world, world.robot_id)
    original_render = navigation.renderer.render_rgbd
    old_pitch = float(world.data.qpos[world.model.joint("Pitch_L").qposadr[0]])

    def advancing_render(model, frozen_data):
        # Another controller step publishes while this capture renders the
        # original copy. The filter must never pair those new encoders with it.
        world.data.qpos[model.joint("Pitch_L").qposadr[0]] = old_pitch+.2
        mujoco.mj_forward(model, world.data)
        world._publish_sensor_snapshot()
        return original_render(model, frozen_data)

    monkeypatch.setattr(navigation.renderer, "render_rgbd", advancing_render)
    try:
        acquired = navigation.capture_with_state()
        assert acquired.joint_positions["Pitch_L"] == old_pitch
        assert world.sensor_snapshot[1]["_self_filter_joint_positions"]["Pitch_L"] == old_pitch+.2
        acquired.joint_positions["Pitch_L"] = 999
        assert world.sensor_snapshot[1]["_self_filter_joint_positions"]["Pitch_L"] == old_pitch+.2
        assert acquired.joints_observed_at_unix_ms == acquired.frame.captured_at_unix_ms
    finally:
        navigation.close()
