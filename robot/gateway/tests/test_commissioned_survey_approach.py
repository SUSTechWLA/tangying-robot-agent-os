"""Commissioned route connection; no simulator or robot motion."""
import math
from types import SimpleNamespace

import pytest
from tangying_robot_gateway.gazebo_workflow import (
    GazeboWorkflowBindings,
    commissioned_survey_approach,
)
from tangying_robot_gateway.home_commissioning import HOME_WAYPOINTS
from tangying_robot_gateway.service_registry import ServiceError


def binding(monkeypatch, tmp_path, pose, *, custom=(), driver=None):
    import tangying_robot_gateway.gazebo_commissioning as commissioning
    monkeypatch.setattr(commissioning, "commissioning", lambda: {"cameras": {}})
    result = GazeboWorkflowBindings(runtime=SimpleNamespace(), navigation=None, robot_id="unit-1",
                                    root=tmp_path, survey_goals=custom, bounded_driver=driver)
    result.capture = lambda: SimpleNamespace(robot_state={"base_pose": list(pose)})
    return result


def test_failed_bedroom_start_rejoins_door_center_before_corridor(monkeypatch, tmp_path):
    pose = [-1.0916, 2.9324, .035, 1., 0., 0., 0.]
    obj = binding(monkeypatch, tmp_path, pose)
    route = obj.survey_goals()
    prefix = route[:-len(obj._goals)]
    assert route[-len(obj._goals):] == obj._goals, "never discard the registered survey sequence"
    assert len(obj._goals) == 10
    assert prefix[0][:2] == pytest.approx([-1.0916, 3.35])
    assert [point[:2] for point in prefix[1:]] == [[0., 3.35], [0., 3.]]
    assert route[len(prefix)][:2] == [0., 1.85]
    assert math.dist(prefix[0][:2], pose[:2]) == pytest.approx(.4176)


@pytest.mark.parametrize("room", ["bedroom", "bathroom", "kitchen", "home_corridor"])
def test_registered_station_starts_keep_original_ten_goals(monkeypatch, tmp_path, room):
    obj = binding(monkeypatch, tmp_path, HOME_WAYPOINTS[room])
    route = obj.survey_goals()
    assert route[-10:] == obj._goals
    assert all(math.isfinite(value) for pose in route for value in pose)


def test_living_room_start_keeps_existing_route_without_overshoot(monkeypatch, tmp_path):
    obj = binding(monkeypatch, tmp_path, HOME_WAYPOINTS["living_room"])
    assert obj.survey_goals() == obj._goals


def test_custom_survey_is_not_reinterpreted_by_home_route(monkeypatch, tmp_path):
    custom = [[4., 4., .035, 1., 0., 0., 0.]]
    obj = binding(monkeypatch, tmp_path, HOME_WAYPOINTS["bedroom"], custom=custom)
    assert obj.survey_goals() == custom


def test_unregistered_and_ambiguous_positions_fail_without_motor_calls(monkeypatch, tmp_path):
    commands = []
    obj = binding(monkeypatch, tmp_path, [50., 50., .035, 1., 0., 0., 0.],
                  driver=lambda *args: commands.append(args))
    with pytest.raises(ServiceError) as error:
        obj.survey_goals()
    assert error.value.code == "SURVEY_START_UNREGISTERED" and not commands
    square = [[0., 0., 0., 1., 0., 0., 0.], [0., 2., 0., 1., 0., 0., 0.],
              [2., 2., 0., 1., 0., 0., 0.], [2., 0., 0., 1., 0., 0., 0.]]
    with pytest.raises(ServiceError) as error:
        commissioned_survey_approach([1., 1., 0., 1., 0., 0., 0.], square, max_offset_m=2.)
    assert error.value.code == "SURVEY_START_AMBIGUOUS"


def test_bad_geometry_never_plans_or_issues_movement():
    route = [[0., 0., 0., 1., 0., 0., 0.], [0., 1., 0., 1., 0., 0., 0.]]
    with pytest.raises(ServiceError) as error:
        commissioned_survey_approach([float("nan"), 0., 0., 1., 0., 0., 0.], route)
    assert error.value.code == "SURVEY_START_INVALID"
    route[1][0] = float("inf")
    with pytest.raises(ServiceError) as error:
        commissioned_survey_approach([0., 0., 0., 1., 0., 0., 0.], route)
    assert error.value.code == "SURVEY_ROUTE_INVALID"


def test_connector_remains_subject_to_original_driver_guard(monkeypatch, tmp_path):
    calls = []
    def refuse(goal, _cancel):
        calls.append(goal)
        return {"ok": False, "code": "NAV_ENVELOPE"}
    pose = [-1.0916, 2.9324, .035, 1., 0., 0., 0.]
    obj = binding(monkeypatch, tmp_path, pose, driver=refuse)
    connector = obj.survey_goals()[0]
    outcome = obj.move(connector, None, bounded=False)
    assert outcome["code"] == "NAV_ENVELOPE" and len(calls) == 1
    assert calls[0][:2] == pytest.approx(connector[:2])
