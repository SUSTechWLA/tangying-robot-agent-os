# Gazebo survey observation wait and diagnostic evidence

Main run 003 (`task-a8b53e358cd235cd64192a64`) remains failed. Its
`mapping.build` operation `e1b05f3420194ea5be0ce25a1dc5c114` ended with
`RGBD_NOT_READY` at 2026-10-09 18:25:45 UTC. The actual mapping status records
4 frames, 3 registrations, 12685 points, and 0.4499949 metres travelled.
This was an already-started physical operation; its unknown outcome must not be
rewritten as an unissued action or automatically replayed.

The previous active map `scan-cbeb53aa8e01` remained active. The run did not reach
navigation or manipulation, so it validates neither the new eight-second Nav2
hold nor the new grasp approach. The later stopped state cannot establish what
happened at an earlier failure instant.

## Observed evidence and limits

`artifacts/acceptance/long-horizon-20261010/mapping-diagnosis-003/` contains:

- `failure-ros.log`: original RTAB-Map logs around the failure; output intervals
  were roughly 1.3–2.2 seconds during this window.
- `current-readonly-status.json`: subsequent Runtime and mapping/navigation
  status, including the failed operation's measured travel. At this later read,
  Runtime advertised two identical RGB-D blockers; those duplicates came from
  two layers reporting the same class, not proof of two independent faults.
- `ros-streams-readonly-probe.json`: an independent eight-second ROS subscriber,
  using the existing Runtime process environment without publishing motion.
  It received base RGB/depth counts 25/34 with 25 exact sensor-stamp pairs, and
  head counts 29/35 with 29 pairs. Maximum RGB receipt gaps were 771.5/727.1 ms.
  Publisher QoS was recorded alongside message stamps.
- `runtime-capture-age-probe.json`: eight later read-only raw-capture metadata
  samples, with the large RGB/depth payloads omitted. Captures remained available;
  one base capture was already 966 ms old after RPC return.

Those later measurements show that messages and exact pairs were arriving; they
do not identify the earlier failure's unique cause. CPU contention, scheduling,
ROS delivery, exact-pair waiting, and lock delay remain distinguishable hypotheses.
No failure-time lock-duration measurement exists for run 003. Heavy tests and
physical acceptance should run separately, but that operational choice is not
proof that CPU load caused this failure.

## Minimal controller change

Survey movement uses `GazeboRuntimeNode._bounded_step`, not the Nav2 HTTP goal
registry. It already stopped its actual velocity publisher while observations
were stale, with a two-second bounded wait. The new
`TANGYING_SURVEY_OBSERVATION_HOLD_SECONDS` keeps a default of **2**, accepts finite
values in **(0, 15]**, and is explicitly set to **8** by the Gazebo Compose file
unless overridden. The Nav2 setting is separate.

Only RGB-D readiness and obstacle-cloud freshness may enter this wait. Joint,
suction, IMU, and odometry failures stop immediately. Existing RGB-D and point
cloud age limits remain one second; joint/suction/IMU remain 0.5 seconds. The
new explicit `ODOMETRY_STALE` guard rejects a missing, future-dated, or older than
one-second receipt of the current accepted base pose. A fresh camera capture
proves interpolation for its own capture pose, not an independently audited
freshness bound for the current pose later read by the controller.

The controller first publishes zero, retains the same target and operation, and
waits for actual fresh inputs. It does not resubmit a command. A missing real
publisher fails rather than simulating a hold. At or after the continuous hold
budget, even newly fresh data cannot resume that step. Cancellation, estop, the
existing operation lease, and the original 45-second step deadline still win.
The deadline is checked before resuming, after geometry work, and inside the
actual publisher after waiting on the sensor lock. A zero publication bypasses
the sensor-readiness lock so an overdue acquisition check cannot itself delay
the stop request. Publication evidence describes a ROS publisher call, not a
physical stop acknowledgement; measured odometry is still needed for reconciliation.

The generic `RobotWorkflow` and its error handling were not changed. A failed
step keeps its original code/message path and still ends the operation safely.
In particular, this change does not retry failed run 003, grant new physical
authority, weaken collision checks, or change observation timestamps.

## Diagnostics available to agents

`ListServices` advertises `runtime.readiness` with an empty object input schema,
`mutates_world=false`, effects `READ`, no resources, and no authority callback.
Its planning projection contains only `ready`, `blockers`, `robotId`, and
`clockSource`; images and high-rate sensor arrays are not added to planning.
`GetRuntimeInfo` continues to report capability blockers, with the duplicate
RGB-D entry removed. Detailed diagnostics are discovered through the service
catalogue, not a new implicit physical capability in RuntimeInfo.

The service reports `scope=current_sensor_state` and
`phase=read_only_diagnostics`. Its same-lock snapshot contains:

- Capture and callback receipt age, exact source/sensor stamps, pending RGB/depth
  stamps, the last assembled stamp, and the capture clock source per camera.
- Joint/odometry interpolation-history bounds and receipt ages for joint,
  suction, IMU, and current odometry, with the corresponding source stamps.
- Readiness-reader lock wait and capture-assembly timing. Source006 recorded
  assembly time under the shared lock. The subsequent
  [feedback isolation fix](2026-10-10-gazebo-feedback-isolation.md) separates
  snapshot-lock, off-lock validation, and commit-lock durations. Both versions
  retain exact-pair admission; their timing field names distinguish the versions.

All nanosecond timestamps are decimal **strings** so protobuf Struct cannot
round them through a double. Ages/durations are milliseconds as numbers. Camera
callback timestamps describe commits after conversion and lock acquisition,
not DDS transport arrival. `captureClockSource=sensor_clock_wall_bridge` preserves
the original source-time conversion; an old capture is never restamped as fresh.

`historicalBoundedStep` is explicitly separate from the current snapshot. It
contains the last transition from this process, bound to the original operation
ID, owner request ID, PID, step start monotonic stamp, and target. A later fresh
snapshot must not replace that failure cause or resolve an old UNKNOWN outcome.
The service's `evidenceIndex` points to
`$TANGYING_GAZEBO_RUNTIME_ROOT/bounded-steps.jsonl` (default
`/data/maps/gazebo-runtime/bounded-steps.jsonl`), using the same root resolver as
`commands.json`. A restart does not load old transitions
into the in-memory current-process cache; an empty cache explicitly does **not**
mean an empty journal or no unresolved physical outcome.

Hold entry, resume, unsafe feedback, and terminal transitions append JSONL,
flush, and fsync the file and containing directory. A terminal transition is
written after the final zero publication attempt. Evidence records actual
nonzero publication count, zero publication count, final-zero publication result,
hold observations, and the original outcome code. Per-step retained hold details
are capped at 16 while transition lines and a total count preserve the history.
Persistence failure propagates as an execution error with the final stop path
still running; it cannot silently report durable evidence. These records are
diagnostic evidence, not permission to replay a physical action.

## Validation and deployment boundary

The deterministic ROS-stub controller/readiness suite passed **36 tests in 1.00
second**. It covers three-second visual/cloud loss with one resumed step, both
fresh and stale expiry, all nonvisual blockers, missing zero publisher,
cancellation/estop/deadline including a lock wait across deadline, zero independent
of the sensor lock, exact stamps through the read-only service, file/directory
fsync, failed persistence, and separating historical evidence from current state.
These tests use an explicit fake publisher, not Gazebo or a physical robot.
The separate gateway backend/workflow/grasp regression set passed **64 tests in
5.19 seconds**. Ruff and `git diff --check` passed.

The service owner must load the updated Runtime module in a recorded Runtime-only
maintenance episode and set `TANGYING_SURVEY_OBSERVATION_HOLD_SECONDS=8` explicitly
for an existing container. Editing Compose does not update process environments.
Keep Gazebo, Nav2, RTAB-Map, world state, and previous failed tasks intact. Confirm
stopped state, no active operation, module hashes, both hold configurations, and
fresh diagnostics before a **new** task. Actual eight-second survey hold recovery
and physical manipulation still require new-run evidence; run 003 supplies none.

## Persistent root correction and loaded episode

The independent pre-run audit found that the first diagnostic implementation used
`TANGYING_RUNTIME_ROOT`, while the established command journal uses
`TANGYING_GAZEBO_RUNTIME_ROOT`. This could leave the diagnostic journal outside
the mounted maps directory. Both paths now use one resolver for the established
variable, with `/data/maps/gazebo-runtime` as its default. The obsolete variable
does not override it. The two configuration regressions plus the existing
readiness/controller suite passed **38 tests in 0.94 seconds**; Ruff and the diff
whitespace check passed. No freshness or controller behavior changed in this fix.

After the owner stopped the agent, the maintenance gate found zero pending
Runtime commands and zero nonterminal navigation goals. At
2026-10-09 19:39:52 UTC, only Runtime PID 68752 was replaced by PID 89234, with
survey hold explicitly set to eight seconds. The module loaded from the installed
site-packages path has SHA-256
`4526ac6cf5463aee17a731a349d5b61ac3f96ea63d14fedb66a021030ab2814c`.
Gazebo, RTAB-Map, Nav2, and the navigation HTTP node retained their process start
identities. The command journal bytes were unchanged across this maintenance
window. Neither the old incorrect path nor the correct diagnostic path contained
a bounded-step journal yet; no replacement history was invented.

The deployment-003 records below preserve the backup, maintenance boundary,
loaded module paths, and subsequent current-state observations:

- `runtime-module-before-persistent-root-fix.py`
- `runtime-persistent-root-maintenance.json`
- `runtime-root-source-after.json`
- `runtime-root-readiness-after.json`
- `runtime-root-stopped-after.json`

Eight subsequent diagnostic samples were ready and advertised the mounted
`/data/maps/home_furnished/runtime/bounded-steps.jsonl` path. Eight separate
observation samples reported IDLE and zero measured XY pose drift. These are
current-state maintenance checks; they do not close previous unknown outcomes
or establish that a new physical survey, hold, or grasp has succeeded.
