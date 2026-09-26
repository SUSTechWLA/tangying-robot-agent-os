"""Replay a real Gazebo capture; the detector receives only RGB-D/calibration."""
from pathlib import Path

import numpy as np
from tangying_robot_gateway.household_perception import HouseholdRgbdPerception
from tangying_robot_gateway.rgbd import RgbdFrame


def test_visible_mug_is_not_ambiguous_with_independently_measured_vase():
    with np.load(Path(__file__).parent/'fixtures/gazebo-xlerobot-kitchen-rgbd.npz') as capture:
        frame = RgbdFrame('recorded-robot', 'recorded-robot/head-rgbd', 'world',
                          'recorded-calibration', 1, 1, capture['rgb'], capture['depth'],
                          capture['k'], capture['world'])
        detector = HouseholdRgbdPerception()
        detections = {item.entity_id: item for item in detector._detect(frame)}
        assert {'ceramic-mug', 'ceramic-vase', 'kitchen-tray'} <= detections.keys()
        assert not np.any(detections['ceramic-mug'].mask & detections['ceramic-vase'].mask)
        center, dimensions = detector._geometry['ceramic-mug']
        # Diagnostic measurement bounds from this captured image, not an input
        # to detection and not a synthetic command success assertion.
        assert 2.23 < center[0] < 2.32 and 3.37 < center[1] < 3.46
        assert .78 < center[2] < .81 and dimensions[2] == .12
