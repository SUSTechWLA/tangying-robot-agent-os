"""The survey loop, driven without a workflow, a robot or a session.

Until this seam existed the loop was a method on a 1,300-line class, so the only
way to ask "what does the survey do when the driver refuses a step" was to build a
workflow and a fake driver inside it. These tests supply three small ports and
nothing else, which is the property the extraction was for.
"""

from __future__ import annotations

import math

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
                 map_port=None, metres: float = 1.0, refuse_short: bool = False):
        self.steps: list[tuple] = []
        self.looks = 0
        self.refuse = refuse
        self.refuse_short = refuse_short
        self.move = move
        self.map_port = map_port
        self.metres = metres
        self.goal: tuple[float, float] | None = None

    def drive_step(self, waypoint, base, pose, *, after_pose=None):
        self.steps.append(tuple(waypoint))
        point = (float(waypoint[0]), float(waypoint[1]))
        if self.refuse is not None and point == self.refuse:
            raise SurveyRefusal("NAV_ENVELOPE", at=point)
        if self.refuse_short and self.goal is not None:
            short = math.hypot(point[0] - self.goal[0], point[1] - self.goal[1]) > 1e-9
            if short:
                raise SurveyRefusal("NAV_ENVELOPE", at=point)
        if self.move and self.map_port is not None:
            self.map_port.travelled += self.metres
            self.map_port.pose = (float(waypoint[0]), float(waypoint[1]), 0.0)
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


def test_the_travel_budget_stops_a_leg_that_has_gone_far_enough():
    """The budget is measured as distance driven *during this leg*, so the fake has
    to advance the odometer as it drives, not start with a large reading."""
    map_port = FakeMap()
    driver = FakeDriver(map_port=map_port, metres=2.0)
    result = SurveyRunner().run_leg(map_port, driver, FakeReport(), remaining_m=5.0, number=1)
    assert result.exploration["stopReason"] == "travel_budget"
    assert result.travelled_m >= 5.0


def test_a_leg_whose_time_is_up_stops_even_with_budget_left():
    # One read to set the deadline, the next by the loop: a step equal to the leg
    # budget puts the second read past it.
    result, _map_port, driver, _report = run(step=EXPLORATION["legSeconds"] * 2)
    assert result.exploration["stopReason"] == "leg_timeout"
    assert not driver.steps, "a leg past its deadline must not ask the driver to move"


def test_cancellation_propagates_out_of_the_loop():
    map_port = FakeMap()
    map_port.cancelled = True
    with pytest.raises(RuntimeError, match="cancelled"):
        SurveyRunner().run_leg(map_port, FakeDriver(), FakeReport(), remaining_m=75.0, number=1)


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
