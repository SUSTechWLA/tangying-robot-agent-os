import numpy as np
import pytest
from tangying_robot_gateway.cad_self_filter import CadSelfFilter
from tangying_robot_gateway.rgbd import RgbdFrame


def robot(tmp_path):
    description = tmp_path/'robot.sdf'
    description.write_text('''<sdf><world><model name="tangying_robot">
      <link name="base_link"><pose>0 0 0 0 0 0</pose></link>
      <link name="wrist"><pose>0 0 1 0 0 0</pose><visual name="tool">
        <geometry><box><size>0.2 0.2 0.2</size></box></geometry></visual></link>
      <joint name="wrist_motor" type="prismatic"><parent>base_link</parent><child>wrist</child>
        <pose>0 0 0 0 0 0</pose><axis><xyz>0 0 1</xyz></axis></joint>
      </model><model name="unrelated_object"><link name="object"><visual name="ignored">
        <geometry><box><size>100 100 100</size></box></geometry></visual></link></model>
      </world></sdf>''')
    return CadSelfFilter(description, tmp_path, revision='measured-cad')


def frame(depth, sequence=1):
    return RgbdFrame('robot', 'robot/head', 'world', 'calibration', 1, sequence,
                     np.zeros((1, 1, 3), dtype=np.uint8), np.array([[depth]]),
                     np.eye(3), np.eye(4))


def test_only_exact_robot_surface_is_removed_and_encoders_move_it(tmp_path):
    cad = robot(tmp_path)
    assert cad.filter(frame(.9), {'wrist_motor': 0.}, np.eye(4), joint_stamp_ns=1).item()
    # A foreground payload and a background surface inside/behind the CAD's
    # broad phase must not become robot pixels.
    assert not cad.filter(frame(.8, 2), {'wrist_motor': 0.}, np.eye(4), joint_stamp_ns=2).item()
    assert not cad.filter(frame(1., 3), {'wrist_motor': 0.}, np.eye(4), joint_stamp_ns=3).item()
    assert not cad.filter(frame(.9, 4), {'wrist_motor': .1}, np.eye(4), joint_stamp_ns=4).item()
    assert cad.filter(frame(1., 5), {'wrist_motor': .1}, np.eye(4), joint_stamp_ns=5).item()


def test_missing_or_stale_capture_encoders_are_refused(tmp_path):
    cad = robot(tmp_path)
    with pytest.raises(ValueError, match='ENCODER_MISSING'):
        cad.filter(frame(.9), {}, np.eye(4), joint_stamp_ns=1)
    with pytest.raises(ValueError, match='UNSYNCHRONIZED'):
        cad.filter(frame(.9), {'wrist_motor': 0.}, np.eye(4), joint_stamp_ns=300_000_001)
