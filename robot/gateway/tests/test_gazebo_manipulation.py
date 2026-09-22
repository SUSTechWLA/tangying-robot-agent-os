import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from tangying_robot_gateway.arm_kinematics import arm_links, chain_poses
from tangying_robot_gateway.contracts import RobotProfile, validate_tool_parameters
from tangying_robot_gateway.gazebo_manipulation import GazeboManipulation, solve_tip
from tangying_robot_gateway.gazebo_perception import GazeboWorkcellPerception
from tangying_robot_gateway.rgbd import RgbdFrame


def test_internal_motion_planning_is_explicit_and_does_not_weaken_existing_adapters():
    from robot.gateway.tests.test_plugin_contracts import arm_profile
    profile = RobotProfile.model_validate(arm_profile())
    with pytest.raises(ValueError, match="action_chunk"):
        validate_tool_parameters("manipulation.pick", {"targetRef": "cup"}, profile, target_ref="cup")
    profile.internally_planned_tools = ["manipulation.pick"]
    validate_tool_parameters("manipulation.pick", {"targetRef": "cup"}, profile, target_ref="cup")
    with pytest.raises(ValueError, match="target"):
        validate_tool_parameters("manipulation.pick", {}, profile)
    with pytest.raises(ValueError):
        validate_tool_parameters("arm.move", {}, profile)


def test_tip_inverse_kinematics_meets_measured_chain_and_refuses_unreachable():
    links = arm_links("left")
    joints = {link.motor: 0. for link in links}
    target = np.array([.44, -.24, .86])
    result = solve_tip(target, joints, np.eye(4))
    actual = chain_poses(links, result, base=np.eye(4))[-1][:3, 3]
    assert np.linalg.norm(actual-target) < .008
    with pytest.raises(ValueError, match="UNREACHABLE"):
        solve_tip(np.array([2., 0., .86]), joints, np.eye(4))


def test_navigation_stow_keeps_both_arm_links_inside_chassis_footprint():
    for side in ("left", "right"):
        links = arm_links(side)
        joints = dict(zip([link.motor for link in links], (0., 2.5, 1.5, 1., 0., 0.), strict=True))
        for pose in chain_poses(links, joints, base=np.eye(4)):
            # Include the simplified cylinder radius; all links are contained
            # by the declared conservative rectangle for base navigation.
            x, y = pose[:2, 3]
            assert -.33+.025 < x < .325-.025
            assert abs(y)+.025 < .335


def test_far_edge_payload_can_lift_with_a_small_inward_retraction():
    links = arm_links("left")
    joints = dict(zip([link.motor for link in links],
                      (-.0753, .4220, .5908, .2111, .00009, .3816), strict=True))
    pose = chain_poses(links, joints, base=np.eye(4))[-1]
    upright = pose[:3, :3].T @ [0., 0., 1.]
    target = pose[:3, 3]+[-.04, 0., .10]
    for point in np.linspace(pose[:3, 3], target, 4)[1:]:
        joints = solve_tip(point, joints, np.eye(4), upright=upright)
    result = chain_poses(links, joints, base=np.eye(4))[-1]
    assert np.linalg.norm(result[:3, 3]-target) < .008
    assert result[2, 3]-pose[2, 3] > .055


def test_navigation_stow_refuses_held_payload_before_any_joint_command():
    c = controller({"attached": True})
    assert c.stow_for_navigation(threading.Event()).code == "PAYLOAD_HELD_REQUIRES_PLACE"


def coloured_frame():
    rgb = np.zeros((64, 64, 3), np.uint8)
    rgb[12:20, 12:20] = [200, 10, 10]
    transform = np.diag([1., -1., -1., 1.])
    transform[:3, 3] = [.55, -.25, 1.5]
    return RgbdFrame("gz", "gz/head", "optical", "rev", int(time.time()*1000), 1,
        rgb, np.full((64, 64), .5), np.array([[60., 0., 31.5], [0., 60., 31.5], [0., 0., 1.]]), transform)


def test_detector_uses_depth_and_drops_ambiguous_or_occluded_targets():
    detector, frame = GazeboWorkcellPerception(), coloured_frame()
    detected = detector.reconstruct(frame)
    assert [e.entity_id for e in detected.entities] == ["red-cup"]
    assert detected.entities[0].pose[2] == pytest.approx(.94)
    # Same colour outside the calibrated manipulation volume is scenery.
    frame.rgb[12:20, 55:63] = [200, 10, 10]
    assert [e.entity_id for e in detector.reconstruct(frame).entities] == ["red-cup"]
    frame.rgb[40:48, 40:48] = [200, 10, 10]
    assert detector.reconstruct(frame).entities == []
    frame.rgb[:] = 0
    assert detector.reconstruct(frame).entities == []


def controller(state):
    node = SimpleNamespace(suction_snapshot=lambda: state)
    backend = SimpleNamespace(node=node, cancel_event=threading.Event())
    result = GazeboManipulation(backend)
    result.acquisition = {"object": "red-cup", "z": 1.}
    # Exercise the verification predicate without a three-second timeout.
    result.stable_samples = lambda predicate: state if predicate(state) is not None else None
    return result


def test_grasp_ack_cannot_prove_lift_or_correct_payload_identity():
    state = {"attached": True, "held": "red_cup", "objects": {"red_cup": [0., 0., 1.]},
             "tips": {"left": [0., 0., 1.05]}}
    c = controller(state)
    assert not c.verify_grasp("red-cup").success
    state["objects"]["red_cup"][2] = 1.1
    assert c.verify_grasp("red-cup").success
    state["held"] = "blue_bottle"
    assert not c.verify_grasp("red-cup").success


def test_placement_needs_release_full_containment_upright_and_support():
    state = {"attached": False, "objects": {
        "red_cup": [0., 0., 1.075, 1., 0., 0., 0.],
        "tray_floor": [0., 0., 1., 1., 0., 0., 0.]}}
    c = controller(state)
    assert c.verify_placement("red-cup", "right-bin").success
    state["attached"] = True
    assert not c.verify_placement("red-cup", "right-bin").success
    state["attached"] = False
    state["objects"]["red_cup"][0] = .06
    assert not c.verify_placement("red-cup", "right-bin").success
    state["objects"]["red_cup"][0] = 0.
    state["objects"]["red_cup"][2] = 1.2
    assert not c.verify_placement("red-cup", "right-bin").success
