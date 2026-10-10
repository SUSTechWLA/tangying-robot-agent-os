# Gazebo sensor readiness clock snapshot correction — 2026-10-10

## Observed run and evidence boundary

The preserved acceptance record is
`artifacts/acceptance/long-horizon-20261010/run-001/final-task.json`.
Task `task-224652b76775477ce79d8ace` called `mapping.build` in survey mode
at `2026-10-09T17:02:19.039419Z`. The runtime returned operation
`db3f7e68e4304cd0a67d0f9f1e0396da`, map `scan-db3f7e68e430`, and state
`moving`. At `17:06:11.153431Z`, status verification reported failure with
`RGBD_NOT_READY,JOINT_FEEDBACK_STALE,SUCTION_FEEDBACK_STALE,IMU_NOT_READY`.
The agent classified the physical outcome as `UNKNOWN_OUTCOME` and prohibited
automatic retry. The later read-only runtime snapshot in
`deployment-001/runtime-after-failure.json` reported ready again.

This investigation reproduced a lock-contention defect in the readiness check.
The original failure record does **not** contain an instantaneous negative-age
measurement, so the defect cannot be asserted as the sole cause of that run's
failure. No old evidence was rewritten and no failed physical action was replayed
as part of this change.

## Source path and correction

`GazeboWorkflowBindings.move` in
`robot/gateway/tangying_robot_gateway/gazebo_workflow.py` invokes the bounded
driver for survey segments. The driver is `GazeboRuntimeNode.bounded_step` in
`robot/ros2_ws/src/tangying_navigation/tangying_navigation/gazebo_runtime_node.py`.
After navigation preparation it calls `_bounded_step`, which checks sensor
readiness and obstacle-cloud freshness on every control tick. The combined
four-blocker message above is returned by that loop's `SENSOR_STALE` path.

The loop already publishes zero velocity when stale input is detected, waits
up to two seconds for fresh measurements, and returns failure if the measurements
do not recover. It also publishes a final zero command on exit. The same check
can fail before the first pulse or after previous pulses in that segment; the
old result does not distinguish these cases. This change therefore does not
reclassify the old physical outcome as unexecuted or safe to retry.

Previously, `readiness_blockers` sampled `time.monotonic_ns()` before acquiring
the shared sample lock. A callback holding that lock could update sensor
timestamps while the readiness reader waited. Once the reader acquired the
lock, `earlier_now - newer_sample_timestamp` was negative, and all otherwise
fresh samples could fail the lower age bound together.

The correction takes the monotonic clock snapshot **inside** the same lock as
the sample snapshot. It preserves both freshness limits: RGB-D at most one
second, and joint/suction/IMU feedback at most 500 milliseconds. Future-dated
samples remain rejected. There is no additional motion retry, relaxed age
threshold, changed control timeout, or new fault recovery policy.

## Reproducible verification

The new offline test module `tests/install/test_gazebo_runtime_freshness.py`
imports the real runtime source with ROS message/executor stubs; it does not
start a ROS graph or simulate successful physical movement. Its deterministic
contention test holds the sample lock, waits until the readiness reader attempts
to acquire it, advances the monotonic clock by 200 milliseconds, refreshes all
samples, and releases the lock.

Before the source correction: **1 failed, 7 passed**. The contention test returned
all four blockers instead of an empty list. The additional tests cover exact
freshness boundaries, samples one nanosecond in the future, and missing samples.

After the correction:

```sh
.venv/bin/pytest -q tests/install/test_gazebo_runtime_freshness.py \
  robot/gateway/tests/test_gazebo_workflow.py \
  robot/gateway/tests/test_gazebo_backend.py \
  robot/gateway/tests/test_gazebo_actuation.py
```

Result: **69 passed in 1.27s**. `git diff --check` also passed.

Source SHA-256:
`0fa8d31fa3d2545ca80251d8bca38b3f7dae344b62179d98d73527c1a34e7f1e`.
Test SHA-256 at the regression run:
`b62d48f0938095592e4110cfd9b02f9af6f99b20f450c5af7e2031cbe38a6840`.
After integration's import-spacing cleanup (`ruff --fix`, lint passed), the test
SHA-256 is `2de5232f0619216f2e71242e6f6e5c9b6006d39e25170935d86fac10087e1482`;
the production source hash is unchanged.

## Deployment boundary

This investigation did not connect to, stop, restart, or reset any running
service. A deployment can reload this source by restarting only the Gateway
runtime process after the old operation has stopped and its evidence is saved.
Keep the Gazebo world, ROS navigation/SLAM processes, map root, runtime journal,
robot identity, calibration, and world revision unchanged. Record the component
restart as a new runtime episode, including the loaded module path and hash.
The new process must reacquire fresh camera and feedback samples before a new
task is admitted.

Gateway restart is not transparent continuation of a mapping operation:
`RobotWorkflow` creates a new in-memory session/operation on startup, although it
can restore a verified persisted active map. Its new `idle` status cannot clear
the old task's unknown outcome. Keep the failed task's evidence immutable and
use a separate task identity for any newly authorized run. Recheck the actual
loaded file hash, runtime/catalog identity, fresh sensor readiness, and preserved
world/map state after deployment; this test result alone is not evidence that a
running process has loaded the fix.
