"""The survey: one policy, one loop, and the ports it needs to run.

This is the layer the rest of the mapping stack is arranged around. Above it, a
workflow owns sessions, publishing and the driver; below it, `exploration` answers
"where next" and `map_pipeline` answers "what is known". Between them sits the only
thing that has to change when the *policy* changes, and until this module existed
that thing was a 143-line method inside a 1395-line class — so "improve the
exploration module" meant editing the workflow, and every comparison meant running
a robot for twenty minutes.

The seam is three ports and nothing else:

* :class:`SurveyMap` - what is measured: the grid, the poses, the clearance, the
  space the camera cannot see.
* :class:`SurveyDriver` - what moves: one bounded step, one look around.
* :class:`SurveyReport` - what is said: progress, and the report the console reads.

Everything else the loop needs is configuration. Nothing in this module knows what
a ``RobotWorkflow`` is, which is what makes the loop testable against a recording,
a simulator or a fake - and that is the point of extracting it.

This module is deliberately free of robot, session and transport concerns. It has
no imports from the gateway beyond the two pure layers it drives.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from .exploration import (
    MIN_FRONTIER_AREA_M2,
    Grid,
    coverage_report,
    explore_target,
    frontier_mask,
    next_waypoint,
    plan_route,
    still_open,
    traversable_for,
)
from .geometry import pose_se2

#: Everything the survey loop's behaviour depends on. Kept in one dict because
#: these numbers are only meaningful relative to each other: the arrival radius
#: against the lookahead, the clearance against the frontier threshold, the frame
#: margin against the leg budget. A caller overriding one should see the others.
EXPLORATION = {
    #: How far the base RGB-D camera is assumed to settle the map ahead of it.
    "sensorRadiusM": 3.0,
    #: Frames the survey may skip because too little depth was measured, before
    #: the leg ends and publishes what it has. One blank wall is not news; a run
    #: of them means the robot is staring at something it cannot measure.
    "depthStarvedLimit": 25,
    #: Radius used to ask "is this corner still unseen?" before spending turns.
    "lookRadiusM": 3.0,
    #: Above this local unknown fraction, standing still and turning pays off.
    "lookThreshold": 0.25,
    #: Frontier fragments smaller than this area are corners of known rooms, not
    #: rooms. See MIN_FRONTIER_AREA_M2: this is what decides whether a survey
    #: *commits to a room* or nibbles at the slivers along the way, and it is an
    #: area rather than a cell count because the fragments are a physical size.
    "minFrontierAreaM2": MIN_FRONTIER_AREA_M2,
    #: One bounded step per re-plan: the drive changes the map it was planned on.
    "lookaheadM": 0.6,
    "maxStepM": 0.5,
    #: Stop a leg before the 400-frame session budget so it can still be saved
    #: and continued instead of failing at the cap with the leg unpublished.
    "frameMargin": 45,
    "legSeconds": 900.0,
    "headingToleranceRad": 0.10,
    #: Close enough to the chosen viewpoint to be standing at the unknown edge.
    "arrivalM": 0.7,
    #: Turning is not free: a quarter turn is three bounded commands and a
    #: dozen keyframes, so look around only after covering some ground.
    "lookSpacingM": 1.2,
    #: Floor this close to where the robot has already driven is inside its own
    #: camera blind spot: it will never be measured, so it is not a frontier.
    "blindRadiusM": 1.0,
    #: How far past the chassis the robot's own drivable footprint is trusted.
    #: It is standing there without contact, so the space is provably free.
    "selfRadiusMarginM": 0.30,
    #: Planning keeps this much more than the driver's own envelope. It has to be
    #: small, and it has to match the radius the travelled trail was certified
    #: with: a planner more cautious than the certification turns the corridor it
    #: just drove down into no-go space. Measured on a finished house map, a 6 cm
    #: margin cut the drivable cells from 6,909 to 4,149 and left the robot's own
    #: starting cell unplannable, which is what "no reachable frontier" meant.
    #:
    #: This margin is the only difference between the planner's clearance and the
    #: robot's own footprint, and both the trail certification and the driver's
    #: guard are measured against it: a plan, a proof and a motor guard are about
    #: one number, or the robot is told to go somewhere nothing certified.
    "planningMarginM": 0.0,
    #: Consecutive refusals before the leg gives up and reports where it stopped.
    "maxConsecutiveRefusals": 6,
    #: Refused approaches the planner routes around before the list is cleared.
    #: Past this many, the avoid list has stopped describing approaches and started
    #: describing places: on a real house it eventually walls off the very regions
    #: the survey is trying to reach, and the leg ends with unknown space it could
    #: have driven to. Clearing it re-opens the map with the obstacle evidence the
    #: grid has since accumulated.
    "maxRefusedSites": 12,
    #: Steps that neither moved nor were refused before a leg reports no progress.
    "maxIdleSteps": 12,
    "lookSweepsMax": 4,
    "lookSweepsMin": 2,
    "lookDenseThreshold": 0.45,
    #: How many look-arounds one leg may spend. Turning in place is the one motion
    #: the depth ICP has the least to work with, so it is budgeted rather than free.
    "maxLooksPerLeg": 6,
}


class SurveyRefusal(Exception):
    """The driver declined a step, with a code the operator will see.

    Raised by :meth:`SurveyDriver.drive_step`. Kept separate from the gateway's own
    service error so that this module needs no transport vocabulary to describe a
    refusal - the adapter above translates.

    ``at`` is *where* the step was refused, when the driver can say. It matters
    because a refusal is evidence about one place, and the loop has to blacklist
    the place rather than the errand: a step refused a metre from the robot says
    nothing about whether the destination two rooms away is reachable.
    """

    def __init__(self, code: str, at: tuple[float, float] | None = None):
        super().__init__(code)
        self.code = code
        self.at = None if at is None else (float(at[0]), float(at[1]))


class SurveyMap(Protocol):
    """What the survey can measure."""

    def live_grid(self) -> tuple[Grid | None, np.ndarray | None]:
        """The occupancy as it stands now, plus the cells the camera cannot see."""

    def current_pose(self) -> Any:
        """The robot's measured pose: what the drives are certified against."""

    def planning_pose(self) -> tuple[float, float, float]:
        """The pose routes are planned from, which may be ahead of the measured one."""

    def planning_clearance(self) -> float:
        """Distance the planner keeps from observed obstacles, in metres."""

    def self_mask(self, grid: Grid, x: float, y: float, clearance: float) -> np.ndarray:
        """The robot's own footprint, so the planner does not see it as an obstacle."""

    def travelled_m(self) -> float:
        """Distance driven so far in this session."""

    def frame_count(self) -> int:
        """Captures registered so far."""

    def max_frames(self) -> int:
        """The session's capture ceiling."""

    def depth_starved(self) -> int:
        """Consecutive views that measured too little depth to register."""

    def check_cancel(self) -> None:
        """Raise if the run has been cancelled."""


class SurveyDriver(Protocol):
    """What the survey can move."""

    def drive_step(self, waypoint, base, pose, *, after_pose=None) -> bool:
        """Take one bounded step. Raises :class:`SurveyRefusal` if the driver declines."""

    def look_around(self, live, blind=None) -> None:
        """Turn on the spot to measure what a forward camera has not seen."""


class SurveyReport(Protocol):
    """What the survey can say."""

    def publish_exploration(self, report: dict[str, Any]) -> None:
        """Record the running report, which a console reads while the leg runs."""

    def publish_message(self, text: str) -> None:
        """Say what the survey is doing, in language an operator can act on."""


@dataclass
class LegResult:
    """What one leg did, and why it stopped."""

    travelled_m: float
    exploration: dict[str, Any] = field(default_factory=dict)
    #: True only when the map is finished: unknown space the planner could not
    #: reach is a different, reportable outcome.
    complete: bool = False


class SurveyRunner:
    """Drives one leg of a survey to its stopping condition.

    The runner owns the loop and the decision to stop; the ports own the robot.
    That split is what lets the same loop be exercised against a recording or a
    simulator without a workflow, a session or a driver.
    """

    def __init__(self, *, config: dict[str, Any] | None = None,
                 clock=time.monotonic):
        self.config = dict(EXPLORATION if config is None else config)
        self._clock = clock

    def run_leg(self, map_port: SurveyMap, driver: SurveyDriver,
                report: SurveyReport, *, remaining_m: float, number: int) -> LegResult:
        config = self.config
        started = map_port.travelled_m()
        deadline = self._clock() + config["legSeconds"]
        # Viewpoints the driver refused. Retrying one would spin, and treating a
        # refusal as the end of the survey would be wrong: the obstacle is local,
        # so the planner is told to route around it and pick something else.
        refused: list[tuple[float, float]] = []
        consecutive = 0
        idle = 0
        looks = 0
        last_look = 0.0
        # Sticky target. Re-picking the best frontier on every step made the
        # robot walk to the middle of a room and oscillate between four equally
        # good corners; a target is held until it is reached or goes stale.
        target_xy = None
        exploration: dict[str, Any] = {}
        driver.look_around(*map_port.live_grid())

        def finish(reason: str, complete: bool = False) -> LegResult:
            exploration["stopReason"] = reason
            report.publish_exploration(dict(exploration))
            return LegResult(travelled_m=map_port.travelled_m() - started,
                             exploration=dict(exploration), complete=complete)

        while True:
            map_port.check_cancel()
            stopped = self.stop_reason(map_port, started=started, remaining_m=remaining_m,
                                       deadline=deadline)
            if stopped:
                return finish(stopped)
            live, blind = map_port.live_grid()
            if live is None:
                return finish("no_frames")
            grid = live
            base = map_port.current_pose()
            pose = map_port.planning_pose()
            clearance = map_port.planning_clearance()
            if target_xy is not None and (
                    math.hypot(pose[0] - target_xy[0], pose[1] - target_xy[1])
                    <= config["arrivalM"]
                    or not still_open(grid, target_xy, config["sensorRadiusM"])):
                target_xy = None
            if target_xy is None:
                target = explore_target(
                    grid, robot_xy=(pose[0], pose[1]),
                    sensor_radius_m=config["sensorRadiusM"], radius_m=clearance,
                    min_frontier_area_m2=config["minFrontierAreaM2"], avoid_xy=refused,
                    blind=blind,
                    extra_traversable=map_port.self_mask(grid, pose[0], pose[1], clearance))
                if target is None:
                    # Nothing reachable is still unknown: this is the completion
                    # condition, not a failure, and it is worth saying so plainly.
                    # The counts go into the report so "complete" can be checked
                    # rather than taken on faith.
                    open_frontier = frontier_mask(grid.cells)
                    if blind is not None:
                        open_frontier = open_frontier & ~blind
                    unplanned = int(open_frontier.sum())
                    # "Complete" has to mean the map is finished, not that the
                    # planner ran out of ideas. Unknown space the planner could
                    # not reach is a different, reportable outcome - and calling
                    # it complete would end the survey on a lie.
                    finished = unplanned == 0
                    exploration = {**coverage_report(grid.cells), "leg": number,
                                   "refused": len(refused), "target": None,
                                   "stopReason": "complete" if finished
                                   else "no_reachable_frontier",
                                   "frontierCells": unplanned,
                                   "blindCells": 0 if blind is None else int(blind.sum())}
                    report.publish_exploration(dict(exploration))
                    return LegResult(travelled_m=map_port.travelled_m() - started,
                                     exploration=dict(exploration), complete=finished)
                target_xy = tuple(target.viewpoint)
                path = list(target.path)
            else:
                path, _length = plan_route(
                    grid, robot_xy=(pose[0], pose[1]), goal_xy=target_xy,
                    radius_m=clearance, avoid_xy=refused,
                    extra_traversable=map_port.self_mask(grid, pose[0], pose[1], clearance))
                if path is None:
                    target_xy = None
                    continue
            exploration = {**coverage_report(grid.cells), "leg": number,
                           "refused": len(refused), "stopReason": "",
                           "target": [round(v, 3) for v in target_xy]}
            report.publish_exploration(dict(exploration))
            report.publish_message(
                f"自动探索第 {number} 段：前往未知区域 "
                f"({target_xy[0]:.1f}, {target_xy[1]:.1f})，"
                f"地图已探明 {1 - exploration['unknownFraction']:.0%}。")
            traversable = traversable_for(
                grid, clearance, avoid_xy=refused,
                extra_traversable=map_port.self_mask(grid, pose[0], pose[1], clearance))
            waypoint = next_waypoint(grid, path, lookahead_m=config["lookaheadM"],
                                     traversable=traversable)
            if waypoint is None:
                return finish("no_route")
            before = list(base)
            try:
                acted = driver.drive_step(waypoint, base, pose, after_pose=None)
            except SurveyRefusal as error:
                decision = self.refusal_decision(
                    error, target_xy=target_xy, refused=refused, consecutive=consecutive)
                consecutive = decision["consecutive"]
                if decision["retire_target"]:
                    target_xy = None
                if decision["give_up"]:
                    # Refusals come in storms when the planner and the driver
                    # disagree about a corner. Spinning through them burns the leg
                    # and reports nothing, so stop and say where.
                    return finish("no_reachable_frontier")
                report.publish_message(decision["message"])
                continue
            after = map_port.planning_pose()
            was = pose_se2(before)
            moved = math.hypot(after[0] - was[0], after[1] - was[1])
            if map_port.depth_starved() >= config["depthStarvedLimit"]:
                # Too many views in a row measured almost nothing, so the leg has
                # nothing to register even though it can still drive. Ending here
                # publishes what was mapped instead of failing the session: a
                # partial map with a named reason beats no map at all.
                return finish("depth_starved")
            if not acted or (moved < 1e-3 and abs(after[2] - was[2]) < 1e-3):
                # Standing at the viewpoint already, or unable to leave it.
                # Either way this target has nothing left to give, so retire it
                # instead of re-selecting it on the next pass.
                idle += 1
                if tuple(target_xy) not in refused:
                    refused.append(tuple(target_xy))
                target_xy = None
                if idle >= config["maxIdleSteps"]:
                    return finish("no_progress")
                continue
            consecutive = 0
            idle = 0
            # Turning is expensive in both time and keyframes, so a look-around
            # waits until the robot is actually standing at the unknown edge and
            # has covered some ground since the last one.
            travelled = map_port.travelled_m() - started
            standing_at_edge = math.hypot(after[0] - target_xy[0],
                                          after[1] - target_xy[1]) <= config["arrivalM"]
            if standing_at_edge:
                # Reached it. Whether or not it revealed everything expected,
                # coming back here cannot reveal more.
                if tuple(target_xy) not in refused:
                    refused.append(tuple(target_xy))
                target_xy = None
            if (standing_at_edge and looks < config["maxLooksPerLeg"]
                    and travelled - last_look >= config["lookSpacingM"]):
                driver.look_around(live, blind)
                looks += 1
                last_look = map_port.travelled_m() - started

    def refusal_decision(self, error: SurveyRefusal, *, target_xy, refused: list,
                         consecutive: int) -> dict:
        """What a refused step means for the rest of the leg.

        A refusal is evidence about *one place*, so it is recorded against that
        place and not against the errand. Recording it against the destination was
        a real defect: a safety layer stops the robot at the obstacle, some way
        short of the goal, so the next plan - which routes around the avoid list -
        proposed the same refused approach again and was refused again. The leg
        then ended on ``no_reachable_frontier`` with the house largely unmapped,
        reporting a policy failure that was really a bookkeeping one.

        The three outcomes, in the order they are decided:

        * a *new* approach was refused - keep the target, route around it;
        * the *same* approach was refused twice - the planner cannot see a way
          past it, so retire the target and choose another;
        * too many sites have been refused - the list has stopped describing
          approaches and started describing places, so clear it. Keeping it
          eventually walls off regions the robot could reach another way, which is
          the same failure by a slower route.

        Extracted from the loop so the rule can be tested as a rule, rather than
        through a house contrived to close at exactly the twelfth approach.
        """
        here = tuple(error.at) if error.at else tuple(target_xy)
        refused.append(here)
        decision = {"consecutive": consecutive, "retire_target": False, "give_up": False,
                    "message": f"自动探索：目标被安全层拒绝（{error.code}），改选其他未知区域。"}
        if error.at is not None and error.at in refused[:-1]:
            # The same approach twice: the planner has nowhere else to go with it.
            decision["retire_target"] = True
            decision["consecutive"] = consecutive + 1
        else:
            decision["consecutive"] = 0
        if decision["consecutive"] >= self.config["maxConsecutiveRefusals"]:
            decision["give_up"] = True
            return decision
        if len(refused) >= self.config["maxRefusedSites"]:
            refused.clear()
            decision["consecutive"] = 0
            decision["message"] = "自动探索：已绕开多处受阻点，重新评估剩余未知区域。"
        return decision

    def stop_reason(self, map_port: SurveyMap, *, started: float, remaining_m: float,
                    deadline: float) -> str | None:
        """Why this leg should stop, or ``None`` to keep exploring."""
        if map_port.travelled_m() - started >= max(.5, remaining_m):
            return "travel_budget"
        if map_port.frame_count() >= map_port.max_frames() - self.config["frameMargin"]:
            # Saving is the point of stopping here: the leg still fits the frame
            # budget, so it can be published and continued rather than lost.
            return "frame_budget"
        if self._clock() > deadline:
            return "leg_timeout"
        return None
