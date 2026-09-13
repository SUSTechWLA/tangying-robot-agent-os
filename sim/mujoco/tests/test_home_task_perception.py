import time
from dataclasses import replace

import numpy as np
import pytest
from tangying_robot_gateway.rgbd import RgbdFrame
from tangying_sim.rgbd_perception import HomeTaskRgbdPerception


def _frame(*, tx=2.4, rgb=None, stamp=None, sequence=1,
           source_id="unit/head", transform_revision="cal-1"):
    image = np.zeros((30, 30, 3), dtype=np.uint8)
    image[:] = [20, 50, 200] if rgb is None else rgb
    transform = np.diag([1.0, -1.0, -1.0, 1.0])
    transform[:3, 3] = [tx, 3.65, 1.33]
    return RgbdFrame(
        "robot-1", source_id, "head_depth_optical", transform_revision,
        int(time.time() * 1000) if stamp is None else stamp, sequence,
        image, np.full((30, 30), 0.5),
        np.array([[100.0, 0.0, 14.5], [0.0, 100.0, 14.5], [0.0, 0.0, 1.0]]),
        transform,
    )


def _fragment(frame):
    rgb = np.zeros_like(frame.rgb)
    rgb[10:18, 16:19] = [20, 50, 200]
    return replace(frame, rgb=rgb)


def _compact_blue_object(frame):
    rgb = np.zeros_like(frame.rgb)
    rgb[10:20, 10:20] = [20, 50, 200]
    return replace(frame, rgb=rgb)


def _bin_x(perception, frame):
    scene = perception.reconstruct(frame)
    entity = next((item for item in scene.entities if item.entity_id == "kitchen-bin"), None)
    return None if entity is None else entity.pose[0]


def test_complete_current_bin_measurement_replaces_distant_history():
    now = int(time.time() * 1000)
    perception = HomeTaskRgbdPerception()
    first = _bin_x(perception, _frame(tx=2.4, stamp=now - 50, sequence=1))
    moved = _bin_x(perception, _frame(tx=2.7, stamp=now - 20, sequence=2))
    assert first == pytest.approx(2.4125, abs=0.01)
    assert moved == pytest.approx(2.7125, abs=0.01)


def test_blank_frame_clears_bin_track_before_a_new_measurement():
    now = int(time.time() * 1000)
    perception = HomeTaskRgbdPerception()
    assert _bin_x(perception, _frame(stamp=now - 60, sequence=1)) is not None
    blank = _frame(rgb=[0, 0, 0], stamp=now - 40, sequence=2)
    assert _bin_x(perception, blank) is None
    moved = _bin_x(perception, _frame(tx=2.7, stamp=now - 20, sequence=3))
    assert moved == pytest.approx(2.7125, abs=0.01)


@pytest.mark.parametrize(
    ("field", "value"),
    (("source_id", "other/head"), ("transform_revision", "cal-2")),
)
def test_occluded_fragment_cannot_cross_sensor_or_calibration_boundary(field, value):
    now = int(time.time() * 1000)
    perception = HomeTaskRgbdPerception()
    assert _bin_x(perception, _frame(stamp=now - 50, sequence=1)) is not None
    changed = _fragment(_frame(stamp=now - 20, sequence=2))
    assert _bin_x(perception, replace(changed, **{field: value})) is None


def test_occluded_fragment_reuse_expires_from_last_complete_measurement():
    now = int(time.time() * 1000)
    perception = HomeTaskRgbdPerception()
    ttl = perception.BIN_TRACK_MAX_AGE_MS
    complete = _frame(stamp=now - ttl - 100, sequence=1)
    assert _bin_x(perception, complete) is not None
    expired = _fragment(_frame(stamp=complete.captured_at_unix_ms + ttl + 1, sequence=2))
    assert _bin_x(perception, expired) is None


def test_recent_occluded_rim_fragment_reuses_last_complete_sensor_pose_once():
    now = int(time.time() * 1000)
    perception = HomeTaskRgbdPerception()
    complete = _frame(stamp=now - 100, sequence=1)
    expected = _bin_x(perception, complete)
    fragment = _fragment(_frame(stamp=now - 50, sequence=2))
    assert _bin_x(perception, fragment) == pytest.approx(expected)


def test_nearby_compact_blue_object_cannot_reuse_bin_track_as_a_rim_fragment():
    now = int(time.time() * 1000)
    perception = HomeTaskRgbdPerception()
    assert _bin_x(perception, _frame(stamp=now - 100, sequence=1)) is not None
    compact = _compact_blue_object(_frame(stamp=now - 50, sequence=2))
    assert _bin_x(perception, compact) is None
