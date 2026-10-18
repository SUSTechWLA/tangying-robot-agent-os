import copy
import math

import pytest

from sim.gazebo.field_scan import SensorExtrinsics, extract_obstacles, polar_points

IDENTITY = SensorExtrinsics(translation=(0, 0, 0), roll_pitch_yaw=(0, 0, 0))


def state(robot="ground-a", time=1.):
    return {"robotId": robot, "clockDomain": "simulation", "simulationTime": time,
            "position": [0., 0., .15], "quaternionWXYZ": [1., 0., 0., 0.],
            "linearVelocity": [0., 0., 0.], "angularVelocity": [0., 0., 0.],
            "worldId": "field-validation", "frameId": "world", "radius": .33}


def scan(distance=1.):
    ranges = ["Infinity"] * 9
    ranges[3] = distance
    return {"frame": "ground-a::base_link::lidar", "header": {"stamp": {"sec": "1"}},
            "count": 3, "verticalCount": 3, "angleMin": 0., "angleMax": math.pi / 2,
            "angleStep": math.pi / 4, "verticalAngleMin": -.3, "verticalAngleMax": .3,
            "verticalAngleStep": .3, "rangeMin": .05, "rangeMax": 15., "ranges": ranges}


def extract(packet=None, own=None, **kwargs):
    return extract_obstacles("ground-a", scan() if packet is None else packet,
                             state() if own is None else own, now=1.05,
                             extrinsics=IDENTITY, **kwargs)


def test_real_polar_ranges_project_through_explicit_calibration_and_measured_pose():
    own = state()
    own["position"] = [2., 3., .15]
    own["quaternionWXYZ"] = [math.sqrt(.5), 0., 0., math.sqrt(.5)]
    result = extract(own=own)
    assert result.usable and len(result.obstacles) == 1
    assert result.obstacles[0].position == pytest.approx((2, 4))
    assert result.obstacles[0].observed_at == 1.
    assert result.diagnostics["configuredExtrinsics"]


def test_oracle_world_pose_and_cad_are_not_consumed():
    original = extract()
    packet = scan()
    packet["worldPose"] = {"position": {"x": 500}, "orientation": {"w": 0}}
    packet["cad"] = "untrusted scene model"
    assert extract(packet) == original


@pytest.mark.parametrize("change", [
    lambda p: p.update(frame="ground-b::base_link::lidar"),
    lambda p: p["header"].update(stamp={"sec": "0", "nsec": 200_000_000}),
    lambda p: p["header"].update(stamp={"sec": "2"}),
    lambda p: p["ranges"].pop(),
    lambda p: p["ranges"].__setitem__(3, "NaN"),
    lambda p: p.update(angleMax=1.),
    lambda p: p.update(verticalCount=1),
    lambda p: p.update(header=None),
    lambda p: p["header"].update(stamp=[]),
])
def test_invalid_measurements_fail_closed(change):
    packet = scan()
    change(packet)
    result = extract(packet)
    assert not result.usable and result.obstacles == ()


def test_missing_base_transform_or_unsynchronized_pose_fails_closed():
    own = state()
    own["quaternionWXYZ"] = [0, 0, 0, 0]
    assert not extract(own=own).usable
    own = state(time=.8)
    assert not extract(own=own).usable


def test_only_fresh_peer_geometry_removes_duplicate_returns():
    peer = state("ground-b")
    peer["position"] = [1., 0., .15]
    current = extract(peer_states={"ground-b": peer})
    assert current.usable and current.obstacles == ()
    assert current.diagnostics["peerFiltered"] == 1
    peer["simulationTime"] = .1
    assert len(extract(peer_states={"ground-b": peer}).obstacles) == 1


def test_support_floor_and_actual_self_body_are_filtered():
    packet = scan()
    packet["ranges"][3] = "Infinity"
    packet["ranges"][0] = .5  # z=.15 - .5*sin(.3), close to support floor
    assert extract(packet).obstacles == ()
    assert extract(scan(.1)).diagnostics["selfFiltered"] == 1


def test_external_hit_inside_safety_envelope_is_not_mistaken_for_self():
    result = extract(scan(.30))
    assert result.usable and len(result.obstacles) == 1
    assert result.obstacles[0].position == pytest.approx((.3, 0))


def test_pose_scan_skew_inflates_obstacle_not_timestamp():
    own = state(time=1.04)
    own["linearVelocity"] = [.5, 0, 0]
    result = extract(own=own)
    assert result.obstacles[0].radius > extract().obstacles[0].radius
    assert result.obstacles[0].observed_at == 1.


def test_nearest_unique_points_are_bounded_with_honest_truncation():
    packet = scan()
    packet.update(count=360, angleMin=-math.pi, angleMax=math.pi, angleStep=2*math.pi/359)
    packet["ranges"] = [1.5] * (360 * 3)
    result = extract(packet, max_points=32)
    assert result.usable and len(result.obstacles) == 32
    assert result.diagnostics["truncated"]
    assert not result.diagnostics["coverageGuarantee"]


def test_vertical_major_order_is_explicit():
    packet = scan()
    packet["ranges"][3] = "Infinity"
    packet["ranges"][7] = 2.
    values, _ = polar_points(packet)
    assert values[0][0] == 7
    assert values[0][1] == pytest.approx((2*math.cos(.3)*math.cos(math.pi/4),
                                         2*math.cos(.3)*math.sin(math.pi/4), 2*math.sin(.3)))


def test_input_packet_and_state_are_unchanged():
    packet, own = scan(), state()
    before = copy.deepcopy((packet, own))
    extract(packet, own)
    assert (packet, own) == before
