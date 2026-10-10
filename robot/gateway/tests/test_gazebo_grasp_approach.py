"""Offline FK/IK replay of run-002 measurements, never a physical success test."""

import threading
from types import SimpleNamespace as NS

import numpy as np
import pytest
from tangying_robot_gateway.arm_kinematics import arm_links, chain_poses
from tangying_robot_gateway.gazebo_actuation import validate_chunk
from tangying_robot_gateway.gazebo_manipulation import (
    GazeboManipulation,
    plan_empty_arm_approach,
    plan_tip_chunk,
    pose_matrix,
    solve_tip,
)
from tangying_robot_gateway.gazebo_tools import tool_commissioning, tool_pose
from tangying_robot_gateway.runtime import Command, Result

# Runtime journal observation, not a configured world pose:
# gazebo-home_furnished/head-rgbd-261260200000000-1791568451340
BASE = [2.051344551251087, 3.0002529952830903, .035,
        .7667298993953489, 0., 0., .6419698290209582]
OBJECT = np.array([2.4515001112462356, 3.333774199761013, .7909868361224217])
DESTINATION = [2.458995248850189, 3.3350008908945, .7309792842265203]
JOINTS = {
    "right_arm_shoulder_pan": -7.347553151126742e-06,
    "right_arm_wrist_flex": -4.776898378090909e-07,
    "left_arm_gripper": -6.926253699087333e-07,
    "right_arm_shoulder_lift": 3.0999997972910847,
    "right_arm_wrist_roll": .00010747966752697917,
    "right_arm_gripper": -1.4024013679130175e-06,
    "left_arm_shoulder_pan": -3.481757582444006e-07,
    "left_arm_wrist_roll": .00010747831181098831,
    "left_arm_shoulder_lift": 3.099999790993557,
    "left_arm_elbow_flex": 1.0000009452939447,
    "left_arm_wrist_flex": -3.140401782830832e-07,
    "right_arm_elbow_flex": 1.0000010210463524,
}


def inputs():
    tool, _ = tool_commissioning()
    links = arm_links("left")
    upright = tool_pose(chain_poses(links, {link.motor: 0. for link in links},
                                   base=np.eye(4)), tool)[:3, :3].T @ [0., 0., 1.]
    return tool, links, upright, pose_matrix(BASE)


def test_recorded_endpoint_is_reachable_but_original_cartesian_chord_is_not():
    tool, _, upright, base = inputs()
    overhead = OBJECT+[0., 0., .16]
    # The old plan admitted these endpoints; the actual Cartesian approach
    # instead hit an IK branch boundary before execute_chunk was reached.
    solve_tip(overhead, JOINTS, base, upright=upright, tool=tool,
              fixed_gripper=tool["gripperOpenRad"])
    with pytest.raises(ValueError, match="GRASP_TARGET_UNREACHABLE"):
        plan_tip_chunk(overhead, JOINTS, base, tool=tool,
                       fixed_gripper=tool["gripperOpenRad"])


def test_recorded_approach_keeps_every_actuator_ramp_above_measured_target():
    tool, links, upright, base = inputs()
    chunk, target = plan_empty_arm_approach(OBJECT, JOINTS, base,
                                          upright=upright, side="left", tool=tool)
    assert 1 <= len(chunk) <= 64
    measured = {name: JOINTS[name] for name in target}
    for waypoint in validate_chunk(chunk):
        assert waypoint["left_arm_gripper"] <= tool["gripperOpenRad"]
        while max(abs(waypoint[name]-measured[name]) for name in waypoint) > 1e-9:
            measured = {name: value+np.clip(waypoint[name]-value, -.025, .025)
                        for name, value in measured.items()}
            tip = tool_pose(chain_poses(links, measured, base=base), tool)
            assert tip[2, 3] >= OBJECT[2]+.16-.008
    tip = tool_pose(chain_poses(links, measured, base=base), tool)
    assert np.linalg.norm(tip[:3, 3]-(OBJECT+[0., 0., .16])) <= .008
    assert np.linalg.norm(tip[:3, :3] @ upright-[0., 0., 1.]) <= .12
    assert measured["left_arm_gripper"] == tool["gripperOpenRad"]
    # The subsequent measured-target descent uses the same bounded Cartesian
    # planner as execution, not merely another reachable endpoint.
    plan_tip_chunk(OBJECT+[0., 0., tool["contactHeightM"]], measured, base,
                   upright=upright, tool=tool, fixed_gripper=tool["gripperOpenRad"])


@pytest.mark.parametrize("case", ["low_start", "outside_workspace"])
def test_unsafe_approach_is_rejected_without_relaxing_reachability(case):
    tool, links, upright, base = inputs()
    joints, position = dict(JOINTS), OBJECT.copy()
    if case == "low_start":
        joints.update({link.motor: 0. for link in links})
    else:
        position[0] += 2.
    with pytest.raises(ValueError, match="GRASP_APPROACH_CLEARANCE|GRASP_TARGET_UNREACHABLE"):
        plan_empty_arm_approach(position, joints, base, upright=upright, side="left", tool=tool)


@pytest.mark.parametrize("feedback_age", [0., .501, -.001])
def test_preflight_and_pick_use_identical_approach_before_any_dispatch(monkeypatch, feedback_age):
    import tangying_robot_gateway.gazebo_manipulation as module

    tool, _, _, base = inputs()
    node = NS(joint_snapshot=lambda: (dict(JOINTS), 0., 1),
              runtime=NS(base_pose=base, robot_id="offline-recording", calibration_revision="recorded-calibration"))
    controller = GazeboManipulation(NS(node=node, home=True, cancel_event=threading.Event()))
    controller.capture = lambda: (NS(observation_id="recorded-plan-observation", wall_time_unix_ms=1,
        robot_state={"base_pose": BASE, "perception": {"sensor_stamp_ns": "1", "calibration_revision": "recorded-calibration"}},
        reconstruction={"robotId": "offline-recording", "entities": []}), {
        "ceramic-mug": NS(entity_id="ceramic-mug", category="cup", confidence=.9, attributes={}, pose_xyz_quat=OBJECT),
        "kitchen-tray": NS(entity_id="kitchen-tray", category="storage_bin", confidence=.9,
                           attributes={"recognition": "rgbd_metric_rim"}, pose_xyz_quat=DESTINATION)})
    controller.state = lambda: {"attached": False, "toolCommissioning": tool}
    expected = []
    original = module.plan_empty_arm_approach

    def record(*args, **kwargs):
        chunk, target = original(*args, **kwargs)
        expected.append((kwargs["side"], chunk))
        return chunk, target

    monkeypatch.setattr(module, "plan_empty_arm_approach", record)
    plan = controller.execute(Command(schema_version="robot.v1", command_id="plan", task_id="task",
        capability="plan_grasp", parameters={"objectId": "ceramic-mug", "destinationId": "kitchen-tray"}))
    assert plan.success
    preflight = next(chunk for side, chunk in expected if side == controller.side)
    sent = []
    monkeypatch.setattr(module, "execute_chunk", lambda _node, chunk, _cancel:
                        sent.append(chunk) or Result(False, "OFFLINE_DISPATCH_BOUNDARY"))
    node.joint_snapshot = lambda: (dict(JOINTS), feedback_age, 2)
    result = controller.execute(Command(schema_version="robot.v1", command_id="pick", task_id="task",
                                       capability="manipulation.pick", target_ref="ceramic-mug"))
    assert not result.success
    if feedback_age == 0.:
        assert result.code == "OFFLINE_DISPATCH_BOUNDARY"
        assert sent == [preflight]
    else:
        assert result.code == "JOINT_FEEDBACK_STALE"
        assert sent == []
