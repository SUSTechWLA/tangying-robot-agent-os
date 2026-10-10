# Gazebo feedback isolation after main run 004

Main run 004 (`task-24ddc92a7911f1078eb5410e`) failed in mapping operation
`81092ee55fb846cd89e34df11f0928df`, after approximately 6.25 m and 48
registrations. The original task remains failed with an unknown physical outcome;
no command from this operation is automatically replayed.

## Failure evidence

The immutable copies in
`artifacts/acceptance/long-horizon-20261010/mapping-diagnosis-004/` include the
original bounded-step JSONL, Runtime log, command journal, their SHA-256 manifest,
ROS logs, a later independent feedback subscriber, and a deterministic source
reproduction. These files preserve source006 / Runtime PID 89234.

The journal contains 15 ended steps: 14 completed and the last stopped with
`SENSOR_STALE`. In the failure snapshot at monotonic `1129963656962769` ns,
suction receipt age was **504.807 ms**, IMU **504.554625 ms**, joint **7.333875
ms**, and accepted odometry **519.497792 ms**. Both RGB-D captures were
670.309904 ms old. The 500 ms nonvisual gate correctly rejected suction and IMU;
the one-second camera/odometry gates were still satisfied. These are positive
ages, not the earlier negative-age clock-sampling defect.

The last step had already published 171 nonzero pulses. It published zero on
unsafe feedback and again in the final stop path; its terminal record states
`finalZeroPublished=true`. This proves the ROS publication calls, not physical
zero-velocity acknowledgement. The final record followed the sampled fault by
96.965042 ms. It must not be reclassified as an unissued action.

An earlier step in this same operation actually entered a visual/cloud wait and
resumed after **0.242318208 seconds**, then completed with the same step identity.
This demonstrates that path's actual execution, not an eight-second boundary
experiment or complete mapping success. The failed task never reached its new
grasp approach.

## Proven code defect and attribution limits

Before the fix, both joint and odometry callbacks called RGB-D assembly for both
cameras while owning the common sensor lock and the mutually exclusive feedback
callback group. Image construction and full validation could therefore prevent
IMU and suction callbacks from committing new data. A deterministic test of the
original source held assembly for 550 ms: the joint receipt had already been
updated, while both IMU and suction callbacks remained blocked until assembly was
released. That is a reproducible implementation defect.

The failure reader waited only 0.004042 ms for its lock; that measures this
readiness call, not earlier feedback starvation. The cumulative assembly maxima
were 381.120751 ms for base and 115.577458 ms for head, but their exact occurrence
times were not retained. Neither number establishes this failure's unique cause.
The original diagnostic snapshot also lacks suction source sequence and callback
entry timestamps. We cannot reconstruct missing instantaneous evidence.

A subsequent independent 12-second ROS subscriber received 329 IMU, 131 suction,
and 193 each joint/odometry messages. Maximum observed receipt gaps were
316.823292 / 348.983708 / 362.195959 / 365.15125 ms respectively. This confirms
later message arrival; it does not prove arrival at the original failure instant.
The suction plugin emits at 50 ms of simulation time, and the IMU sensor is
configured for 50 Hz of simulation time. These are not wall-time delivery
promises. Scheduling, simulation progress, bridge delivery and callback work can
all affect wall receipt gaps. No unsupported CPU or clock root-cause claim is made.

## Change and safety contract

Joint, odometry and image callbacks now only stage accepted input. A 20 ms timer
in the image/default callback group assembles the latest pending pair per camera;
it does not run in the mutually exclusive feedback group. There is one pending
pair per camera, no unbounded job queue, and one timer callback at a time. The
existing two-thread executor can process feedback during image validation.

The timer takes bounded array/history references under the sensor lock, then
interpolates and performs the complete existing RGB-D validation outside that
lock. Callback arrays, joint dictionaries and pose matrices are replaced rather
than mutated. Their referenced storage remains alive throughout validation.
`GazeboRuntime.validate_sample` has no sample-cache publication side effect;
`record` preserves its existing validation and publication API for other callers.

The final locked commit only publishes a sensor stamp newer than the last
committed stamp. A newer pair arriving while an older valid pair is checked does
not invalidate that older result; the newer pending data is retained for the next
tick. Pending arrays are cleared only when their channel stamp matches the
committed capture. A delayed older completion cannot overwrite a newer sample.
Malformed data cannot replace the last valid sample.

An independent review also reproduced a separate pre-existing mixed-capture race:
`observation` selected sample metadata, then `rgbd_payload` read the cache again.
A concurrent replacement could combine old pose/time with newer RGB-D pixels.
Observation now encodes raw data from the same selected sample; the public
`rgbd_payload(camera)` API remains compatible. The backend observation path also
selects its sample under the lock and performs frame validation/encoding outside
it, retaining that same capture for pose, joints and pixels. It does not add a
large serialization lock around observations.

Capture stamps and interpolation bases are never replaced by validation completion
time. Exact RGB/depth pairing, calibrated image dimensions, bracketed joint/pose/
clock histories, and IMU admission for odometry remain required. The 500 ms
joint/suction/IMU, one-second RGB-D/odometry/cloud, 45-second step deadline,
cancellation and estop gates are unchanged. Nonvisual loss still publishes zero
and fails immediately; it does not enter the visual wait or authorize a retry.

Assembly diagnostics now distinguish snapshot-lock duration, off-lock validation
duration and commit-lock duration. Validation time is no longer labelled as time
under the feedback lock. Imagery can still become stale during long off-lock validation;
the existing strict freshness and bounded visual wait handle that condition.
This change removes the demonstrated lock coupling, not every possible upstream
message-delivery outage.

## Verification and deployment

The final focused ROS-stub / gateway regression set passed **91 tests in 1.15 seconds**.
It covers blocked validation while all four actual feedback callbacks progress,
immutable capture time/pose/joint/intrinsics basis, ROS message buffer lifetime,
new pending pair retention, out-of-order completion, invalid/incomplete capture
rejection and the pre-existing freshness, stop and stream-cancellation contracts.
The two additional cases cover cache replacement during raw observation and
blocked backend frame encoding while actual suction feedback continues.
These are deterministic offline tests, not a successful physical task.

`gazebo_runtime_node.py`, gateway `gazebo_runtime.py` and `gazebo_backend.py` must be synchronized
into a recorded Runtime-only maintenance episode. Keep the existing persistent
root, command/evidence journals, Gazebo world, Nav2, navigation HTTP node,
RTAB-Map, and failed task states. A new Runtime PID and module-pair SHA manifest
must precede a new task. Actual long-task success remains a separate acceptance
condition.

The authorized Runtime-only deployment occurred at 2026-10-09 20:04:41 UTC:
PID 89234 exited on SIGINT and PID 697 started with the previous private process
environment, the same persistent root, and survey hold explicitly set to eight
seconds. The module trio was backed up before replacement. Command-journal and
bounded-step-journal bytes remained unchanged across this maintenance window.
Gazebo 27, RTAB-Map 35, Nav2 37–39 and navigation HTTP 57054 retained their start
identities. No world reset or old-command replay occurred.

`artifacts/acceptance/long-horizon-20261010/deployment-004/` contains
`runtime-isolation-maintenance.json`, the three `module-before-isolation-*.py`
backups, `runtime-isolation-module-resolution.json`, and the before/after read-only
sample files. The maintenance record pins each source and installed path with its
SHA-256. New-run results must identify this component episode rather than attributing
the patch to failed run 004.
