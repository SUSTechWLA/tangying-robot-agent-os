"""The survey loop, driven without a workflow, a robot or a session.

Until this seam existed the loop was a method on a 1,300-line class, so the only
way to ask "what does the survey do when the driver refuses a step" was to build a
workflow and a fake driver inside it. These tests supply three small ports and
nothing else, which is the property the extraction was for.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest
from tangying_robot_gateway.exploration import (
    OCCUPIED,
    Grid,
    explore_target,
    next_waypoint,
    plan_route,
    traversable_for,
)
from tangying_robot_gateway.survey import (
    EXPLORATION,
    LegResult,
    SurveyRefusal,
    SurveyRunner,
)

#: A room with unmapped space on the right and a wall down the middle.
ROOM = [
    "..........??????????",
    "..........??????????",
    "..........??????????",
    "....................",
    "....######..........",
    "....................",
]


def open_room_grid() -> Grid:
    """A 16 m x 10 m open room with an unmapped pocket at the far end.

    Sized in real survey units - 0.2 m cells, metres of clearance - because the
    refusal machinery is spatial: on a one-metre toy grid the robot's first step
    lands in the only corridor cell there is and blacklisting it severs the map,
    which measures the fixture instead of the loop.
    """
    cells = np.zeros((50, 80), dtype=np.int16)
    cells[0, :] = OCCUPIED
    cells[-1, :] = OCCUPIED
    cells[:, 0] = OCCUPIED
    cells[:, -1] = OCCUPIED
    cells[10:40, 66:78] = -1
    return Grid(cells=cells, resolution=0.2, origin=(0.0, 0.0))


def room_grid() -> Grid:
    values = {".": 0, "#": OCCUPIED, "?": -1}
    cells = np.array([[values[c] for c in row] for row in reversed(ROOM)], dtype=np.int16)
    return Grid(cells=cells, resolution=1.0, origin=(0.0, 0.0))


class FakeMap:
    """A map that never changes: enough to exercise the loop's decisions."""

    def __init__(self, *, frames: int = 0, travelled: float = 0.0,
                 pose: tuple[float, float, float] = (0.5, 0.5, 0.0), grid=None):
        self.grid = grid if grid is not None else room_grid()
        self.frames = frames
        self.travelled = travelled
        self.cancelled = False
        # The pose has to move when the robot drives. A fake that reported steps
        # without changing where the robot was made every target look unreached,
        # so the loop retired viewpoints it had in fact arrived at and eventually
        # reported no reachable frontier - a property of the fake, not the loop.
        self.pose = pose
        #: Where the loop is currently trying to go, so a fake driver can refuse an
        #: approach near the goal rather than only the goal itself.

    def live_grid(self):
        return self.grid, np.zeros(self.grid.cells.shape, dtype=bool)

    def current_pose(self):
        return (self.pose[0], self.pose[1], 0.035, 1.0, 0.0, 0.0, 0.0)

    def planning_pose(self):
        return self.pose

    def planning_clearance(self):
        return 0.0

    def self_mask(self, grid, x, y, clearance):
        return np.zeros(grid.cells.shape, dtype=bool)

    def travelled_m(self):
        return self.travelled

    def frame_count(self):
        return self.frames

    def max_frames(self):
        return 400

    def depth_starved(self):
        return 0

    def check_cancel(self):
        if self.cancelled:
            raise RuntimeError("cancelled")


class FakeDriver:
    """Counts the steps it is asked for, and optionally advances the map.

    A driver that moves must advance the map's odometer, because the loop measures
    a leg by the distance travelled: a fake that reported movement without moving
    the odometer would make every budget test meaningless.

    ``refuse`` names one waypoint to refuse.

    ``refuse_short`` refuses any step that is not the goal itself, which is what a
    safety layer guarding a blocked approach does: it stops the robot *short* of
    the destination, and the errand is untouched by that refusal. It is the case
    that distinguishes blacklisting the refused step from blacklisting the goal -
    refusing the goal itself is a different, honest outcome.
    """

    def __init__(self, *, refuse: tuple[float, float] | None = None, move: bool = True,
                 map_port=None, metres: float = EXPLORATION["maxStepM"],
                 refuse_short: bool = False):
        self.steps: list[tuple] = []
        self.looks = 0
        self.refuse = refuse
        self.refuse_short = refuse_short
        self.move = move
        self.map_port = map_port
        self.metres = metres
        self.goal: tuple[float, float] | None = None

    def drive_step(self, waypoint, base, pose, *, after_pose=None, remaining_m=None):
        self.steps.append(tuple(waypoint))
        point = (float(waypoint[0]), float(waypoint[1]))
        if self.refuse is not None and point == self.refuse:
            raise SurveyRefusal("NAV_ENVELOPE", at=point)
        if self.refuse_short and self.goal is not None:
            short = math.hypot(point[0] - self.goal[0], point[1] - self.goal[1]) > 1e-9
            if short:
                raise SurveyRefusal("NAV_ENVELOPE", at=point)
        if self.move and self.map_port is not None:
            x, y, _yaw = self.map_port.pose
            distance = math.hypot(point[0]-x, point[1]-y)
            moved = min(distance, self.metres, max(0., remaining_m-.10))
            if distance:
                self.map_port.pose = (x+(point[0]-x)*moved/distance,
                                      y+(point[1]-y)*moved/distance, 0.0)
            self.map_port.travelled += moved
        return self.move

    def look_around(self, grid, blind=None):
        self.looks += 1


class FakeReport:
    def __init__(self):
        self.reports: list[dict] = []
        self.messages: list[str] = []

    def publish_exploration(self, report):
        self.reports.append(report)

    def publish_message(self, text):
        self.messages.append(text)


class AdvancingClock:
    """A clock that moves forward a fixed amount each time it is read.

    A leg's deadline is computed from the clock and compared against the clock, so
    a *fixed* clock can never time out: both readings are the same instant. Time
    has to pass between the two reads for "the leg ran out of time" to mean
    anything.
    """

    def __init__(self, *, start: float = 0.0, step: float = 0.0):
        self.now = start
        self.step = step

    def __call__(self) -> float:
        current = self.now
        self.now += self.step
        return current


def run(map_port=None, driver=None, report=None, *, now: float = 0.0, step: float = 0.0,
        remaining_m: float = 75.0, number: int = 1,
        ) -> tuple[LegResult, FakeMap, FakeDriver, FakeReport]:
    map_port = map_port if map_port is not None else FakeMap()
    driver = driver if driver is not None else FakeDriver()
    report = report if report is not None else FakeReport()
    runner = SurveyRunner(clock=AdvancingClock(start=now, step=step))
    result = runner.run_leg(map_port, driver, report, remaining_m=remaining_m, number=number)
    return result, map_port, driver, report


def test_the_loop_drives_towards_the_unmapped_side_without_a_workflow():
    map_port = FakeMap()
    result, _map_port, driver, report = run(map_port=map_port,
                                            driver=FakeDriver(map_port=map_port))
    assert driver.steps, "the survey should have asked the driver to move"
    assert driver.looks >= 1, "it should have looked around before driving"
    assert report.messages, "an operator should be told what is happening"
    assert all("当前观测栅格已知比例" in message and "非全场景覆盖率" in message
               for message in report.messages)
    # Which budget ends it depends on how the fake's map evolves; what matters is
    # that it drove, looked, said something, and named a real reason.
    assert result.exploration["stopReason"] in {
        "no_progress", "travel_budget", "frame_budget", "no_reachable_frontier"}
    assert result.travelled_m > 0.0, "the leg reports the distance it drove"


def test_a_refused_step_is_routed_around_rather_than_ending_the_survey():
    """The refusal is local evidence about one approach. Ending the survey on it
    would abandon a house because one corner is blocked."""
    map_port = FakeMap()
    driver = FakeDriver()
    first = SurveyRunner().run_leg(map_port, driver, FakeReport(), remaining_m=75.0, number=1)
    refused_waypoint = driver.steps[0]
    driver2 = FakeDriver(refuse=refused_waypoint)
    result = SurveyRunner().run_leg(FakeMap(), driver2, FakeReport(), remaining_m=75.0, number=1)
    assert len(driver2.steps) >= 1, "the refusal is recorded"
    assert result.exploration["refused"] >= 1, "the refusal is counted in the report"
    assert first.exploration["stopReason"] is not None


def test_blacklisting_a_refused_step_leaves_another_approach_open():
    """The defect this pins: a refused *approach* was recorded against the goal.

    A safety layer stops the robot at the obstacle, so the refused coordinate is
    some way short of the destination. Recorded against the destination, that
    retired a target that was very likely reachable another way - and because the
    avoid list is what the planner routes around, the next plan re-derived the same
    refused approach and was refused again, until the leg gave up reporting
    ``no_reachable_frontier`` with the house largely unmapped.

    Blacklisting the refused *step* turns each refusal into one approach the planner
    will not repeat, so the survey keeps working the same region.
    """
    grid = open_room_grid()
    avoid: list[tuple[float, float]] = []
    tried: list[tuple[float, float]] = []
    for _ in range(2):
        target = explore_target(grid, robot_xy=(1.0, 5.0), sensor_radius_m=3.0,
                                radius_m=0.0, min_frontier_area_m2=0.0, avoid_xy=avoid)
        assert target is not None, "the region is still reachable at every reroute"
        path, _length = plan_route(grid, robot_xy=(1.0, 5.0), goal_xy=target.viewpoint,
                                   radius_m=0.0, avoid_xy=avoid)
        assert path is not None
        traversable = traversable_for(grid, 0.0, avoid_xy=avoid)
        waypoint = next_waypoint(grid, path, lookahead_m=0.6, traversable=traversable)
        tried.append((round(waypoint[0], 2), round(waypoint[1], 2)))
        avoid.append(tuple(waypoint))

    assert len(set(tried)) == len(tried), (
        f"each refusal must buy a different approach, not the same one again: {tried}")
    assert avoid[0] != avoid[1]


def test_a_refusal_rule_reads_the_place_not_the_errand():
    """The refusal rule, tested as a rule.

    Every branch here was a real failure. A refusal is evidence about one place: a
    safety layer stops the robot at the obstacle, some way short of the goal, and a
    rule that records it against the goal both writes off a reachable region and
    lets the planner re-derive the same refused approach forever.
    """
    runner = SurveyRunner()
    bound = EXPLORATION["maxRefusedSites"]

    # A fresh approach: the target is kept and the place is remembered.
    refused: list[tuple[float, float]] = []
    decision = runner.refusal_decision(SurveyRefusal("NAV", at=(2.0, 1.0)),
                                       target_xy=(9.0, 5.0), refused=refused,
                                       consecutive=0)
    assert refused == [(2.0, 1.0)], "the refused step is what gets blacklisted"
    assert decision["retire_target"] is False
    assert decision["give_up"] is False
    assert (9.0, 5.0) not in refused, "the errand must not be written off"

    # The same approach again: the planner has nowhere else to go with this target.
    decision = runner.refusal_decision(SurveyRefusal("NAV", at=(2.0, 1.0)),
                                       target_xy=(9.0, 5.0), refused=refused,
                                       consecutive=decision["consecutive"])
    assert decision["retire_target"] is True, "a repeat refusal retires the target"
    assert decision["consecutive"] == 1

    # A different approach: the target gets its chance again, and the counter resets.
    decision = runner.refusal_decision(SurveyRefusal("NAV", at=(3.0, 1.0)),
                                       target_xy=(9.0, 5.0), refused=refused,
                                       consecutive=decision["consecutive"])
    assert decision["retire_target"] is False
    assert decision["consecutive"] == 0, "a new approach is not a repeat failure"

    # A driver that cannot say where: fall back to the target, which is the old
    # behaviour and the honest reading when there is no better evidence.
    refused = []
    runner.refusal_decision(SurveyRefusal("NAV"), target_xy=(9.0, 5.0),
                            refused=refused, consecutive=0)
    assert refused == [(9.0, 5.0)]

    # Past the bound the list is cleared: by then it describes places, not approaches.
    refused = [(float(index), 0.0) for index in range(bound - 1)]
    decision = runner.refusal_decision(SurveyRefusal("NAV", at=(99.0, 99.0)),
                                       target_xy=(9.0, 5.0), refused=refused,
                                       consecutive=0)
    assert refused == [], "past the bound the avoid list is emptied"
    assert "重新评估" in decision["message"]
    assert decision["give_up"] is False, "clearing is the alternative to giving up"

    # And the storm guard still ends the leg when one approach keeps coming back.
    # The counter tracks *repeats*, not refusals, which is why a survey can be
    # refused a dozen times in a corridor and still not give up: every one of those
    # was a new place, and a new place is not a failure.
    refused = []
    consecutive = 0
    attempts = EXPLORATION["maxConsecutiveRefusals"] + 1
    for _ in range(attempts):
        decision = runner.refusal_decision(SurveyRefusal("NAV", at=(1.0, 1.0)),
                                           target_xy=(9.0, 5.0), refused=refused,
                                           consecutive=consecutive)
        consecutive = decision["consecutive"]
    assert decision["give_up"] is True, (
        f"the same approach refused {attempts} times must end the leg")


def test_a_leg_that_can_never_move_stops_instead_of_spinning():
    """A robot that never moves must stop and say so, not retry forever.

    The reason can be either truthful one: the loop retires each viewpoint it
    cannot leave, and once enough are retired there is genuinely no reachable
    frontier left - so a stationary robot reports that, or "no progress" if the
    idle counter trips first. What must not happen is an unbounded retry loop.
    """
    map_port = FakeMap()
    driver = FakeDriver(move=False, map_port=map_port)
    result = SurveyRunner().run_leg(map_port, driver, FakeReport(), remaining_m=75.0, number=1)
    assert result.exploration["stopReason"] in {"no_progress", "no_reachable_frontier"}
    assert len(driver.steps) <= EXPLORATION["maxIdleSteps"] + 1
    assert result.travelled_m == pytest.approx(0.0), "it never moved"


def test_the_frame_budget_stops_a_leg_before_it_cannot_be_saved():
    """Stopping early is the point: the leg still fits the budget, so it can be
    published and continued instead of being lost at the cap."""
    map_port = FakeMap(frames=400 - EXPLORATION["frameMargin"])
    result, _map_port, driver, _report = run(map_port=map_port)
    assert result.exploration["stopReason"] == "frame_budget"
    assert not driver.steps, "it should stop before asking the driver to move"
    assert driver.looks == 0, "a look-around is motion and needs frame budget too"
    assert result.exploration["initialScanAttempted"] is False


def test_the_travel_budget_stops_before_the_requested_limit():
    """The budget is measured as distance driven *during this leg*, so the fake has
    to advance the odometer as it drives, not start with a large reading."""
    map_port = FakeMap()
    driver = FakeDriver(map_port=map_port, metres=2.0)
    result = SurveyRunner().run_leg(map_port, driver, FakeReport(), remaining_m=5.0, number=1)
    assert result.exploration["stopReason"] == "travel_budget"
    assert 4.8 <= result.travelled_m <= 5.0


def test_a_leg_whose_time_is_up_stops_even_with_budget_left():
    # One read to set the deadline, the next by the loop: a step equal to the leg
    # budget puts the second read past it.
    result, _map_port, driver, _report = run(step=EXPLORATION["legSeconds"] * 2)
    assert result.exploration["stopReason"] == "leg_timeout"
    assert not driver.steps, "a leg past its deadline must not ask the driver to move"
    assert driver.looks == 0


def test_cancellation_propagates_out_of_the_loop():
    map_port = FakeMap()
    map_port.cancelled = True
    driver = FakeDriver()
    with pytest.raises(RuntimeError, match="cancelled"):
        SurveyRunner().run_leg(map_port, driver, FakeReport(), remaining_m=75.0, number=1)
    assert driver.looks == 0
    assert driver.steps == []


def test_a_finished_map_is_reported_complete_and_not_merely_stopped():
    """`complete` has to mean the map is finished. Unknown space the planner could
    not reach is a different, reportable outcome."""
    class NoFrontier(FakeMap):
        def live_grid(self):
            grid = self.grid
            settled = Grid(cells=np.where(grid.cells < 0, 0, grid.cells).astype(np.int16),
                           resolution=grid.resolution, origin=grid.origin)
            return settled, np.zeros(settled.cells.shape, dtype=bool)

    result = SurveyRunner().run_leg(NoFrontier(), FakeDriver(), FakeReport(),
                                    remaining_m=75.0, number=1)
    assert result.exploration["stopReason"] == "complete"
    assert result.exploration["terminationMeaning"] == "frontier_exhausted"
    assert result.exploration["coverageScope"] == "observed_grid_nonblind_frontiers"
    assert result.complete is True


def test_a_map_with_unreachable_unknown_is_not_called_complete():
    """The lie this distinction exists to prevent: ending a survey and saying the
    house is finished when the planner merely ran out of reachable ideas."""
    class Blocked(FakeMap):
        def planning_clearance(self):
            return 50.0  # nothing is plannable at this envelope

    result = SurveyRunner().run_leg(Blocked(), FakeDriver(), FakeReport(),
                                    remaining_m=75.0, number=1)
    assert result.complete is False
    assert result.exploration["stopReason"] != "complete"
    assert result.exploration["frontierCells"] > 0, "the report says how much is left"


@pytest.mark.parametrize("remaining_m", [0.0, 0.1, 0.15])
def test_exhausted_travel_budget_never_authorizes_an_initial_sweep(remaining_m):
    result, _, driver, _ = run(remaining_m=remaining_m)
    assert result.exploration["stopReason"] == "travel_budget"
    assert not result.exploration["initialScanAttempted"]
    assert driver.looks == 0 and driver.steps == []


def test_a_leg_stops_at_the_exact_deadline_before_scanning():
    result, _, driver, _ = run(step=EXPLORATION["legSeconds"])
    assert result.exploration["stopReason"] == "leg_timeout"
    assert driver.looks == 0 and driver.steps == []


@pytest.mark.parametrize("cell", [-1, OCCUPIED])
def test_a_grid_without_observed_free_space_is_not_a_finished_survey(cell):
    grid = Grid(cells=np.full((8, 8), cell, dtype=np.int16), resolution=.2,
                origin=(0.0, 0.0))
    result, _, driver, _ = run(map_port=FakeMap(grid=grid))
    assert result.exploration["stopReason"] == "insufficient_map_evidence"
    assert result.exploration["free"] == 0
    assert result.exploration["frontierCells"] == 0
    assert result.complete is False
    assert driver.looks == 1 and driver.steps == []


def test_continuation_legs_do_not_repeat_the_initial_sweep():
    grid = Grid(cells=np.zeros((8, 8), dtype=np.int16), resolution=.2,
                origin=(0.0, 0.0))
    result, _, driver, _ = run(map_port=FakeMap(grid=grid), number=2)
    assert result.complete is True
    assert result.exploration["initialScanStatus"] == "skipped_continuation"
    assert result.exploration["initialScanAttempted"] is False
    assert driver.looks == 0


def test_a_driver_can_measure_the_first_grid_without_a_premapped_scene():
    class InitiallyEmpty(FakeMap):
        measured = False

        def live_grid(self):
            if not self.measured:
                return None, None
            return super().live_grid()

    class BootstrapDriver(FakeDriver):
        def look_around(self, grid, blind=None):
            super().look_around(grid, blind)
            assert grid is None
            self.map_port.measured = True

    map_port = InitiallyEmpty(grid=Grid(cells=np.zeros((8, 8), dtype=np.int16),
                                       resolution=.2, origin=(0.0, 0.0)))
    driver = BootstrapDriver(map_port=map_port)
    result, _, _, _ = run(map_port=map_port, driver=driver)
    assert result.complete
    assert result.exploration["initialScanStatus"] == "returned"
    assert result.exploration["initialScanAttempted"]
    assert driver.looks == 1 and driver.steps == []


def test_no_measured_grid_after_the_sweep_remains_an_incomplete_result():
    class NoGrid(FakeMap):
        def live_grid(self):
            return None, None

    result, _, driver, _ = run(map_port=NoGrid())
    assert not result.complete
    assert result.exploration["stopReason"] == "no_frames"
    assert driver.looks == 1 and driver.steps == []


def test_a_refused_initial_sweep_is_not_retried_as_a_frontier_approach():
    class DeclinesSweep(FakeDriver):
        def look_around(self, grid, blind=None):
            self.looks += 1
            raise SurveyRefusal("TURN_UNSUPPORTED")

    result, _, driver, _ = run(driver=DeclinesSweep())
    assert not result.complete
    assert result.exploration["stopReason"] == "initial_scan_refused"
    assert result.exploration["initialScanStatus"] == "refused"
    assert result.exploration["initialScanRefusal"] == "TURN_UNSUPPORTED"
    assert driver.looks == 1 and driver.steps == []


def test_the_sweep_must_leave_budget_for_any_subsequent_motion():
    map_port = FakeMap()

    class ConsumesFrames(FakeDriver):
        def look_around(self, grid, blind=None):
            super().look_around(grid, blind)
            self.map_port.frames = 400 - EXPLORATION["frameMargin"]

    result, _, driver, _ = run(map_port=map_port, driver=ConsumesFrames(map_port=map_port))
    assert result.exploration["stopReason"] == "frame_budget"
    assert driver.looks == 1 and driver.steps == []


def test_a_sweep_that_observes_cancellation_cannot_be_followed_by_a_drive():
    map_port = FakeMap()

    class CancelsDuringSweep(FakeDriver):
        def look_around(self, grid, blind=None):
            super().look_around(grid, blind)
            self.map_port.cancelled = True

    driver = CancelsDuringSweep(map_port=map_port)
    with pytest.raises(RuntimeError, match="cancelled"):
        run(map_port=map_port, driver=driver)
    assert driver.looks == 1 and driver.steps == []


@pytest.mark.parametrize("ending", ["frame_budget", "cancel"])
def test_reaching_a_frontier_does_not_authorize_a_sweep_after_budget_or_cancel(
        monkeypatch, ending):
    from tangying_robot_gateway import survey

    monkeypatch.setattr(survey, "explore_target", lambda *_args, **_kwargs:
                        SimpleNamespace(viewpoint=(1.5, .5), path=[(.5, .5), (1.5, .5)]))
    map_port = FakeMap()

    class ConsumesLastStep(FakeDriver):
        def drive_step(self, *args, **kwargs):
            acted = super().drive_step(*args, **kwargs)
            if ending == "cancel":
                self.map_port.cancelled = True
            else:
                self.map_port.frames = 400 - EXPLORATION["frameMargin"]
            return acted

    driver = ConsumesLastStep(map_port=map_port)
    runner = SurveyRunner(config={**EXPLORATION, "lookSpacingM": .5})
    if ending == "cancel":
        with pytest.raises(RuntimeError, match="cancelled"):
            runner.run_leg(map_port, driver, FakeReport(), remaining_m=75.0, number=1)
    else:
        result = runner.run_leg(map_port, driver, FakeReport(), remaining_m=75.0, number=1)
        assert result.exploration["stopReason"] == "frame_budget"
    assert len(driver.steps) == 1 and driver.looks == 1


def test_repeated_lost_routes_without_translation_stop_with_a_bounded_attempt_count(monkeypatch):
    from tangying_robot_gateway import survey

    map_port = FakeMap(grid=open_room_grid(), pose=(1., 5., 0.))
    route_calls = []
    monkeypatch.setattr(survey, "explore_target", lambda *_args, **_kwargs:
                        SimpleNamespace(viewpoint=(14., 5.), path=[(1., 5.), (1.5, 5.)]))

    def lost_route(*_args, **_kwargs):
        route_calls.append(True)
        return None, 0.0

    monkeypatch.setattr(survey, "plan_route", lost_route)

    class HeadingOnly(FakeDriver):
        def drive_step(self, waypoint, *_args, **_kwargs):
            self.steps.append(tuple(waypoint))
            x, y, yaw = self.map_port.pose
            self.map_port.pose = (x, y, yaw + .1)
            return True

    driver = HeadingOnly(map_port=map_port)
    config = {**EXPLORATION, "maxIdleSteps": 3}
    result = SurveyRunner(config=config).run_leg(map_port, driver, FakeReport(),
                                               remaining_m=75.0, number=1)
    assert not result.complete
    assert result.exploration["stopReason"] == "no_progress"
    assert result.exploration["routeFailuresSinceProgress"] == 3
    assert len(route_calls) == len(driver.steps) == 3
    assert result.travelled_m == 0.0


def test_a_transient_lost_route_can_recover_and_translation_resets_its_counter(monkeypatch):
    from tangying_robot_gateway import survey

    map_port = FakeMap(grid=open_room_grid(), pose=(1., 5., 0.))
    # A viewpoint is on measured free floor, before the unknown pocket starts
    # at x=13.2 m. The strict waypoint selector must not cross that unknown space.
    goal = (12.9, 5.)
    route_calls = []

    def choose(*_args, **_kwargs):
        if map_port.travelled:
            return None
        return SimpleNamespace(viewpoint=goal, path=[map_port.pose[:2], (1.5, 5.)])

    def route(*_args, **_kwargs):
        route_calls.append(True)
        return (None, 0.) if len(route_calls) < 3 else (
            [map_port.pose[:2], goal], math.hypot(goal[0]-map_port.pose[0], goal[1]-map_port.pose[1]))

    monkeypatch.setattr(survey, "explore_target", choose)
    monkeypatch.setattr(survey, "plan_route", route)

    class TurnsThenTranslates(FakeDriver):
        def drive_step(self, waypoint, *_args, **_kwargs):
            self.steps.append(tuple(waypoint))
            x, y, yaw = self.map_port.pose
            if len(route_calls) < 3:
                self.map_port.pose = (x, y, yaw + .1)
            else:
                self.map_port.travelled += math.hypot(waypoint[0] - x, waypoint[1] - y)
                self.map_port.pose = (*waypoint, 0.)
            return True

    driver = TurnsThenTranslates(map_port=map_port)
    result = SurveyRunner(config={**EXPLORATION, "maxIdleSteps": 3}).run_leg(
        map_port, driver, FakeReport(), remaining_m=75., number=1)
    assert result.exploration["stopReason"] == "no_reachable_frontier"
    assert result.exploration["routeFailuresSinceProgress"] == 0
    assert goal[0]-1.-EXPLORATION["arrivalM"] <= result.travelled_m <= goal[0]-1.
    assert math.hypot(map_port.pose[0]-goal[0], map_port.pose[1]-goal[1]) <= EXPLORATION["arrivalM"]


def test_initial_sweep_counts_towards_the_leg_look_budget():
    map_port = FakeMap()
    driver = FakeDriver(map_port=map_port)
    SurveyRunner(config={**EXPLORATION, "maxLooksPerLeg": 1}).run_leg(
        map_port, driver, FakeReport(), remaining_m=75., number=1)
    assert driver.looks == 1


def test_a_zero_look_budget_leaves_scanning_to_the_supported_motion_driver():
    map_port = FakeMap()
    driver = FakeDriver(move=False, map_port=map_port)
    result = SurveyRunner(config={**EXPLORATION, "maxLooksPerLeg": 0}).run_leg(
        map_port, driver, FakeReport(), remaining_m=75., number=1)
    assert driver.looks == 0
    assert not result.exploration["initialScanAttempted"]
    assert result.exploration["initialScanStatus"] == "disabled"


def test_a_viewpoint_without_progress_is_not_turned_into_a_geometric_obstacle(monkeypatch):
    from tangying_robot_gateway import survey

    selections = []

    def choose(*_args, **kwargs):
        selections.append((list(kwargs["avoid_xy"]), list(kwargs["exclude_viewpoints"])))
        return SimpleNamespace(viewpoint=(1.5, .5), path=[(.5, .5), (1.5, .5)])

    monkeypatch.setattr(survey, "explore_target", choose)
    result, _, driver, _ = run(driver=FakeDriver(move=False))
    assert result.exploration["stopReason"] == "no_progress"
    assert result.exploration["refused"] == 0
    assert result.exploration["visitedViewpoints"] == 1
    assert all(avoid == [] for avoid, _ in selections)
    assert selections[0][1] == []
    assert selections[1][1] == [(1.5, .5)]
    assert driver.steps


def test_actual_motion_refusals_remain_geometric_avoidance_evidence(monkeypatch):
    from tangying_robot_gateway import survey

    selections = []

    def choose(*_args, **kwargs):
        selections.append((list(kwargs["avoid_xy"]), list(kwargs["exclude_viewpoints"])))
        if kwargs["avoid_xy"]:
            return None
        return SimpleNamespace(viewpoint=(1.5, .5), path=[(.5, .5), (1.5, .5)])

    monkeypatch.setattr(survey, "explore_target", choose)
    _, _, probe, _ = run(driver=FakeDriver(move=False))
    refused_waypoint = probe.steps[0]
    selections.clear()
    monkeypatch.setattr(survey, "plan_route", lambda *_args, **_kwargs: (None, 0.))
    result, _, _, _ = run(driver=FakeDriver(refuse=refused_waypoint))
    assert result.exploration["stopReason"] == "no_reachable_frontier"
    assert result.exploration["refused"] > 0
    assert result.exploration["visitedViewpoints"] == 0
    assert selections[0][0] == []
    assert selections[1][0] == [refused_waypoint]
    assert all(excluded == [] for _, excluded in selections)


@pytest.mark.parametrize("starved_before_scan", [True, False])
def test_depth_loss_limit_before_or_during_bootstrap_prevents_an_extra_drive(starved_before_scan):
    class DepthMap(FakeMap):
        rejected = EXPLORATION["depthStarvedLimit"] if starved_before_scan else 0

        def depth_starved(self):
            return self.rejected

    class ExhaustsDepthDuringScan(FakeDriver):
        def look_around(self, grid, blind=None):
            super().look_around(grid, blind)
            self.map_port.rejected = EXPLORATION["depthStarvedLimit"]

    map_port = DepthMap()
    driver = ExhaustsDepthDuringScan(map_port=map_port)
    result, _, _, _ = run(map_port=map_port, driver=driver)
    assert result.exploration["stopReason"] == "depth_starved"
    assert not result.complete
    assert driver.steps == []
    assert driver.looks == (0 if starved_before_scan else 1)
    assert result.exploration["initialScanAttempted"] is (not starved_before_scan)


def test_depth_loss_below_the_limit_still_allows_a_new_view_and_frontier_attempt():
    class RecoverableDepthMap(FakeMap):
        def depth_starved(self):
            return EXPLORATION["depthStarvedLimit"] - 1

    result, _, driver, _ = run(map_port=RecoverableDepthMap(), driver=FakeDriver(move=False))
    assert result.exploration["stopReason"] != "depth_starved"
    assert driver.looks == 1
    assert driver.steps


def test_continuation_does_not_confuse_map_anchor_offset_with_actual_progress(monkeypatch):
    from tangying_robot_gateway import survey

    class OffsetWorldMap(FakeMap):
        def current_pose(self):
            return (self.pose[0]-10., self.pose[1]-20., .035, 1., 0., 0., 0.)

    map_port = OffsetWorldMap(grid=open_room_grid(), pose=(1., 5., 0.))
    monkeypatch.setattr(survey, "explore_target", lambda *_args, **_kwargs:
                        SimpleNamespace(viewpoint=(12.9, 5.), path=[(1., 5.), (1.5, 5.)]))

    class AcknowledgesWithoutMovement(FakeDriver):
        def drive_step(self, waypoint, *_args, **_kwargs):
            self.steps.append(tuple(waypoint))
            return True

    driver = AcknowledgesWithoutMovement(map_port=map_port)
    result = SurveyRunner(config={**EXPLORATION, "maxIdleSteps": 3, "legSeconds": 50},
                          clock=AdvancingClock(step=1.)).run_leg(
        map_port, driver, FakeReport(), remaining_m=75., number=2)
    assert result.exploration["stopReason"] == "no_progress"
    assert len(driver.steps) == 3
    assert result.travelled_m == 0.
