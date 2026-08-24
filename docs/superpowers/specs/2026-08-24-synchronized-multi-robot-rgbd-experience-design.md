# Synchronized Multi-Robot RGB-D Task Experience Design

Date: 2026-08-24
Status: approved for implementation

## Context

TangYing Robot Agent OS can already parse the Chinese two-robot handoff request,
dispatch tools through Fleet and Edge, execute both XLeRobot views in a shared
RoboCasa/MuJoCo world, project world state, and render the result in the browser.
The current demo still breaks the product contract in three important ways:

1. a completed simulator episode is reused by the next task, so the second run
   starts with the red block already in the right target zone and fails
   `resolve_targets` with `OBJECT_NOT_FOUND` before any motion;
2. semantic observations, joint state, rendered camera bytes, task activity, and
   the browser world do not carry one shared capture identity, so an old image
   can be presented beside newer task or world state;
3. the browser repeatedly downloads full task aggregates and installs polling
   timers without a single lifecycle owner. Multiple open tabs can overload the
   control plane, delay health checks, and contribute to Redis lease timeouts.

The required outcome is not an animated mock. A non-technical user must be able
to enter one natural-language request and watch a truthful, continuous chain:

```text
natural language
  -> plain-language understanding
  -> ordered robot subtasks
  -> registered tool activities
  -> synchronized robot state + RGB-D evidence + world changes
  -> Harness-confirmed subtask results
  -> Harness-confirmed overall task completion
```

MuJoCo remains the only physical truth source in simulation. Browser animation,
tool acknowledgements, and camera refreshes are never accepted as task proof by
themselves.

## Goals

- Make the RoboCasa handoff repeatable without restarting the stack.
- Show the natural-language interpretation, ordered subtasks, nested tool calls,
  assigned robot, live state, and environment evidence in ordinary Chinese.
- Render the complete XLeRobot model and joint motion from authoritative world
  observations rather than inventing a second browser physics model.
- Use a real MuJoCo RGB-D camera mounted on each imported open-source XLeRobot
  head assembly; its view must follow the robot/head model.
- Correlate RGB, depth, pose, joints, entities, tool activity, and Harness
  evidence through an immutable simulation/capture identity.
- Detect delayed, duplicated, missing, or out-of-order sensor data and display
  it as stale instead of silently presenting it as live.
- Keep the contracts suitable for later physical XLeRobot cameras, maps,
  calibration, VLA/IL/RL policies, and Harness perception.
- Remove the polling behavior that can overload the control plane.

## Non-goals

- Building a browser physics engine or estimating motion from task progress.
- Claiming real-robot validation from simulation results.
- Making RGB pixels the only completion proof. The simulation Harness continues
  to use authoritative object relations and robot state, while also requiring
  advancing camera evidence where the tool contract declares it.
- Streaming unbounded RGB-D bytes through task/domain events.
- Automatically resetting a real workspace when a user creates a task.

## Selected architecture

### Considered approaches

**Browser-driven animation** would be fast to implement but would duplicate
physics, drift from MuJoCo, and could show a successful move after a failed
tool. It is rejected.

**Independent video streaming plus the existing world stream** would improve
visual smoothness, but without a common capture identity the video, robot
state, and task rail could still disagree. It is rejected as the consistency
model, although the transport may later be upgraded to WebRTC without changing
the data contract.

**One MuJoCo capture bundle projected end to end** is selected. Each robot
observation is built from one immutable `MjData` snapshot. Robot state,
entities, RGB, metric depth, camera calibration, and simulation coordinates all
refer to that snapshot. Edge and Fleet preserve the identity; the browser only
labels evidence live when the identity is current and monotonic.

## Simulation episode lifecycle

Task creation gains an optional execution context:

```json
{
  "request": "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块放到右侧目标区",
  "adapter": "robocasa",
  "executionContext": {
    "mode": "simulation_demo",
    "newEpisode": true
  }
}
```

The local RoboCasa demo browser sets `newEpisode: true`. Other clients remain
backward compatible, and physical adapters reject or ignore simulation-only
execution context according to their declared capabilities.

The planner prepends a visible, simulation-only intent assigned to `robot-1`:

```text
准备新的仿真环境
  -> simulation.reset_episode
  -> observe_scene
  -> verify_episode_ready
```

Reset is a registered Runtime tool, not an out-of-band browser mutation. It
resets the shared `MjData` in place, increments the episode number, restores
both robot joints and the block to their configured initial state, clears held
and placement state, and publishes a fresh capture. Replaying the same command
ID returns its cached result and cannot reset twice.

Reset must not move an external fencing token backwards. The shared simulator
keeps the last adopted token; the Coordinator issues a newer resource lease for
the subsequent manipulation intent. Old task commands and old capture bundles
remain invalid after the episode changes.

`verify_episode_ready` succeeds only after fresh post-reset evidence shows:

- both robots are idle and not holding an object;
- `red-block` is inside `left-start-zone`;
- the episode increased and the simulation step restarted;
- both robot capture sources reported the new episode;
- no prior active manipulation intent still owns the block.

The prelude is displayed to the user as “正在准备新的仿真环境”. It is excluded
from physical deployments and never masquerades as a real-world reset.

## Head-mounted XLeRobot RGB-D sensors

The MJCF composer keeps the upstream open-source XLeRobot geometry, head pan and
tilt joints, and camera mounting structure. It removes the current fixed
world-space `robot-1-evidence` and `robot-2-evidence` cameras. For each prefixed
robot it exposes a stable camera alias attached below the imported head tilt
body:

- `robot-1__rgbd_head`
- `robot-2__rgbd_head`

The camera local pose and optical direction come from the upstream XLeRobot
head camera mount. Prefix rewriting and model validation prove that each camera
belongs to the corresponding robot body subtree. The existing fixed `overview`
camera remains a simulator/operator camera and is never labelled as robot
evidence.

For each head camera MuJoCo renders:

- RGB as `image/png`;
- depth as 16-bit PNG millimetres with declared scale, minimum range, and
  maximum range;
- width, height, field of view, derived pinhole intrinsics, and current
  camera-to-world extrinsics;
- media SHA-256 for immutable evidence references.

RGB and depth are rendered from the same copied `MjData` and share a capture
ID. Invalid depth is encoded as zero. Render failure never invalidates semantic
robot state, but the sensor is marked unavailable/stale and tools that require
fresh RGB-D evidence wait or fail safely according to their contract.

## Atomic capture contract

The Runtime produces `sensor.capture.v1` from one immutable simulation state:

```json
{
  "captureId": "robot-1/episode/4/step/36/capture/81",
  "robotId": "robot-1",
  "episodeId": "robocasa-handoff-v1:4",
  "simulationStep": 36,
  "sourceSequence": 81,
  "capturedAt": "2026-08-24T16:00:00.000Z",
  "frameId": "robot-1/rgbd_head",
  "transformRevision": "robocasa-world-v1",
  "robotState": {},
  "entities": [],
  "frames": [
    {"modality": "rgb", "mediaType": "image/png", "sha256": "..."},
    {"modality": "depth", "mediaType": "image/png;depth=uint16-mm", "sha256": "..."}
  ]
}
```

The gRPC observation protocol is extended compatibly with repeated sensor
frames and capture metadata. The old single compressed image remains readable
during migration and maps to the RGB head frame, but new code never treats it
as unversioned current evidence.

The renderer no longer attaches an arbitrary cached frame to a newer semantic
observation. A capture worker may render outside the world lock, but it renders
an immutable `MjData` copy and publishes state, entities, RGB, and depth as one
completed bundle. A bounded latest-wins queue prevents render backlog. Capture
sequences are monotonic per robot and never reused across episodes.

Edge telemetry preserves the capture fields and forwards the complete bundle.
Fleet stores bounded metadata and the latest bytes per robot, sensor, and
modality. World projection records the capture ID and source sequence used by
the corresponding robot/entity observation. The cloud-assigned world revision
is added to the stored sensor metadata after projection.

## Fleet sensor APIs

The existing scene-frame APIs remain as compatibility aliases for the latest
head RGB image. New APIs expose explicit sensor evidence:

- `GET /v1/sensors/captures?robot_id=robot-1` returns bounded metadata only;
- `GET /v1/sensors/captures/{captureId}/rgb` returns immutable RGB bytes;
- `GET /v1/sensors/captures/{captureId}/depth` returns immutable depth bytes;
- `GET /v1/sensors/latest/{robotId}` returns the latest capture metadata and
  correlated world revision;
- raw byte responses use `ETag` equal to the media SHA-256 and `Cache-Control:
  private, no-cache`; conditional requests avoid downloading unchanged frames.

Authorization and operator/device boundaries follow the existing Fleet rules.
Task events carry capture IDs, revision ranges, and hashes only, never raw image
bytes.

## Tool and task synchronization

Every tool activity includes a synchronization projection:

- command ID and tool attempt;
- robot ID and capture source;
- world/capture basis before execution;
- first and last world revision observed while `RUNNING`;
- latest capture ID and sensor freshness;
- postcondition evidence IDs when confirmed.

The user-visible task flow is:

1. **理解要求** — display the original Chinese request and plain-language
   interpretation immediately after task creation.
2. **准备仿真** — run and verify the episode-reset prelude.
3. **1号机器人交接** — show `observe_scene`, `resolve_targets`,
   `plan_grasp`, `manipulation.pick`, `verify_grasp`,
   `manipulation.place`, and `verify_placement` nested under the first
   subtask.
4. **确认交接** — Harness proves the block is stable in `handoff-zone` and
   advances custody/fencing.
5. **2号机器人接力** — show the corresponding observation, grounding,
   grasp, place, and verification tools for `robot-2`.
6. **确认任务完成** — Harness proves the block is stable in
   `right-target-zone`, neither robot holds it, and required evidence sources
   advanced after the final command.

`SENDING`, `RUNNING`, `AWAITING_EVIDENCE`, `CONFIRMED`, and `FAILED` remain
separate. A tool acknowledgement can advance only to “动作已发送”; it cannot
mark a subtask complete. A subtask becomes complete only from Harness evidence.

While a physical tool is `RUNNING`, at least one of its declared observable
state channels must advance: relevant joint state, held state, object pose, or
base pose. Camera captures must continue advancing at the configured rate. If
tool activity advances but world state is frozen, the system enters
reconciliation and shows “动作已发送，正在重新连接环境数据” rather than showing
success.

## Browser experience

The live world remains the largest panel. Beside it, the mission rail displays:

- “你说的是” and the original request;
- “机器人理解为” and the ordered two-robot goal;
- prominent numbered subtask cards with robot assignment;
- nested human-readable tool rows and their live status;
- a synchronization badge containing environment revision, sensor revision,
  and freshness;
- the exact environment evidence that confirmed each completed subtask;
- guided recovery text when execution or evidence is interrupted.

Two persistent evidence cards show `robot-1` and `robot-2` head RGB views. Each
card identifies “XLeRobot 头部 RGB-D”, capture age, capture ID suffix, world
revision, and RGB/depth availability. An optional depth visualization uses the
actual metric depth image; it is not a colorized copy of RGB. The overview
camera is labelled “MuJoCo 总览” and kept separate.

The WebGL scene consumes authoritative world snapshots and interpolates only
between two observed joint/pose states. Interpolation does not update task
status or fabricate an observation. A visible “同步” state requires:

- monotonic world revision;
- capture episode equal to the active task episode;
- capture source sequence not ahead of an unprojected world basis;
- capture age inside the configured threshold;
- no unresolved revision gap.

Otherwise the relevant card is marked “画面延迟” or “等待传感器”, keeps the last
image visibly stale, and requests a bounded resynchronization.

## Browser and control-plane load

The task list gains a summary representation and bounded pagination. The
browser requests at most the latest 20 summaries and loads intents, experience,
revisions, and events only for the selected task. Historical event arrays are
not returned by the summary endpoint.

All Fleet polling belongs to one session lifecycle object. Showing the
dashboard twice cannot install duplicate timers. Logout, mode change, page
hide, or session replacement cancels timers, in-flight fetches, object URLs,
and sockets. World WebSocket updates remain primary; HTTP snapshot polling is a
bounded recovery path. Sensor metadata is polled only while the page is visible
and image bytes are fetched only when the capture ID or ETag changes.

Fleet-mode detection retries transient health timeouts before falling back to
Local Brain, and a later healthy Fleet response can recover the dashboard
without a full page reload.

## Failure handling

The following cases are explicit and testable:

- repeated task after a completed episode: reset prelude creates a new episode;
- duplicate reset command: idempotent cached result, no second reset;
- stale command from an old episode: rejected before state mutation;
- RGB render failure with healthy semantics: sensor marked unavailable; required
  evidence waits, unrelated safety telemetry continues;
- depth render failure: RGB remains identified as RGB-only, never as RGB-D;
- frame delayed behind state: UI marks the frame stale and does not relabel it;
- frame from the wrong robot or episode: rejected by Fleet/browser;
- out-of-order capture: ignored without decreasing the displayed revision;
- world WebSocket gap: fetch one authoritative snapshot and resume after it;
- browser refresh during a running tool: reload selected task experience and
  current capture basis without replaying the command;
- Edge disconnect after command acceptance: reconcile from environment state,
  never blindly rerun manipulation;
- Redis/coordinator interruption: leadership and fencing rules remain the
  authority; the browser cannot cause dispatch by retrying reads;
- camera healthy while semantic observation freezes: Harness blocks completion;
- semantics healthy while required camera freezes: tool waits or fails safe;
- multiple browser tabs: bounded read load and no mutation beyond explicit user
  commands.

## Sim2Real boundary

The stable production contract is `sensor.capture.v1`, not MuJoCo image APIs.
For a physical XLeRobot:

- the head RGB-D driver replaces the MuJoCo renderer;
- calibrated intrinsics/extrinsics and transform revision replace simulator
  calibration;
- encoder, odometry, gripper, SLAM/map, and RGB-D perception populate the same
  capture/world evidence fields;
- VLA/IL/RL providers consume immutable capture references and semantic task
  context through the existing policy observation contract;
- the Runtime safety executor, resource fencing, Harness predicates, task
  experience, sensor APIs, and browser remain unchanged.

Real deployment requires time synchronization, camera calibration, depth-scale
validation, extrinsic validation, map registration, and a supervised hardware
acceptance pack. Simulation success remains `SIMULATION_GO`, not `PHYSICAL_GO`.

## Verification and acceptance

The implementation is accepted only when all of the following pass:

1. The same running stack completes the Chinese handoff task twice in sequence
   without a process restart. Both tasks reach `SUCCEEDED`; the second task has
   a higher episode ID and starts with the block in `left-start-zone`.
2. The mission rail shows the natural-language understanding, preparation
   prelude, two ordered robot subtasks, every registered tool activity, and the
   Harness confirmation for each subtask.
3. During each manipulation tool, recorded world samples contain relevant
   joint/object changes and both the WebGL robot model and MuJoCo overview show
   those changes before the tool is confirmed.
4. Both head cameras are descendants of the correct XLeRobot head body. Their
   RGB and depth captures share a capture ID, have valid calibration metadata,
   advance during motion, and have changing media hashes when the view changes.
5. Browser evidence cards display only capture/world pairs that satisfy the
   synchronization rules. Injected old, duplicated, cross-robot, and
   wrong-episode frames never replace newer evidence.
6. RGB/depth render faults, frozen semantics, frozen camera, Edge disconnect,
   WebSocket gap, coordinator restart, stale fencing, and Redis interruption
   produce the designed safe/recovery states without false completion.
7. Opening multiple browser tabs does not create duplicate per-page timers or
   unbounded task downloads; health and Redis lease operations remain within
   their configured latency/error gates during the demo.
8. Existing Local Brain, MuJoCo, RoboCasa, Fleet, task revision, policy,
   WebGL, security, and repository CI tests do not regress.

Acceptance artifacts include task/intents/domain events, Harness evidence,
world and sensor trajectories, RGB/depth metadata and hashes, fault results,
browser performance/network summaries, and screenshots from the running page.
