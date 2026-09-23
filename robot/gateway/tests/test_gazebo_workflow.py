"""The Gazebo binding, without Gazebo.

Everything here is a rule that decided whether a survey moved or stalled, and every
one of them was got wrong at least once while the Gazebo backend was being built.
That is the reason this file exists: none of these mistakes is visible in a log,
and all of them look like "the robot did not explore".
"""

from __future__ import annotations

import json
import math
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from tangying_robot_gateway.calibration import calibration_revision
from tangying_robot_gateway.gazebo_bridge import base_from_camera
from tangying_robot_gateway.gazebo_workflow import (
    CLEARANCE_QUERY_TOLERANCE_M,
    GAZEBO_CAMERA_MOUNTS,
    GAZEBO_FOOTPRINT_RADIUS_M,
    GAZEBO_NAV2_FOOTPRINT,
    GazeboNavigationClient,
    GazeboNavigationError,
    GazeboTravelClearance,
    GazeboWorkflowBindings,
    bounded_step_command,
    chassis_points,
    gazebo_calibration_document,
    optical_rpy,
    swept_step_is_clear,
)


def planar(x, y, yaw):
    return [x, y, 0.0, math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]


# -- calibration -------------------------------------------------------------


def test_the_declared_camera_rotation_is_the_one_the_runtime_converts_with():
    """Two places describe where the camera points: the calibration document and
    the runtime's optical conversion. If they disagree, every depth return lands at
    the wrong angle and the map looks plausible while being wrong everywhere."""
    document = gazebo_calibration_document(robot_id="gazebo-house-rgbd")
    for name, (x, y, z, tilt) in GAZEBO_CAMERA_MOUNTS.items():
        declared = document["cameras"][name]["extrinsics"]
        assert tuple(declared["xyz"]) == (x, y, z)
        angle = math.radians(tilt)
        link = np.eye(4)
        link[:3, :3] = np.array([
            [math.cos(angle), 0.0, math.sin(angle)],
            [0.0, 1.0, 0.0],
            [-math.sin(angle), 0.0, math.cos(angle)],
        ])
        link[:3, 3] = (x, y, z)
        runtime_rotation = base_from_camera(np.eye(4), link)[:3, :3]
        roll, pitch, yaw = declared["rpy"]
        cr, sr = math.cos(roll), math.sin(roll)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cy, sy = math.cos(yaw), math.sin(yaw)
        declared_rotation = np.array([
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ])
        np.testing.assert_allclose(declared_rotation, runtime_rotation, atol=1e-9)


def test_the_mapping_camera_is_aimed_at_the_floor_not_at_the_walls():
    """The defect that made every Gazebo leg unpublishable.

    The reference robot puts its mapping camera low and 15 degrees down so it can
    see the floor. This world shipped a level camera at 0.48 m, which measures
    walls: far, repetitive surfaces with no ground plane, so the depth ICP
    registered nothing - 28 frames, 0 registrations, and the map publisher
    refusing the leg for having no registrable views.
    """
    from tangying_robot_gateway.gazebo_workflow import GAZEBO_CAMERA_MOUNTS

    _x, _y, z, tilt = GAZEBO_CAMERA_MOUNTS["base-rgbd"]
    assert tilt >= 10.0, "the mapping camera must be aimed down to see the floor"
    assert z <= 0.25, "a camera at chest height measures walls, not ground"
    # The direction the camera looks, in the base frame.
    roll, pitch, yaw = optical_rpy(tilt)
    view = np.array([0.0, 0.0, 1.0])          # optical z is the view direction
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rotation = np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])
    forward = rotation @ view
    assert forward[0] > 0.9, f"the camera must look forward, got {forward}"
    assert forward[2] < -0.15, f"the camera must look downward, got {forward}"


def test_the_lens_matches_the_field_of_view_the_world_declares():
    document = gazebo_calibration_document(robot_id="gazebo-house-rgbd")
    intrinsics = document["cameras"]["base-rgbd"]["intrinsics"]
    expected = (320 / 2.0) / math.tan(1.25 / 2.0)
    np.testing.assert_allclose(intrinsics["fx"], expected, rtol=1e-12)
    np.testing.assert_allclose(intrinsics["fy"], expected, rtol=1e-12)


def test_the_revision_is_a_content_hash_not_a_timestamp():
    """A map is bound to the calibration it was surveyed with. A revision that
    changed between two identical runs would orphan every map on restart."""
    first = calibration_revision(gazebo_calibration_document(
        robot_id="gazebo-house-rgbd", updated_at_unix_ms=1))
    second = calibration_revision(gazebo_calibration_document(
        robot_id="gazebo-house-rgbd", updated_at_unix_ms=999_999))
    assert first == second


@pytest.mark.parametrize("source", ["simulation", "manual"])
def test_calibration_rpc_save_keeps_derived_identity_across_restart(tmp_path, source):
    from google.protobuf.json_format import MessageToDict, ParseDict
    from google.protobuf.struct_pb2 import Struct
    from tangying_robot_gateway.service_registry import ServiceError

    def bindings():
        return GazeboWorkflowBindings(runtime=SimpleNamespace(_samples={}), navigation=None,
                                      robot_id="gazebo-house-rgbd", root=tmp_path)

    before = bindings()
    initial = before.calibration_get()
    document = MessageToDict(ParseDict(initial["document"], Struct()))
    document["source"] = source
    saved = before.calibration_save(document, initial["revision"], "rpc roundtrip")
    assert saved["revision"] == initial["revision"] == bindings().calibration_get()["revision"]
    assert saved["document"]["source"] == "simulation"
    document["safety"]["maxLinearSpeedMPerS"] = 1.0
    with pytest.raises(ServiceError) as error:
        before.calibration_save(document, initial["revision"], "changed speeds")
    assert error.value.code == "SIMULATION_CALIBRATION_IMMUTABLE"


def test_the_nav2_proof_radius_covers_the_clearance_the_planner_asks_for():
    """The workflow plans at 0.32 m and certifies at the same number. A proof
    surface smaller than the query certifies nothing at all, silently."""
    assert GAZEBO_FOOTPRINT_RADIUS_M >= 0.32
    assert GAZEBO_FOOTPRINT_RADIUS_M == pytest.approx(
        min(abs(v) for point in GAZEBO_NAV2_FOOTPRINT for v in point))


# -- the swept-path proof ----------------------------------------------------


def test_certification_needs_the_robot_to_have_driven_there():
    clearance = GazeboTravelClearance()
    assert clearance([0.0, 0.0], 0.3) is False
    clearance.record((0.0, 0.0), (1.0, 0.0))
    assert clearance([0.5, 0.0], 0.3) is True
    assert clearance([0.5, 5.0], 0.3) is False


def test_a_wider_query_is_refused_rather_than_answered_from_a_narrower_check():
    """The caller asking for more is asking for a guarantee this evidence cannot
    give, and answering anyway is how a certificate becomes a lie."""
    clearance = GazeboTravelClearance(radius_m=0.35)
    clearance.record((0.0, 0.0), (1.0, 0.0))
    assert clearance([0.5, 0.0], 0.35) is True
    assert clearance([0.5, 0.0], 0.36) is False


def test_the_band_edges_are_inside_the_proof_and_the_middle_is_not():
    clearance = GazeboTravelClearance()
    clearance.record((0.0, 0.0), (1.0, 0.0))
    # Inside: within the query tolerance of the driven line, and the requested disc
    # still fits inside the proven radius.
    assert clearance([0.5, CLEARANCE_QUERY_TOLERANCE_M], 0.30) is True
    # Just outside the driven line: floor the robot never went over.
    assert clearance([0.5, CLEARANCE_QUERY_TOLERANCE_M + 0.05], 0.30) is False


def test_a_step_that_did_not_move_adds_no_proof():
    """The robot samples while standing still; recording those as segments would
    grow the list without bounding anything."""
    clearance = GazeboTravelClearance()
    for _ in range(50):
        clearance.record((1.0, 2.0), (1.0, 2.0))
    assert clearance._segments == []
    assert clearance([1.0, 2.0], 0.3) is False


def test_reset_starts_a_new_session_proof():
    clearance = GazeboTravelClearance()
    clearance.record((0.0, 0.0), (1.0, 0.0))
    clearance.reset()
    assert clearance([0.5, 0.0], 0.3) is False


# -- bounded steps -----------------------------------------------------------


def test_a_turn_only_goal_is_a_turn_and_not_an_arrival():
    """This is the defect that cost the first Gazebo surveys: the survey turns in
    place before every step, those goals carry the robot's current x and y, and a
    driver that checked only position reported every one as already complete. The
    robot never rotated, twelve steps moved nothing, and the leg ended on
    ``no_progress`` with 0.00 m travelled."""
    command = bounded_step_command([0.0, 0.0, 0.0], planar(0.0, 0.0, math.radians(80)))
    assert command is not None
    linear, angular = command
    assert linear == 0.0 and angular > 0.0


def test_a_turn_that_is_already_done_is_finished():
    assert bounded_step_command([0.0, 0.0, 0.0], planar(0.0, 0.0, 0.0)) is None


def test_misalignment_turns_before_it_translates():
    """A differential base that translates while misaligned traces an arc the
    survey did not plan and the guard did not check."""
    linear, angular = bounded_step_command([0.0, 0.0, 0.0], planar(0.5, 0.5, 0.0))
    assert linear == 0.0 and angular != 0.0


def test_an_aligned_step_drives_forward_and_shows_its_heading():
    linear, angular = bounded_step_command([0.0, 0.0, 0.0], planar(0.5, 0.0, 0.0))
    assert linear > 0.0 and angular == 0.0


def test_a_short_step_is_driven_at_full_speed_not_at_a_crawl():
    """The defect that capped a whole Gazebo leg at 0.43 m.

    A slowdown ramp is what a fast robot needs to avoid overshoot. At 0.05 m/s a
    control tick advances 2.5 mm against a 40 mm arrival radius, so a ramp buys
    nothing and costs everything: the cluttered spawn area produces many 5 cm
    steps, the ramp floored them at 1 cm/s, five seconds each, and the leg ended
    before three registrable keyframes existed.
    """
    from tangying_robot_gateway.gazebo_workflow import GAZEBO_STEP_LINEAR_MPS

    for distance in (0.05, 0.08, 0.12, 0.3, 0.5):
        linear, _ = bounded_step_command([0.0, 0.0, 0.0], planar(distance, 0.0, 0.0))
        assert linear == pytest.approx(GAZEBO_STEP_LINEAR_MPS), (
            f"a {distance} m step was commanded at {linear} m/s")


def test_arrival_and_heading_are_both_required():
    assert bounded_step_command([0.0, 0.0, 0.0], planar(0.01, 0.0, 0.0)) is None
    assert bounded_step_command([0.0, 0.0, 0.0], planar(0.01, 0.0, 1.0)) is not None


# -- the depth guard ---------------------------------------------------------


def test_only_chassis_height_obstacles_count():
    """A base that refuses to move because it can see the ceiling is a base that
    never moves, and one that ignores the floor drives into the furniture."""
    points = np.array([
        [0.30, 0.0, -0.9],    # far below the chassis: the floor
        [0.30, 0.0, 3.0],     # far above it: the ceiling
        [0.30, 0.0, 0.05],    # in the way
    ])
    relevant = chassis_points(points)
    assert len(relevant) == 1
    assert relevant[0][2] == pytest.approx(0.05)


def test_an_obstacle_inside_the_swept_rectangle_refuses_the_step():
    obstacle = np.array([[0.30, 0.0, 0.1]])
    assert swept_step_is_clear(obstacle, forward_m=0.2, turn_rad=0.0) is False


def test_an_obstacle_beyond_the_swept_rectangle_permits_it():
    obstacle = np.array([[0.90, 0.0, 0.1]])
    assert swept_step_is_clear(obstacle, forward_m=0.2, turn_rad=0.0) is True


def test_an_obstacle_beside_the_robot_does_not_block_a_forward_step():
    obstacle = np.array([[0.30, 0.60, 0.1]])
    assert swept_step_is_clear(obstacle, forward_m=0.2, turn_rad=0.0) is True


def test_rotating_in_place_sweeps_the_whole_corner_disc():
    """A turn moves the chassis corners through every bearing, so an obstacle the
    translation check would miss still has to stop it."""
    obstacle = np.array([[-0.30, 0.30, 0.1]])
    assert swept_step_is_clear(obstacle, forward_m=0.0, turn_rad=0.2) is False
    assert swept_step_is_clear(obstacle, forward_m=0.0, turn_rad=0.0) is True


def test_an_empty_measurement_is_not_an_obstacle():
    """Refusing on no data would strand the robot the first time a cloud was late."""
    assert swept_step_is_clear(np.zeros((0, 3)), forward_m=0.4, turn_rad=0.3) is True


# -- the navigation client ---------------------------------------------------


class _Recorder:
    """A navigation sidecar that answers from a script, and records the questions."""

    def __init__(self, statuses, *, submit=None):
        self.statuses = list(statuses)
        self.submit = submit or {"goalId": "a" * 64, "state": "PENDING"}
        self.calls: list[tuple[str, str, object]] = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path == "/v1/navigation/goals":
            if isinstance(self.submit, Exception):
                raise self.submit
            return self.submit
        if method == "GET":
            return self.statuses.pop(0) if self.statuses else {"state": "RUNNING"}
        return {"state": "CANCELLED"}


def a_client(recorder, **changes):
    client = GazeboNavigationClient("http://127.0.0.1:18790", "token", poll_interval_s=0.001)
    client._request = recorder  # type: ignore[method-assign]
    for key, value in changes.items():
        setattr(client, key, value)
    return client


def test_a_goal_that_succeeds_is_reported_as_success():
    recorder = _Recorder([{"state": "RUNNING"}, {"state": "SUCCEEDED"}])
    result = a_client(recorder).navigate(planar(1, 0, 0))
    assert result["ok"] is True


def test_a_refused_goal_keeps_the_sidecars_own_code():
    """A nav2 refusal is local news, and the survey's refusal rule is written
    against a code and a place. Flattening it to a generic failure would throw
    away the only evidence about where the robot could not go."""
    recorder = _Recorder([{"state": "FAILED", "message": "NAV2_ACTION_ENDED"}])
    result = a_client(recorder).navigate(planar(1, 0, 0))
    assert result == {"ok": False, "code": "NAV2_ACTION_ENDED", "message": "NAV2_ACTION_ENDED"}


def test_a_busy_robot_is_news_rather_than_a_transport_failure():
    recorder = _Recorder([], submit=GazeboNavigationError("NAVIGATION_BUSY", "busy"))
    result = a_client(recorder).navigate(planar(1, 0, 0))
    assert result["ok"] is False and result["code"] == "NAVIGATION_BUSY"


def test_a_cancelled_step_stops_the_robot_and_says_so():
    cancel = threading.Event()
    cancel.set()
    recorder = _Recorder([{"state": "CANCELLED"}])
    result = a_client(recorder).navigate(planar(1, 0, 0), cancel=cancel)
    assert result["ok"] is False and result["code"] == "CANCELLED"
    assert any(path.endswith("/cancel") for _, path, _ in recorder.calls)


def test_goals_are_asked_for_in_the_frame_the_planner_plans_in():
    """The survey plans on odometry. A goal in the map frame would be a goal in a
    frame the planner never saw."""
    recorder = _Recorder([{"state": "SUCCEEDED"}])
    a_client(recorder).navigate(planar(1, 0, 0))
    body = next(body for method, path, body in recorder.calls
                if method == "POST" and path == "/v1/navigation/goals")
    assert body["frameId"] == "odom"


def test_a_goal_that_is_not_seven_finite_numbers_is_refused_before_it_is_sent():
    recorder = _Recorder([])
    with pytest.raises(GazeboNavigationError):
        a_client(recorder).navigate([1.0, float("nan")])
    assert recorder.calls == []


def test_a_poll_slower_than_the_goal_lease_is_refused_at_construction():
    """The sidecar cancels any active goal whose status has not been read for two
    seconds. Polling is what keeps the robot moving, so the interval is not a
    latency knob and must not be settable as one."""
    with pytest.raises(GazeboNavigationError) as failure:
        GazeboNavigationClient("http://x", "t", poll_interval_s=2.0)
    assert failure.value.code == "POLL_INTERVAL_UNSAFE"


def test_a_client_without_a_token_is_refused():
    with pytest.raises(GazeboNavigationError):
        GazeboNavigationClient("http://x", "")


def test_an_http_refusal_becomes_the_code_the_sidecar_reported(monkeypatch):
    """The sidecar answers refusals as JSON with a code. A caller that saw only
    "HTTP 409" could not tell a busy robot from a broken link."""
    import urllib.request

    def failing(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "http://x", 409, "conflict", {},
            _Body(json.dumps({"code": "NAVIGATION_BUSY"})))

    monkeypatch.setattr(urllib.request, "urlopen", failing)
    client = GazeboNavigationClient("http://127.0.0.1:18790", "token")
    with pytest.raises(GazeboNavigationError) as failure:
        client.map_status()
    assert failure.value.code == "NAVIGATION_BUSY"


def test_an_unreachable_sidecar_is_reported_as_unavailable_rather_than_as_a_wrong_answer(monkeypatch):
    import urllib.request

    def failing(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", failing)
    client = GazeboNavigationClient("http://127.0.0.1:18790", "token")
    with pytest.raises(GazeboNavigationError) as failure:
        client.map_status()
    assert failure.value.code == "NAVIGATION_UNAVAILABLE"


class _Body:
    """The error body `HTTPError` reads, and closes."""

    def __init__(self, text: str):
        self._text = text.encode()

    def read(self) -> bytes:
        return self._text

    def close(self) -> None:
        pass


def test_guard_excludes_actual_gazebo_support_plane_but_keeps_low_obstacles():
    floor = np.array([[.30, 0., -.20], [.40, .1, -.20]])
    assert swept_step_is_clear(floor, forward_m=.2, turn_rad=.2)
    # A 4 cm rise above the supporting plane reaches the chassis bottom.
    low_obstacle = np.array([[.40, 0., -.16]])
    assert not swept_step_is_clear(low_obstacle, forward_m=.2, turn_rad=0.)


def test_camera_self_filter_does_not_remove_obstacles_in_the_planning_margin():
    from tangying_robot_gateway.gazebo_workflow import remove_chassis_returns
    points = np.array([[.20, .1, .163], [.40, .1, .163], [.40, 0., -.16]])
    np.testing.assert_array_equal(remove_chassis_returns(points), points[1:])
