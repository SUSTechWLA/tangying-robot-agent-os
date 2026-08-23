# Policy-Backed Tools, Recovery, and Sim2Real Production Design

Date: 2026-08-24
Status: approved for implementation under the project's standing recommended-route authorization

## Context

TangYing Robot Agent OS already runs a natural-language two-robot block handoff through the Fleet Coordinator, per-robot Edge Workers, MuJoCo or RoboCasa Robot Runtimes, the canonical World Model, and an independent Harness. The Console already projects task understanding, revisions, tool activity, recovery guidance, and the live WebGL world. Distributed acceptance covers process disconnects, coordinator recovery, observation faults, fencing, and evidence revalidation.

The missing production boundary is learned control. The physical XLeRobot backend deliberately accepts only a bounded `action_chunk`, but the Edge Worker has no first-class way to obtain that chunk from a VLA, imitation-learning, or reinforcement-learning policy. Policy identity, model compatibility, observation basis, action limits, and inference failures are therefore not yet part of the auditable command path.

This design adds that boundary without allowing a model to become the safety authority or the source of task truth.

## Goals

- Keep natural-language planning and semantic tools independent of ML frameworks.
- Let a registered VLA, imitation-learning, RL, or deterministic simulation policy implement `manipulation.pick` and `manipulation.place`.
- Bind every learned action to an immutable policy manifest and a concrete observation basis.
- Fail closed on stale/missing observations, policy incompatibility, malformed actions, timeouts, and model drift.
- Make safe automatic recovery visible in the existing `task.experience.v1` mission rail.
- Preserve the same task graph, command identity, leases, fencing, observations, and Harness predicates between simulation and real XLeRobot deployments.
- Add deterministic fault acceptance that demonstrates recovery without requiring a GPU or physical robot in CI.

## Non-goals

- Shipping a general-purpose VLA model or claiming physical validation without robot hardware, calibration, cameras, and a signed model artifact.
- Moving emergency-stop, torque limits, joint limits, collision checks, or operator-presence checks into an ML model.
- Treating a policy response, tool success response, or rendered browser animation as proof of physical completion.
- Sending raw image bytes through Fleet task events or exposing action chunks in the default user interface.
- Coupling the Go control plane to PyTorch, JAX, LeRobot, ROS 2, or a vendor SDK.

## Considered approaches

### Embed policy inference in the Go Agent

This minimizes processes, but binds the planner to model runtimes and GPU libraries, makes model upgrades risky, and prevents independent resource control. Rejected.

### Run policy inference inside the robot gateway

This keeps the action producer close to hardware, but overloads the Pi/runtime, mixes ML lifecycle with the safety executor, and makes cloud and local-brain deployments diverge. Rejected as the default. A gateway-local provider may still implement the same protocol for capable hardware.

### Edge policy sidecar with a stable manifest and inference contract

Selected. A policy sidecar runs beside the Edge Worker on the robot laptop or edge GPU. The Worker sends a semantic capability request plus a bounded observation bundle. The sidecar returns a finite action chunk and immutable policy evidence. The Worker validates it and attaches it to the existing transport-neutral runtime command. The Robot Runtime validates hardware-specific limits again before execution.

## System boundaries

```text
natural language
      |
      v
Task Service -> Fleet Coordinator -> Edge Worker -> semantic tool command
                                         |             |
                                  observation bundle   |
                                         v             |
                                  Policy Sidecar ------+
                                         |
                                  bounded action chunk
                                         v
                                  Robot Runtime safety executor
                                         |
                            canonical environment observations
                                         v
                                  World Model -> Harness
                                         |
                                  task.experience.v1 -> Console
```

The policy sidecar has no Fleet credentials and cannot complete an intent. It can only propose bounded actions for a command already authorized by the Worker.

## Policy manifest

Every provider exposes `policy.manifest.v1`:

- `policyId`, `version`, `framework`: stable identity; framework is `vla`, `imitation`, `reinforcement`, or `deterministic`.
- `artifactSha256`: content identity of the model/checkpoint; all-zero or missing hashes are invalid outside deterministic test mode.
- `capabilities`: semantic tools the policy implements.
- `robotModels`, `adapters`: compatible robot/runtime identities.
- `observationSchema`, `requiredObservationSources`, `maxObservationAgeMs`: required input contract.
- `actionSchema`, `maxActionChunkLength`, `actionBounds`: output contract.
- `transformRevision`, `calibrationRevision`: optional but, when declared, exact-match compatibility gates.
- `training`: dataset, algorithm, seed, evaluation pack, and promotion metadata for audit only; it never relaxes runtime checks.

The manifest has a canonical SHA-256 revision. A decision is rejected when its manifest revision changes between discovery and inference.

## Observation bundle

`policy.observation.v1` is derived locally from the same semantic Robot Runtime telemetry that becomes `world.observation.v1` in Fleet. It includes:

- observation ID and timestamp;
- robot ID, adapter, transform revision, robot pose/joint semantic state;
- grounded entities, poses, relations, confidence, and anomalies;
- optional content-addressed frame references for RGB/RGB-D inputs;
- task, task revision, step, command, world revision, resource, and fencing basis.

Frame references are immutable handles with MIME, dimensions, capture time, and SHA-256. The sidecar may read only explicitly configured local URIs. Fleet events receive references and hashes, never raw frames or credentials.

The Worker rejects inference before any physical side effect when the bundle is missing, older than the manifest limit, contains a required degraded source, uses a mismatched transform, or lacks a required frame.

## Inference and action contract

`POST /v1/infer` accepts a `policy.inference.request.v1` document and returns `policy.inference.result.v1`:

- immutable request/command identity;
- manifest revision;
- inference ID and elapsed time;
- action chunk containing finite named actions;
- observation ID used;
- optional non-sensitive diagnostics.

The Worker enforces response size, timeout, command echo, manifest revision, capability, action count, finite numeric values, and declared bounds. It adds `action_chunk` plus a compact `policy_execution` record to the runtime command. The physical XLeRobot backend independently rejects unknown action keys, base motion, excessive chunks, non-finite values, and out-of-range joints.

Only physical capabilities that advertise `action_chunk` require policy inference. Read-only, verification, safe-pose, and simulation-native semantic tools continue to work without a model. Simulation may enable policy audit mode, where deterministic action chunks are generated and recorded while MuJoCo/RoboCasa still execute their native semantic tool implementation.

## Recovery model

Failures are classified before a physical command is sent:

| Class | Examples | Automatic behavior | User view |
|---|---|---|---|
| `OBSERVATION_WAIT` | stale camera, missing entity, source degraded | refresh observations with bounded backoff | “正在重新确认环境” |
| `POLICY_RETRY` | timeout, sidecar temporarily unavailable | retry inference with same command identity and new inference attempt | “控制模型暂时繁忙，正在重试” |
| `POLICY_BLOCKED` | model hash/robot/calibration mismatch, malformed or unsafe action | send no motion; require configuration correction | “控制模型与当前机器人不匹配” |
| `EXECUTION_RECONCILE` | connection lost after command acceptance | observe first; never blindly replay a physical action | “正在确认机器人最后状态” |
| `SAFE_RECOVERY` | known failed manipulation at a safe checkpoint | invoke `recover_to_safe_pose`, then re-observe/replan | “机器人正在回到安全姿态” |
| `SAFETY_STOP` | e-stop, fencing loss, unsafe limits | latch stop; no automatic resume | “机器人已安全停止，需要现场确认” |

Every attempt emits a structured `RECOVERY_ACTIVITY` task event with a safe public explanation and a collapsed professional record. The browser never derives recovery state from raw error strings.

Automatic retries are allowed only before physical side effects, or after the Harness proves a declared safe checkpoint. Unknown execution outcomes are observation/reconciliation problems, not retry signals.

## Console experience

The existing mission rail remains the only task UI. It gains:

- a compact “控制方式” label such as “视觉语言动作模型” or “仿真确定性策略” on learned tools;
- an explicit progression: “准备环境信息” -> “生成安全动作” -> “机器人执行” -> “等待环境确认” -> “已由环境确认”;
- a recovery timeline showing what happened, what is known, the automatic action, and whether the user must act;
- professional details for policy ID/version/hash prefix, observation ID, inference ID, attempt, command ID, and Harness evidence IDs;
- no raw action chunk in the normal or professional browser view.

WebGL remains a view of authoritative world snapshots. UI animation cannot advance task status, and reconnect/version gaps trigger full task-experience resynchronization.

## Sim2Real mapping

| Stable AgentOS concept | Simulation adapter | Physical adapter |
|---|---|---|
| semantic tool | MuJoCo/RoboCasa registered tool | XLeRobot gateway capability |
| policy provider | deterministic/recorded sidecar | VLA/IL/RL sidecar |
| observation bundle | simulator truth plus rendered frames | calibrated cameras, proprioception, SLAM/map |
| action execution | native semantic world motion or actuator bridge | bounded joint action chunk |
| success proof | simulator observations projected to World Model | independent perception/proprioception projected to World Model |
| safety authority | guard, lease, fencing, simulated limits | guard, lease, fencing, driver limits, e-stop |

Migration therefore changes the policy manifest, observation adapters, calibration/transform revisions, and runtime address. It does not change natural-language task APIs, task revisions, intent identity, custody transfer, or Harness predicates.

## Acceptance gates

The production candidate must pass:

1. Clean full repository CI.
2. Natural-language two-robot red-block handoff to `SUCCEEDED` with 14 confirmed tool activities and post-command Harness evidence.
3. Policy audit evidence for pick/place with manifest revision and observation basis, with no action chunk leaked to task experience.
4. Deterministic faults for observation loss, policy timeout, unavailable sidecar, model/adapter/calibration mismatch, malformed/oversized/non-finite actions, connection loss, worker restart, stale fencing, coordinator restart, and browser reconnect/version gap.
5. Recovery events and user-facing Chinese guidance matching the authoritative event replay.
6. RoboCasa/WebGL visual acceptance proving two complete robot models, the block and zones, current robot activities, camera interaction, and task status all refer to the same world revision.
7. A real-robot release remains `NO-GO` until hardware calibration, camera/verifier integration, emergency-stop drill, signed policy artifact, physical shadow run, and supervised handoff evidence are attached.

## Production truthfulness

This work can make the software integration production-ready and fully prove the simulation path. It cannot honestly certify a physical production deployment without the target XLeRobot hardware and a promoted model. The release evidence must distinguish `SIMULATION_GO`, `SHADOW_GO`, and `PHYSICAL_GO`; simulation success never silently promotes the physical gate.
