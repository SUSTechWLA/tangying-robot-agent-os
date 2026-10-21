"""Algorithm contracts; these kinematic checks are not physical validation."""
import math
from dataclasses import replace

import pytest
from tangying_robot_gateway.dynamic_avoidance import (
    AvoidanceConfig,
    MotionObservation,
    TrajectoryPoint,
    VelocityAvoider,
    continuous_closest_distance,
)


def observation(identity="a", position=(0., 0.), velocity=(0., 0.), **kwargs):
    return MotionObservation(identity=identity, position=position, velocity=velocity,
                             radius=kwargs.pop("radius", .2), observed_at=kwargs.pop("observed_at", 10.),
                             world_id="room", frame_id="world", transform_revision="alignment-1",
                             **kwargs)


def test_continuous_check_detects_collision_between_clear_endpoints():
    # Both t=0 and t=2 are 2 m apart, but the paths intersect at t=1.
    separation, at = continuous_closest_distance((2., 0.), (-2., 0.), 2.)
    assert separation == 0 and at == 1
    assert continuous_closest_distance((2., 1.), (-2., 0.), 2.) == (1., 1.)


def test_obstacle_swept_interior_rejects_desired_velocity():
    avoider = VelocityAvoider(AvoidanceConfig(horizon=4.))
    result = avoider.select(observation(), (.5, 0.),
                            obstacles=[observation("post", (1., 0.), radius=.1)], now=10.)
    assert result.velocity != (.5, 0.)
    assert result.diagnostics["desiredMinimumClearance"] < 0
    assert result.diagnostics["predictionClear"]
    assert result.diagnostics["minimumPredictedClearance"] >= 0


def test_empty_measured_neighborhood_does_not_claim_whole_world_safe():
    result = VelocityAvoider().select(observation(), (3., 4.), now=10.)
    assert math.hypot(*result.velocity) == pytest.approx(.5)
    assert result.diagnostics["speedLimited"]
    assert result.diagnostics["minimumPredictedClearance"] is None
    assert not result.diagnostics["physicalSafetyVerified"]
    assert not result.diagnostics["physicalStopVerified"]
    assert not result.diagnostics["grantsExecution"]


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("observed_at", 9., "stale_peer_observation"),
    ("observed_at", 10.1, "future_observation"),
    ("world_id", "elsewhere", "basis_mismatch"),
    ("frame_id", "map", "basis_mismatch"),
    ("transform_revision", "alignment-2", "basis_mismatch"),
    ("velocity", (math.nan, 0.), "invalid_observation"),
    ("radius", -1., "invalid_observation"),
    ("identity", "", "invalid_observation"),
])
def test_invalid_peer_stops_even_when_last_position_is_distant(field, value, reason):
    peer = replace(observation("b", (20., 0.)), **{field: value})
    result = VelocityAvoider().select(observation(), (.5, 0.), peers=[peer], now=10.)
    assert result.stop_required and result.velocity == (0., 0.) and result.reason == reason
    assert not result.diagnostics["predictionClear"]


def test_stale_self_and_stale_range_hit_stop():
    result = VelocityAvoider().select(observation(observed_at=9.), (.5, 0.), now=10.)
    assert result.reason == "stale_self_observation"
    result = VelocityAvoider().select(observation(), (.5, 0.), now=10., obstacles=[
        observation("ray-0", (3., 0.), observed_at=9.)])
    assert result.reason == "stale_obstacle_observation"


def test_peer_motion_extrapolates_from_capture_not_receipt():
    peer = observation("b", (1., 0.), (-.4, 0.), observed_at=9.8)
    result = VelocityAvoider().select(observation(), (.5, 0.), peers=[peer], now=10.)
    current = replace(peer, position=(.92, 0.), observed_at=10.)
    latest = VelocityAvoider().select(observation(), (.5, 0.), peers=[current], now=10.)
    assert result.diagnostics["desiredMinimumClearance"] < latest.diagnostics["desiredMinimumClearance"]
    assert result.diagnostics["observationAges"]["b"] == pytest.approx(.2)


def test_stop_target_is_not_physical_stop_acknowledgement():
    result = VelocityAvoider().select(observation(velocity=(.4, 0.)), (0., 0.), now=10.)
    assert result.stop_required and result.reason == "goal_stop"
    assert result.velocity == (0., 0.) and not result.diagnostics["physicalStopVerified"]


def test_no_candidate_reports_required_braking_without_certifying_its_clearance():
    result = VelocityAvoider().select(observation(), (.5, 0.), now=10.,
                                     peers=[observation("b", (.1, 0.))])
    assert result.reason == "no_collision_free_candidate" and result.stop_required
    assert not result.diagnostics["predictionClear"]


def test_braking_margin_increases_for_measured_forward_velocity():
    avoider = VelocityAvoider()
    post = observation("post", (2., 0.), radius=.1)
    slow = avoider.select(observation(), (.1, 0.), obstacles=[post], now=10.)
    fast = avoider.select(observation(velocity=(.5, 0.)), (.1, 0.), obstacles=[post], now=10.)
    assert fast.diagnostics["maximumBuffer"] > slow.diagnostics["maximumBuffer"]
    assert fast.diagnostics["minimumPredictedClearance"] < slow.diagnostics["minimumPredictedClearance"]


def test_peer_future_waypoints_do_not_replace_measured_velocity_prediction():
    peer = observation("b", (1.5, 0.), (-.5, 0.),
                       trajectory=(TrajectoryPoint(10.5, (1.5, 2.)), TrajectoryPoint(12., (1.5, 3.))))
    result = VelocityAvoider().select(observation(), (.5, 0.), peers=[peer], now=10.)
    assert result.velocity != (.5, 0.)
    assert result.diagnostics["desiredMinimumClearance"] < 0
    assert result.diagnostics["advisoryTrajectoryIds"] == ["b"]


def test_trajectory_interior_collision_is_checked_even_if_measured_peer_is_stationary():
    peer = observation("b", (1., 2.), trajectory=(
        TrajectoryPoint(11., (.5, -2.)), TrajectoryPoint(12., (1., 2.))))
    result = VelocityAvoider().select(observation(), (.5, 0.), peers=[peer], now=10.)
    assert result.diagnostics["desiredMinimumClearance"] < 0
    assert result.velocity != (.5, 0.)


def test_invalid_trajectory_and_duplicate_source_cannot_be_treated_as_clear():
    avoider = VelocityAvoider()
    peer = observation("b", (1., 0.), trajectory=(TrajectoryPoint(10., (2., 0.)),))
    assert avoider.select(observation(), (.5, 0.), peers=[peer], now=10.).reason == "invalid_observation"
    peer = observation("b", (1., 0.))
    assert avoider.select(observation(), (.5, 0.), peers=[peer, peer], now=10.).reason == "invalid_observation"
    assert avoider.select(observation(), (.5, 0.), peers=[observation()], now=10.).reason == "invalid_observation"


def test_crossing_priority_uses_stable_identity_and_is_order_independent():
    own = observation("b", (0., -1.), (0., .5))
    priority = observation("a", (-1., 0.), (.5, 0.))
    distant = observation("c", (5., 5.))
    avoider = VelocityAvoider()
    one = avoider.select(own, (0., .5), peers=[priority, distant], now=10.)
    two = avoider.select(own, (0., .5), peers=[distant, priority], now=10.)
    assert one == two
    assert one.velocity == (0., 0.) and one.reason == "yielding_right_of_way"
    assert one.diagnostics["yieldingTo"] == ["a"]


def test_symmetric_head_on_robots_choose_their_own_right():
    a = observation("a", (-1., 0.), (.5, 0.))
    b = observation("b", (1., 0.), (-.5, 0.))
    avoider = VelocityAvoider()
    assert avoider.select(a, (.5, 0.), peers=[b], now=10.).velocity[1] < 0
    assert avoider.select(b, (-.5, 0.), peers=[a], now=10.).velocity[1] > 0


@pytest.mark.parametrize("case", ["headon", "crossing"])
def test_repeated_measured_kinematic_loop_completes_without_reciprocal_deadlock(case):
    # Integration smoke for policy decisions only; MuJoCo provides separate
    # inertia/contact/controller validation. No hidden truth enters this policy.
    positions = [(-2., 0.), (2., 0.)] if case == "headon" else [(-2., 0.), (0., -2.)]
    goals = [positions[1], positions[0]] if case == "headon" else [(2., 0.), (0., 2.)]
    velocities = [(0., 0.), (0., 0.)]
    avoiders = [VelocityAvoider(), VelocityAvoider()]
    minimum_separation = math.inf
    for step in range(240):
        now = step * .1
        states = [observation(chr(97 + i), positions[i], velocities[i], observed_at=now) for i in range(2)]
        decisions = []
        for i in range(2):
            delta = tuple(goals[i][k] - positions[i][k] for k in range(2))
            distance = math.hypot(*delta)
            desired = tuple(x * min(.5, distance) / max(distance, 1e-12) for x in delta)
            decisions.append(avoiders[i].select(states[i], desired, peers=[states[1-i]], now=now))
        velocities = [decision.velocity for decision in decisions]
        positions = [tuple(positions[i][k] + .1 * velocities[i][k] for k in range(2)) for i in range(2)]
        minimum_separation = min(minimum_separation, math.dist(*positions))
        if all(math.dist(positions[i], goals[i]) < .05 for i in range(2)):
            break
    assert all(math.dist(positions[i], goals[i]) < .05 for i in range(2))
    assert minimum_separation > .46


def test_3d_sphere_sweep_allows_vertical_avoidance_and_bounds_total_speed():
    avoider = VelocityAvoider(AvoidanceConfig(dimensions=3, horizon=4.))
    own = observation(position=(0., 0., 1.), velocity=(0., 0., 0.))
    obstacle = observation("post", (1., 0., 1.), (0., 0., 0.), radius=.4)
    result = avoider.select(own, (.5, 0., 0.), obstacles=[obstacle], now=10.)
    assert result.diagnostics["predictionClear"] and result.velocity != (.5, 0., 0.)
    assert math.sqrt(sum(v*v for v in result.velocity)) <= .500000001
    assert result.diagnostics["dimensions"] == 3


@pytest.mark.parametrize("changes", [
    {"max_speed": 0}, {"max_speed": True}, {"horizon": math.inf}, {"dimensions": 4},
    {"braking_deceleration": -1}, {"angular_samples": 7}, {"max_objects": 0},
])
def test_invalid_configuration_rejected(changes):
    with pytest.raises(ValueError):
        AvoidanceConfig(**changes)


def test_clear_3d_desired_candidate_uses_equivalent_fast_path_for_dense_scan():
    avoider = VelocityAvoider(AvoidanceConfig(dimensions=3))
    own = observation(position=(0., 0., 1.), velocity=(0., 0., 0.))
    obstacles = [observation(f"scan-{i}", (4., -3. + i * .05, 1.), (0., 0., 0.), radius=0.)
                 for i in range(128)]
    result = avoider.select(own, (.5, 0., 0.), obstacles=obstacles, now=10.)
    assert result.velocity == (.5, 0., 0.)
    assert result.diagnostics["desiredCandidateFastPath"]
    assert result.diagnostics["checkedCandidates"] == 1
    assert result.diagnostics["minimumPredictedClearance"] > 0


def test_obstructed_desired_velocity_never_takes_fast_path():
    result = VelocityAvoider().select(observation(), (.5, 0.),
        obstacles=[observation("obstacle", (.8, 0.), radius=.1)], now=10.)
    assert not result.diagnostics.get("desiredCandidateFastPath")
    assert result.diagnostics["checkedCandidates"] > 1
    assert result.velocity != (.5, 0.)
