"""Does the gateway's forward kinematics describe the model MuJoCo actually moves?

Hand-eye calibration is ``AX = XB``, and a wrong ``A`` produces a camera mount that
looks exactly like a measured one. So the kinematics this repository solves with is
compared here against the only thing that knows where the links are: MuJoCo's own
``xpos``/``xmat`` for the same joint configuration. A test that only checked the FK
against itself would pass with a mirrored axis, a swapped chain order or a wrong
zero, and none of those would be visible downstream.

The second thing checked here is the *zero*: the wizard asks an operator to put each
joint in a described pose and calls that zero (``calibration_wizard.py:53``), and the
count-to-angle conversion assumes that pose is the model's assembled ``qpos = 0``.
That assumption is the one that cannot be recovered from the data afterwards, so it
is checked against the model's geometry rather than stated.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import mujoco
import numpy as np
import pytest
from tangying_robot_gateway import arm_kinematics
from tangying_robot_gateway.arm_kinematics import (
    ArmLink,
    KinematicsError,
    arm_link_poses,
    arm_links,
    chain_poses,
    joint_angles_from_counts,
)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

#: How many random arm configurations the FK is compared at. Each one exercises all
#: six joints of both arms at once; twenty-four is far more than the six degrees of
#: freedom need, and the cost is a single ``mj_forward`` per configuration.
RANDOM_CONFIGURATIONS = 24

#: Agreement demanded of a pose. The two implementations are the same arithmetic in
#: a different order, so the honest tolerance is floating-point noise, not a
#: millimetre: anything looser would hide a transposed quaternion.
POSE_TOLERANCE = 1e-9


@pytest.fixture(scope="module")
def model():
    from tangying_sim.rgbd_navigation import load_navigation_model

    return load_navigation_model(scene="home_task")


def mujoco_poses(model, angles: dict[str, float]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Where MuJoCo itself puts each modelled body at these joint angles."""
    data = mujoco.MjData(model)
    for link in arm_kinematics.all_links():
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, link.joint_name)
        assert joint_id >= 0, f"the model has no joint {link.joint_name}"
        data.qpos[model.jnt_qposadr[joint_id]] = angles[link.motor]
    mujoco.mj_forward(model, data)
    out = {}
    for link in arm_kinematics.all_links():
        body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, link.body)
        assert body_id >= 0, f"the model has no body {link.body}"
        out[link.link] = (np.array(data.xpos[body_id]), np.array(data.xmat[body_id]).reshape(3, 3))
    return out


def random_angles(generator: np.random.Generator) -> dict[str, float]:
    return {link.motor: float(generator.uniform(link.range_min, link.range_max))
            for link in arm_kinematics.all_links()}


def segment_angle_from_horizontal(start: np.ndarray, end: np.ndarray) -> float:
    """How far a link is from level, in degrees."""
    delta = np.asarray(end) - np.asarray(start)
    return math.degrees(math.atan2(abs(delta[2]), math.hypot(delta[0], delta[1])))


def test_forward_kinematics_matches_mujoco_over_random_configurations(model):
    generator = np.random.default_rng(20240919)
    worst = 0.0
    for _ in range(RANDOM_CONFIGURATIONS):
        angles = random_angles(generator)
        truth = mujoco_poses(model, angles)
        for link in arm_kinematics.all_links():
            pose = arm_kinematics.link_pose(link.link, angles)
            position, rotation = truth[link.link]
            worst = max(worst,
                        float(np.abs(pose[:3, 3] - position).max()),
                        float(np.abs(pose[:3, :3] - rotation).max()))
    assert worst < POSE_TOLERANCE, f"FK disagrees with MuJoCo by {worst:g}"


def test_the_frozen_table_is_a_copy_of_the_live_model():
    """The checked-in table is a cache, and this is what keeps it one.

    Without this, editing the model's link lengths would leave the gateway solving
    hand-eye against geometry the robot no longer has, and every test above would
    still pass because they all read the same stale table.
    """
    from tangying_sim.model import MODEL_REVISION

    from scripts.generate_arm_kinematics import (
        ASSET_PATH,
        build_table,
        load_model,
        serialize,
    )

    model, model_path, mjcf_path = load_model("home_task")
    rebuilt = serialize(build_table(model, scene="home_task", model_path=model_path,
                                    mjcf_path=mjcf_path, model_revision=MODEL_REVISION))
    assert ASSET_PATH.read_text(encoding="utf-8") == rebuilt, (
        "the frozen arm table no longer matches the model; "
        "run scripts/generate_arm_kinematics.py")
    assert json.loads(rebuilt)["arms"].keys() == {"left", "right"}
    # The table records which model it came from, so a table regenerated against a
    # different revision is visible rather than merely different.
    assert arm_kinematics.table_provenance()["modelRevision"] == MODEL_REVISION


def test_the_base_link_the_chain_hangs_off_is_the_models_chassis(model):
    """The root of the chain, checked against MuJoCo like the links are.

    It cancels out of every ``A`` in ``AX = XB`` — only the arm's motion relative to
    its own base matters — so a wrong root would move the numbers FK reports without
    moving a single solved camera. Worth pinning for exactly that reason: it is the
    one part of a link pose no hand-eye result can catch.
    """
    chassis = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "chassis")
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    base = arm_kinematics.base_pose()
    assert np.abs(base[:3, 3] - data.xpos[chassis]).max() < POSE_TOLERANCE
    assert np.abs(base[:3, :3] - data.xmat[chassis].reshape(3, 3)).max() < POSE_TOLERANCE


def test_the_chain_order_is_the_servo_order(model):
    """Index *i* of the chain is servo id *i* — the assumption motor names rest on.

    ``ARM_JOINTS`` names the six servos of an arm in the order a person moves along
    it, and the wizard reads ``{side}_arm_{joint}`` for every one of them. If the
    model's chain order were different, every pose would be built from the right
    numbers in the wrong places — silently, and with the wrong-hand shape to show
    for it.
    """
    del model
    from tangying_robot_gateway.calibration import ARM_JOINTS

    for side in ("left", "right"):
        links = arm_links(side)
        assert [link.joint for link in links] == list(ARM_JOINTS)
        zero = {link.motor: 0.0 for link in links}
        poses = arm_link_poses(side, zero, base=np.eye(4))
        # Servo 1 is the shoulder pan: its axis is vertical at the assembled pose.
        pan_axis = poses[links[0].link][:3, :3] @ np.array(links[0].axis)
        assert abs(abs(pan_axis[2]) - 1.0) < 1e-6, f"{side} servo 1 is not the pan joint"
        # Servo 5 is the wrist roll: it turns about the arm's own long axis, which is
        # the section between the wrist pitch joint and the roll joint itself.
        roll_axis = poses[links[4].link][:3, :3] @ np.array(links[4].axis)
        roll_shaft = poses[links[4].link][:3, 3] - poses[links[3].link][:3, 3]
        roll_shaft = roll_shaft / np.linalg.norm(roll_shaft)
        assert abs(abs(float(np.dot(roll_axis, roll_shaft))) - 1.0) < 1e-6, (
            f"{side} servo 5 is not the wrist roll")
        # Servo 6 is the gripper, and it is last: the moving jaw is the chain's end.
        assert links[5].joint == "gripper"
        assert links[5].body in ("Moving_Jaw", "Moving_Jaw_2")


def test_the_zero_pose_the_wizard_asks_for_is_the_models_assembled_pose():
    """The conversion from counts assumes the wizard's zero is ``qpos = 0``.

    Nothing in a capture record can reveal a wrong zero: a constant offset on every
    joint is absorbed into the arm poses, and the extrinsic that comes out is a
    plausible shape in the wrong place. So the pose the wizard *describes* in words
    (``calibration_wizard.py:53``) is compared with the geometry of the assembled
    model, in the chassis frame, using the FK that the test above ties to MuJoCo.
    """
    links = arm_links("left")
    zero = {link.motor: 0.0 for link in links}
    poses = arm_link_poses("left", zero, base=np.eye(4))
    origin = {name: pose[:3, 3] for name, pose in poses.items()}

    # "把大臂抬到与地面平行的高度" — the upper arm (shoulder lift joint at link2 to the
    # elbow joint at link3) is level.
    upper_arm = segment_angle_from_horizontal(origin["left_arm_link2"], origin["left_arm_link3"])
    assert upper_arm < 20.0, f"the assembled upper arm is {upper_arm:.1f}° from level"
    # "把小臂伸直，与地面平行" — the forearm is level.
    forearm = segment_angle_from_horizontal(origin["left_arm_link3"], origin["left_arm_link4"])
    assert forearm < 5.0, f"the assembled forearm is {forearm:.1f}° from level"
    # "让手腕保持水平，夹爪朝前" — the wrist section is exactly level.
    wrist = segment_angle_from_horizontal(origin["left_arm_link4"], origin["left_arm_link5"])
    assert wrist < 1.0, f"the assembled wrist is {wrist:.1f}° from level"
    # "把这一节转到正对机器人正前方" — the arm lies along the chassis' forward axis,
    # which is where the base camera looks (see the derived base extrinsic).
    arm = origin["left_arm_link4"] - origin["left_arm_link1"]
    azimuth = math.degrees(math.atan2(arm[1], arm[0]))
    assert abs(azimuth) < 1.0, f"the assembled arm points {azimuth:.1f}° off forward"
    # "让夹爪的两个指头上下相对（不要左右相对）" — the jaw closes in the vertical plane,
    # about a horizontal axis, so the two fingers are stacked.
    jaw_axis = poses["left_arm_link6"][:3, :3] @ np.array(links[5].axis)
    assert abs(jaw_axis[2]) < 0.01, "the jaw axis is not horizontal"
    fingers = origin["left_arm_link6"] - origin["left_arm_link5"]
    assert fingers[2] > 0.015, "the two fingers are not stacked vertically"

    # And the alternative reading — zero at the middle of the model's joint range —
    # is not the pose those words describe. It is off by a right angle and a half.
    middle = dict(zero)
    middle["left_arm_shoulder_lift"] = 0.5 * (links[1].range_min + links[1].range_max)
    middle_poses = arm_link_poses("left", middle, base=np.eye(4))
    middle_arm = segment_angle_from_horizontal(
        middle_poses["left_arm_link2"][:3, 3], middle_poses["left_arm_link3"][:3, 3])
    assert middle_arm > 45.0, (
        "the two readings of 'zero' are not distinguishable in this model, so the "
        "choice between them is not evidence-backed after all")


def test_every_count_the_model_can_reach_maps_back_to_the_same_pose(model):
    """Counts and angles round-trip across the whole range the model allows.

    Compared through the *pose*, not through the number: a whole turn is not a
    different joint position, and the wrist roll's reach *is* a whole turn, so its
    two ends are the same pose written two ways. What has to hold is that the count
    the model would report for a reachable angle puts the link back where the model
    puts it.
    """
    del model
    zero = {link.motor: 0.0 for link in arm_kinematics.all_links()}
    for link in arm_kinematics.all_links():
        for fraction in (0.0, 0.01, 0.25, 0.5, 0.75, 0.99, 1.0):
            angle = link.range_min + fraction * link.range_span
            count = round(arm_kinematics.ZERO_COUNT
                          + angle * arm_kinematics.COUNTS_PER_TURN / (2 * math.pi))
            # A joint that reaches past half a turn reports the wrapped count; that
            # is what an absolute encoder does, and the model's range un-wraps it.
            count %= arm_kinematics.COUNTS_PER_TURN
            solved = joint_angles_from_counts({link.motor: count})[link.motor]
            assert link.range_min - 1e-9 <= solved <= link.range_max + 1e-9, (
                f"{link.motor} count {count} solved outside the joint's range")
            # Exact, and free of the encoder's resolution: the angle that came back
            # is the angle whose count is the one that went in. A wrong turn would
            # come back 4096 counts away.
            assert round(arm_kinematics.ZERO_COUNT
                         + solved * arm_kinematics.COUNTS_PER_TURN / (2 * math.pi)) \
                % arm_kinematics.COUNTS_PER_TURN == count, (
                f"{link.motor} count {count} came back as {math.degrees(solved):.2f}°, "
                "which is a different count")
            expected = arm_kinematics.link_pose(link.link, {**zero, link.motor: angle})
            observed = arm_kinematics.link_pose(link.link, {**zero, link.motor: solved})
            # The count is quantised to 0.088°, so the two poses differ by half a
            # count carried through the joints below this one — up to a few
            # millimetres at the end of a half-metre arm. That is the floor here, and
            # it is stated rather than tuned: a wrong turn is three orders larger.
            assert np.abs(expected - observed).max() < 5e-3, (
                f"{link.motor} at {math.degrees(angle):.2f}° (count {count}) came back as "
                f"{math.degrees(solved):.2f}° and moved the link")


def test_a_count_past_half_a_turn_is_placed_in_the_turn_the_model_allows():
    """``shoulder_lift`` is the case the branch rule exists for.

    The model gives it 197.7° above its zero — past half a turn — so at that pose the
    servo reads a count *below* 2048. Reading it as the small negative angle the
    count literally spells would put the upper arm 160° away from where it is, and
    nothing downstream would notice.
    """
    solved = joint_angles_from_counts({"left_arm_shoulder_lift": 201})
    assert math.degrees(solved["left_arm_shoulder_lift"]) == pytest.approx(197.67, abs=0.1)


def test_a_count_the_model_cannot_explain_is_refused_not_wrapped():
    """A reading that fits no turn inside the joint's range means the document or the
    arm is not what the solver thinks it is — not that the angle should be wrapped."""
    # shoulder_lift spans -5.7°…197.7°, leaving a 156° arc this joint cannot be in.
    # Count 1000 spells -92.1°, and -92.1 + 360 = 267.9°: neither is inside.
    with pytest.raises(KinematicsError) as failure:
        joint_angles_from_counts({"left_arm_shoulder_lift": 1000})
    assert failure.value.code == "READING_OUTSIDE_JOINT_RANGE"
    assert "shoulder_lift" in failure.value.message
    # A reading a little further along the same arc is still refused (the gap is
    # 156° wide), and one on the other side of it is accepted — so the refusal is
    # about the gap and not about the joint.
    with pytest.raises(KinematicsError):
        joint_angles_from_counts({"left_arm_shoulder_lift": 1900})
    assert 0.0 < joint_angles_from_counts({"left_arm_shoulder_lift": 2500})["left_arm_shoulder_lift"]


#: A one-joint chain whose joint is deliberately *not* at its body origin, with a
#: rotation on both bodies. MuJoCo's ``jnt_pos`` is the joint anchor in the body
#: frame and a hinge turns about that anchor, so a body whose anchor is elsewhere
#: moves its origin too. Every arm joint in this repository's model has its anchor
#: at the body origin, where "turn about the anchor" and "turn about the origin"
#: give identical numbers — which is exactly why the distinction needs a model like
#: this one to be testable at all.
MOVED_ANCHOR_XML = """
<mujoco>
  <worldbody>
    <body name="base" pos="0 0 0.4" quat="0.70710678 0 0 0.70710678">
      <body name="l1" pos="0.1 0.2 0.3" quat="0.9238795 0 0.3826834 0">
        <joint name="j1" type="hinge" axis="1 0 0" pos="0.05 0.02 0.01"/>
        <geom type="box" size="0.01 0.01 0.01"/>
      </body>
    </body>
  </worldbody>
</mujoco>
"""


def test_forward_kinematics_matches_mujoco_for_a_moved_anchor():
    """The composition rule, checked where a wrong reading of it would show."""
    from tangying_robot_gateway.arm_kinematics import _quaternion_matrix

    model = mujoco.MjModel.from_xml_string(MOVED_ANCHOR_XML)
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "l1")
    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "j1")
    base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
    link = ArmLink(
        side="left", index=1, link="synthetic_link", motor="j1", joint="j1",
        body="l1", parent_body="base", joint_name="j1",
        position=tuple(float(value) for value in model.body_pos[body_id]),
        quaternion=tuple(float(value) for value in model.body_quat[body_id]),
        axis=tuple(float(value) for value in model.jnt_axis[joint_id]),
        anchor=tuple(float(value) for value in model.jnt_pos[joint_id]),
        range_min=float(model.jnt_range[joint_id][0]),
        range_max=float(model.jnt_range[joint_id][1]),
    )
    base = np.eye(4)
    base[:3, :3] = np.array(_quaternion_matrix(model.body_quat[base_id]))
    base[:3, 3] = model.body_pos[base_id]
    for angle in (-1.3, -0.4, 0.0, 0.7, 2.1):
        data = mujoco.MjData(model)
        data.qpos[model.jnt_qposadr[joint_id]] = angle
        mujoco.mj_forward(model, data)
        (pose,) = chain_poses([link], {"j1": angle}, base=base)
        assert np.abs(pose[:3, 3] - data.xpos[body_id]).max() < POSE_TOLERANCE
        assert np.abs(pose[:3, :3] - data.xmat[body_id].reshape(3, 3)).max() < POSE_TOLERANCE
