# Gazebo navigation: bounded waiting with zero velocity

Preparation 003 (`task-78ea5f777ad4c708acdbf7d7`) failed during
`navigation.pre_position` at 2026-10-09 18:11:06 UTC. This is a preserved failed
attempt, not a completed preparation and not evidence of a successful grasp.
Its physical action had already started; the original unknown-outcome decision
must not be changed to permit automatic physical replay.

## What the recorded failure proves

Evidence is under
`artifacts/acceptance/long-horizon-20261010/navigation-diagnosis-003/`:

- `failure-navigation-journal.json`: read-only copy of the actual navigation
  SQLite row, goal `0c12beb6294e94e7c929dcb0329a5447e97a2da545594f6b0429810deb2f109a`.
- `failure-ros.log`: original container logs for 18:10:45–18:11:20 UTC, including
  Nav2 acceptance, observation loss, cancellation, and controller stop.
- `live-nav-readonly-samples.json`: eight subsequent map-status reads.
- `runtime-readonly-reconciliation.json`: eight subsequent read-only service
  calls and independent base observations. A first collection attempt failed in
  the local summary calculation because it selected a nonexistent pose key; the
  saved second collection uses the observed `robot_state.base_pose` field.

At 18:11:06.224, the frozen stop observation contained positive ages: RTAB-Map
3400 ms, head depth 2269 ms, base depth 551 ms, map pose and odometry 54 ms.
The first logged head-depth blocker was at 18:11:03.955. The existing two-second
hold elapsed, then Nav2 accepted cancellation at 18:11:06.319 and the controller
reported cancellation successful and stopping at 18:11:06.348. RTAB-Map log
530214→530215 had a 2.890-second wall-clock gap; 530215 reports 0.982 seconds of
processing plus 0.177 seconds of map update.

These are actual delayed observations. They do not reproduce the negative-age
lock defect fixed separately in the Runtime. Navigation PID 41, its ROS
subscriptions, and its clock projection survived the Runtime-only restart.
The logs do not prove CPU pressure as the cause of the delay. Nav2 also rejected
two old GoalUpdater messages before controlling the new goal; it did not accept
those older targets. No unsupported cache-reset or timestamp-rejuvenation fix
was applied.

Later map-status samples were all ready, with head ages 237–356 ms and RTAB ages
529–1058 ms. Eight Runtime observations were `IDLE`, and the measured XY drift
over that collection was zero. These describe the later stopped state only;
they do not retroactively establish the failed action's success or prove the
instantaneous physical velocity at the earlier stop.

## Runtime contract and deployment

`TANGYING_NAVIGATION_OBSERVATION_HOLD_SECONDS` configures one continuous
observation-loss episode. The generic node defaults to **2 seconds** and rejects
non-finite values, zero, negative values, and values over **15 seconds** before
opening the goal journal. The Gazebo-only Compose entry
`deploy/robot/navigation/gazebo-house.compose.yaml` explicitly defaults to
**8 seconds**, preserving a caller's environment override. The existing
`scripts/gazebo-house-stack.sh` uses this Compose file without filtering the
variable. No physical-robot deployment default changes.

This is a wait-budget change, not a sensor-freshness change. A driver must already
opt into `supports_observation_hold` and implement the actual velocity-publisher
gate. The production gate emits zero immediately and continues emitting zero
while observations are unready or the hold remains active. Only the existing
transient blocker set is eligible. Unsupported drivers, mixed unsafe blockers,
and localization-unavailable without another transient blocker still fail
immediately. Freshness thresholds and covariance/visual-quality checks remain
unchanged.

Fresh observations within the budget release the hold for the **same Nav2 goal**;
the velocity lease is cleared, requiring a new command sample before nonzero
output. No goal is resubmitted. At or after the budget boundary the goal fails
and is cancelled, even if the delayed checking thread now sees fresh data. This
also closes the previous ready-first branch that could resume after its wait
budget had elapsed. The independent two-second client polling lease, Runtime
command deadline, explicit cancellation, and stop paths remain effective. The
wait budget is per continuous outage, not an extension of the task deadline.

Deployment into an already running container requires explicit node maintenance;
editing Compose does not change a running process environment. First establish
no active goal and retain terminal receipts, then have the service owner load
both updated navigation modules and set the variable for the new navigation
process. Record its PID, module hashes, configuration, and episode boundary.
Do not restart Gazebo or reset its world to deploy this setting. GoalRegistry
still turns nonterminal journal rows into `NAVIGATION_SERVICE_RESTARTED` on
restart; it never resumes a physical action from such a row. Prior failed
preparations remain failed, and any subsequent preparation is a new task.

## Verification and limits

The targeted HTTP-registry and clock tests passed: **38 passed in 4.62 seconds**.
The registry tests use an explicit fake driver and controlled monotonic clock;
they are not a new Gazebo run or hardware validation. They cover a three-second
outage recovering on the same goal, zero returned velocity during the hold,
budget expiry from both polling and watchdog paths with fresh/stale late data,
unchanged client lease expiry, unsupported/unsafe blockers, invalid budgets, and
failed-command receipt replay without a second action dispatch. Existing
production publisher tests separately verify zero output and discarding a
cached command on resume. Ruff and `git diff --check` also passed.

An eight-second hold could tolerate the recorded 2.890-second gap if all required
inputs become fresh within that budget. It does not guarantee recovery from the
recorded run or from arbitrary load, sensor loss, localization loss, process
failure, or safety faults. Actual longer-budget behavior must be recorded in a
new deployment episode and a new task; the preceding evidence remains unchanged.
