# XLeRobot Observable Simulation and Semantic Tool Training Design

**Date:** 2026-08-19

**Status:** Approved

## 1. Objective

Deliver a local, observable minimum closed loop in which one command starts the MuJoCo Robot Runtime and Local Agent, the Console immediately shows an XLeRobot task scene, and the existing natural-language request completes both goals:

1. put the red cup in the right storage bin;
2. bring the blue bottle to the front delivery tray.

The first training milestone is semantic tool-level reinforcement learning. Joint-level PPO/SAC, visual policies, and hardware sim-to-real calibration are outside this milestone.

## 2. Current Failure and Root Cause

The running MuJoCo service already returns task entities, and the Local Agent can reach its RuntimeInfo API. The empty Console has two independent causes:

- Local Agent telemetry is only published while a task executes, so a newly opened Console receives `hasLatest: false` until the user approves a task.
- The MuJoCo XML contains only a table, objects, bins, and a tray. It has no XLeRobot bodies, joints, actuators, cameras, or robot scene entity. The Console therefore draws only a fixed placeholder dot.

The existing pick and place implementation changes an object free-joint position directly. It validates Agent protocol behavior but is not an articulated robot motion simulation.

## 3. Scope

### Included

- persistent one-command simulation lifecycle with start, stop, restart, status, and logs;
- startup and periodic low-rate observations independent of task execution;
- an articulated XLeRobot MuJoCo model combined with the tabletop task scene;
- robot, map, object, bin, and tray semantic observations;
- a live scene frame plus a semantic top-down fallback in the Console;
- observable joint, gripper, held-object, active-tool, reward, and verification state;
- bounded tool implementations for observe, resolve, plan, pick, verify, place, and recover;
- a deterministic, dependency-light semantic tool RL environment, trainer, checkpoint, evaluator, and policy executor;
- end-to-end verification of the full two-goal Chinese task;
- preservation of the existing Robot Runtime boundary for MuJoCo and physical XLeRobot.

### Excluded

- claiming millimetre-accurate equivalence to the latest two-wheel physical XLeRobot;
- continuous joint-level PPO, SAC, TD3, or vision-language-action training;
- automatic hardware commissioning or bypassing physical safety checks;
- allowing a learned policy to construct approval IDs, deadlines, leases, idempotency keys, or safety profiles.

## 4. Model Provenance and Fidelity

The repository already pins XLeRobot upstream commit `3d14695e40c9c68229c0aacffca6053c75cd3eb6`. The simulation vendors the official MuJoCo model and only the mesh assets referenced by that model, with an attribution file containing the upstream URL, commit, and Apache-2.0 license notice.

The official model is integrated as an articulated visual and kinematic base for the task scene. Runtime metadata and the Console identify the exact model revision. Because upstream does not provide a complete current two-wheel URDF/MJCF at this milestone, the product describes this as a pinned official XLeRobot simulation model, not a calibrated digital twin of every hardware revision.

The XLeRobot is placed in front of the table with both arms in a collision-safe home configuration. Task objects are located inside the reachable workspace. The scene retains the current stable entity IDs so existing grounding and task tests remain valid.

## 5. Architecture

```text
robot-agent start sim
  -> scripts/sim-stack.sh start
       -> MuJoCo Robot Runtime (127.0.0.1:50051)
       -> Local Agent          (127.0.0.1:8787)

Local Agent observer (1 Hz)
  -> Robot Runtime Observe
       -> semantic entities
       -> robot and training state
       -> optional compressed scene frame
  -> TelemetryHub + SceneFrame cache
  -> Console telemetry API + scene frame API

Natural-language task
  -> deterministic/LLM intent and plan
  -> Agent Runner safety envelope
  -> Robot Runtime semantic tool commands
  -> MuJoCo tool implementations
  -> post-action verification
  -> task state and observable telemetry

SemanticToolEnv
  -> tool actions
  -> same MuJoCo world/tool implementations
  -> shaped reward and terminal result
  -> Q-learning trainer
  -> versioned JSON checkpoint
  -> evaluator/policy executor
```

The Agent continues to depend only on `edge/runtime`. Switching between simulation and hardware changes connection configuration and adapter identity, not Agent business logic.

## 6. Simulation World and Tools

`TabletopWorld` remains the authority for MuJoCo state. It gains focused collaborators rather than absorbing training, rendering, and policy code:

- scene/model loader: constructs the combined XLeRobot tabletop model and validates required bodies, joints, actuators, cameras, and entities;
- motion controller: runs bounded joint interpolation and safe poses;
- attachment controller: attaches only after the gripper reaches a grasp tolerance, keeps the held object aligned with the end effector, and releases at the destination;
- renderer: returns an optional compressed overview frame without changing task correctness;
- semantic tools: implement one capability each and return structured success, code, confidence, and state changes.

The minimum articulated sequence is:

1. observe and resolve one object and one destination;
2. move the selected arm to a safe pre-grasp pose;
3. approach, close the gripper, and establish a controlled attachment;
4. verify that the correct object is held;
5. move through a lift waypoint to the destination;
6. release the object;
7. verify destination distance and height;
8. recover to the home pose.

Every loop is bounded by a maximum step count. Joint targets are clamped to model ranges. Failures return structured error codes instead of reporting success.

## 7. Observation and Console Contract

Local Agent owns a background observer tied to its root context. It attempts an initial observation during startup and continues at one sample per second. Robot disconnection and frame-render failures are best-effort observability failures; they do not mutate task state or bypass execution safety.

Telemetry includes:

- robot identity, adapter, model revision, activity, and mode;
- robot base pose, arm joint positions, gripper state, and end-effector pose;
- held entity, active tool, current target, reward, episode, and verification confidence;
- semantic map entities and their poses;
- anomalies and last error.

Compressed frame bytes are cached separately from JSON telemetry. `GET /v1/scene/frame` returns the latest image with its media type and no-store caching. The Console shows the frame when present and continues to render the top-down semantic map when rendering is unavailable.

The top-down view uses the observed robot pose and footprint. It no longer draws a robot at a hard-coded origin.

## 8. Semantic Tool Reinforcement Learning

The training package uses NumPy and the simulator package only. It does not add PyTorch or a GPU requirement.

### Environment

`SemanticToolEnv` exposes:

- `reset(seed, goal)`;
- `step(action)` returning observation, reward, terminated, truncated, and info;
- a finite state encoding for goal, phase, grounded target, held object, placement state, verification state, recoverability, and remaining budget;
- discrete actions matching the shared semantic tool names.

### Reward

- small negative cost for each action;
- positive reward for correct grounding and grasp planning;
- larger reward for successful grasp and placement;
- terminal success reward after verified placement;
- penalties for incorrect order, wrong object/destination, empty grasp, repeated no-op action, timeout, or unsafe state;
- a smaller positive reward for a successful recovery when recovery is required.

### Randomization

Training varies seed, supported object category/color, destination, starting positions within reachable bounds, and optional transient tool failures. The evaluator covers both pick-and-place and fetch goals.

### Policy Artifact

The trainer writes an atomic, versioned JSON checkpoint containing the state/action schema versions, Q-table, hyperparameters, seed, training summary, and tool catalog fingerprint. Loading fails closed on version or catalog mismatch.

The policy executor runs only semantic tools. Agent-side deterministic code continues to generate the physical safety envelope. The learned policy cannot emit transport or safety-control fields.

## 9. One-Command Lifecycle

The stable commands are:

```bash
./bin/robot-agent start sim
./bin/robot-agent status sim
./bin/robot-agent logs sim --follow
./bin/robot-agent restart sim
./bin/robot-agent stop sim
```

`scripts/sim-stack.sh` implements these operations using:

- explicit PID files under `artifacts/sim-stack/run`;
- logs under `artifacts/sim-stack/logs`;
- persistent Local Agent data under `artifacts/sim-stack/local-agent`;
- fixed loopback endpoints `127.0.0.1:50051` and `127.0.0.1:8787` by default;
- dependency, port, process identity, and HTTP/gRPC readiness checks;
- exact-PID shutdown with a bounded graceful wait and escalation only for the recorded child;
- refusal to overwrite live foreign processes or stale state without validation.

The existing transient `robot-agent demo` remains an isolated acceptance command and is not reused as a long-running service manager.

## 10. Error Handling and Safety

- Startup fails with a clear log path if either process or readiness check fails; any child started by that attempt is cleaned up.
- Port conflicts are reported without killing the owning process.
- The observer records connection anomalies but does not mark tasks successful or failed.
- Rendering failure falls back to semantic view and does not change execution.
- Missing model bodies, joints, or policy schema are startup/test failures.
- Physical XLeRobot continues to require mTLS, calibration, perception/action providers, approval, and all existing safety checks.
- Simulation plaintext remains available only through the explicit simulation path.

## 11. Testing Strategy

Implementation follows red-green-refactor cycles.

Unit tests cover:

- model loading and required XLeRobot joints/entities;
- bounded articulated tool motion and structured failure codes;
- scene frame and semantic observation production;
- observer startup, periodic publication, cancellation, and non-fatal errors;
- scene frame HTTP behavior;
- RL reset/step/reward/termination semantics;
- deterministic training, checkpoint round-trip, schema mismatch, and evaluation;
- lifecycle command dispatch, PID validation, port conflicts, readiness failure, and cleanup.

Integration tests cover:

- startup immediately produces non-empty telemetry;
- Console receives a robot entity and task scene before task approval;
- the complete Chinese sequence reaches `SUCCEEDED`;
- red cup verifies inside the right bin and blue bottle verifies on the front tray;
- telemetry exposes articulated movement and final state;
- a trained semantic policy completes randomized evaluation goals.

Final verification runs Go tests with race detection, Python tests, lint, build, semantic policy training/evaluation, the persistent stack smoke test, and the existing simulation acceptance matrix.

## 12. Acceptance Criteria

The work is complete when all of the following are demonstrated from a clean service start:

1. one command starts both long-running services and status reports both healthy;
2. the Console displays the pinned XLeRobot model, table scene, red cup, blue bottle, right bin, and front tray before task approval;
3. the specified two-goal request finishes in `SUCCEEDED`;
4. the red cup and blue bottle pass destination verification;
5. scene, robot state, active tool, and verification telemetry update during execution;
6. the training command creates a loadable checkpoint and its evaluator meets the configured minimum success threshold on seeded randomized goals;
7. MuJoCo and physical XLeRobot retain the same Agent-facing Runtime abstraction;
8. all relevant Go, Python, lint, build, and end-to-end checks pass without incorporating unrelated working-tree changes.
