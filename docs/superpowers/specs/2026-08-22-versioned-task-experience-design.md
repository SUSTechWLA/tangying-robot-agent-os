# Versioned Task Experience and Harness Reconciliation

Date: 2026-08-22
Status: approved in interactive design review; pending written-spec review

## Context

TangYing Robot Agent OS already accepts natural-language tasks, plans intents, dispatches tools to local or simulated robots, projects authoritative world state, and lets a Harness Agent judge physical completion from observations. The current Console still exposes this lifecycle as technical task IDs, intent statuses, and audit events. A non-technical robot owner cannot easily answer four basic questions:

1. What did the robots understand?
2. What are they doing now?
3. Which robot capability is being used, in ordinary language?
4. How can I change the task without losing completed work or creating an unsafe distributed race?

The current task model is also effectively single-version. It supports create, approve, cancel, state transitions, and append-only events, but it has no first-class update/revision contract. This design adds a versioned task experience shared by cloud Fleet and the Local Brain, while preserving authoritative environment evidence and fencing semantics for simulation and future real robots.

## Goals

- Let a user update an existing task with another natural-language instruction.
- Preserve completed work and its evidence; replan only work that has not safely completed.
- Explain task understanding, progress, robot capabilities, recovery, and updates in language a non-technical user can understand.
- Keep raw tool names, parameters, command IDs, and evidence references available as collapsed professional details.
- Make concurrent updates, retries, failover, and delayed messages deterministic and idempotent.
- Require Harness/world evidence before displaying a physical step as complete.
- Use the same contracts for RoboCasa simulation and future real robots.

## Non-goals

- A general visual workflow editor or arbitrary drag-and-drop task graph.
- Editing already completed physical history.
- Treating a tool acknowledgement as proof that the environment changed.
- Hiding emergency-stop controls or replacing physical safety systems with UI confirmation.
- Exposing raw secrets, unrestricted tool arguments, or internal JSON in the default user view.

## Product and visual direction

The page's single job is to let a robot owner understand and safely steer the current mission while continuing to see the live environment.

The selected layout is a **mission rail beside the live world**:

- The live simulation/real-world view remains the largest surface.
- A persistent task rail sits on the right on desktop and below the world on narrow screens.
- The rail begins with the current revision, a short task title, and “机器人理解为”.
- Steps form a continuous mission ribbon from user intent to physical evidence.
- “补充或修改任务” remains visible at the bottom of the rail.
- Completed, running, waiting, paused, and failed states are distinguishable without relying on color alone.

The design retains the Fleet Console's deep navy control-room palette and condensed display face. Mint represents confirmed world evidence, amber represents waiting or safe pause, red is reserved for action-required danger, and cyan identifies system understanding. The signature element is the **mission ribbon**: one continuous line connecting the user's words, each robot action, and the evidence that confirms it.

The main interface uses human-first wording:

- `navigate_to_pose` becomes “移动到指定位置”.
- `close_gripper` becomes “拿稳物品”.
- A successful command acknowledgement becomes “动作已发送”.
- Only a satisfied Harness postcondition becomes “已完成”.

Tool names, safe parameters, command IDs, fencing tokens, and evidence references live under a collapsed “查看专业详情” disclosure.

## User interaction

### Create

The first natural-language input creates revision 1. Before physical execution, the rail shows:

- the user's original instruction;
- the system's plain-language understanding;
- the ordered steps and assigned robots;
- whether physical approval is required.

### Update

The user selects “补充或修改任务” and writes one natural-language instruction. The system creates a proposed revision and returns an inline change preview:

- **保留** — completed work and compatible in-flight work;
- **修改** — pending steps that will be replaced;
- **新增** — new steps;
- **暂停** — any conflict that requires a safe checkpoint or renewed approval.

The user can revise the text or select “确认更新”. Confirmation never rewrites the prior revision. It activates a new immutable revision.

### Recovery

Failures use guided recovery rather than raw alarms. The rail states:

1. what is currently known;
2. whether the robot is safely stopped or holding position;
3. what the system is doing automatically;
4. what the user may choose next.

Example: “还不能确认方块已经放稳。1 号机器人已停在交接区，夹爪保持不动。系统正在重新检查相机和夹爪状态。” Technical reason codes remain available in professional details.

## Domain model

### Task aggregate

`Task` gains:

- `currentRevision: uint64` — the active user-visible revision;
- `aggregateVersion: uint64` — compare-and-swap version for persistence and concurrent writers;
- `revisionState` — the current proposal/activation state.

Existing tasks are treated as revision 1. Existing create/get/list/approve/cancel APIs remain compatible.

### TaskRevision

Each immutable revision contains:

- `taskId` and `revision`;
- `baseRevision` and `expectedAggregateVersion`;
- the user's `request`;
- a plain-language `understanding`;
- a structured `changeSet` containing retained, changed, added, and paused step IDs;
- the parsed intent and immutable plan/catalog revisions used to produce it;
- `riskClass` and `approvalRequired`;
- `status`: `PROPOSED`, `WAITING_APPROVAL`, `WAITING_SAFE_POINT`, `ACTIVE`, `SUPERSEDED`, or `REJECTED`;
- creator, idempotency key, timestamps, and rejection reason.

Revision content is append-only after creation. Status changes are represented by domain events.

### Step identity and state

Steps use a stable logical `stepId` plus the revision that introduced or last changed them. A step contains a semantic fingerprint derived from action, robot binding, resource, goal, and required postcondition. A new revision may retain a completed step only when:

- the semantic fingerprint remains compatible;
- its Harness evidence is still valid for the current world/model identity;
- retaining it does not violate a newer safety or resource constraint.

Step states are:

- `PENDING`
- `READY`
- `RUNNING`
- `AWAITING_EVIDENCE`
- `SATISFIED`
- `FAILED`
- `CANCELLED_BY_REVISION`

Completed steps and their evidence are never changed to a different outcome by a newer revision.

### ToolActivity

Every physical or observational tool invocation publishes a user-safe activity projection:

- `displayName` and `purpose`;
- robot and step;
- `SENDING`, `RUNNING`, `AWAITING_EVIDENCE`, `CONFIRMED`, or `FAILED`;
- a bounded, allow-listed argument summary;
- optional professional details: tool name, command ID, catalog revision, fencing token, and evidence references.

The tool catalogue adds optional user-facing display metadata. Older robots without this metadata fall back to server-owned, conservative capability descriptions; the browser never invents tool meanings.

## API

### Create a proposed revision

`POST /v1/tasks/{taskId}/revisions`

```json
{
  "expectedRevision": 1,
  "request": "最后放到右侧蓝色垫子上",
  "idempotencyKey": "client-generated-uuid"
}
```

The response contains the proposed immutable revision, its change preview, risk classification, and whether confirmation/physical approval is required. A stale `expectedRevision` returns `409 REVISION_CONFLICT` plus the current user-safe task view.

### Confirm a revision

`POST /v1/tasks/{taskId}/revisions/{revision}/confirm`

The request includes the expected current revision and idempotency key. Confirmation either activates immediately, waits for a safe checkpoint, or enters renewed physical approval.

### Read models

- `GET /v1/tasks/{taskId}/revisions` — immutable revision history.
- `GET /v1/tasks/{taskId}/experience` — human-first task view.
- Existing task/intents/domain-event endpoints remain available for professional and compatibility views.

Task WebSocket/domain events include task revision, aggregate version, step ID, command ID where applicable, and a monotonic event cursor.

## Human-first task view

The server, not the browser, produces a versioned `task.experience.v1` projection containing:

- headline and plain-language understanding;
- current revision and update status;
- change preview;
- ordered step cards with status, explanation, assigned robot, capability label, and evidence statement;
- current recovery guidance;
- safe professional details;
- allowed user actions.

The browser renders all strings with text-only DOM APIs. Raw tool arguments, credentials, device tokens, bearer tokens, and unrestricted error strings never enter this view.

## Reconciliation and distributed consistency

The Coordinator/Harness revision reconciler follows these rules:

1. Persist the proposed revision with compare-and-swap on `aggregateVersion`.
2. Parse and plan against immutable tool-catalog, model, map/transform, and world revision references.
3. Match previously satisfied steps by semantic fingerprint and evidence validity.
4. Mark obsolete pending steps `CANCELLED_BY_REVISION`.
5. Do not revoke an in-flight physical command through a revision update. Wait for its declared safe checkpoint or issue the existing safety-stop protocol when required.
6. Acquire new resource grants only after conflicting prior grants are released or expired. New work uses a higher fencing token.
7. Activate the revision with one event-log compare-and-swap transaction.
8. Ignore delayed events from older revisions or lower fencing tokens.

Every update and command carries:

- task ID;
- task revision;
- aggregate/event version;
- step ID;
- command ID;
- idempotency key where user initiated;
- fencing token for guarded resources.

Leader failover reconstructs the same state from persisted task revisions, task events, coordinator domain events, and authoritative world observations. A duplicate update or command acknowledgement is idempotent.

## Harness and environment truth

Tool delivery, tool acknowledgement, and physical completion remain separate facts:

- Delivery means the command reached the adapter.
- Acknowledgement means the adapter accepted or completed its local call.
- Completion means the Harness observed the required postcondition in a sufficiently fresh authoritative world state.

A stale/missing world source moves the step to `AWAITING_EVIDENCE` or safe pause; it never produces success. The same observation, world-model identity, transform revision, resource ownership, and freshness contracts apply to RoboCasa and real robots.

## Failure behavior

- **Concurrent user update:** reject with the latest revision and an understandable “任务已被其他操作更新” preview.
- **Duplicate request:** return the original proposed/confirmed revision using the idempotency key.
- **Robot offline:** pause; reassign only when another robot has a compatible tool catalogue, map/transform, resource access, and safety capability.
- **Tool timeout:** keep the physical state unconfirmed, retry within policy, then offer guided recovery.
- **Tool success but no world change:** remain `AWAITING_EVIDENCE`; do not mark complete.
- **World source stale:** freeze factual progress and explain that the system is rechecking the environment.
- **Leader/process failure:** resume from event cursor without repeating satisfied steps or commands.
- **Revision conflicts with running motion:** wait for the declared safe checkpoint or require physical approval/safety stop.

## Storage and migration

- Add revision and aggregate-version fields to task persistence.
- Add a revision repository with immutable rows/documents keyed by `(taskId, revision)` and unique `(taskId, idempotencyKey)`.
- Persist task revision lifecycle events in the existing event-log boundary used by distributed coordination.
- Existing tasks are lazily/projected as revision 1 without rewriting their historical events.
- Local memory/SQLite stores and Fleet MySQL/event-log implementations expose identical revision semantics.

## Testing and acceptance

### Unit and contract tests

- revision creation, confirmation, status transitions, and immutability;
- compare-and-swap conflicts and idempotency;
- change-set generation and stable step matching;
- safe tool display metadata and secret filtering;
- task-experience schema and plain-language fallback;
- old task compatibility as revision 1.

### Coordinator/Harness tests

- update while a step is running;
- retain satisfied steps and replace pending steps;
- conflicting update waits for a safe checkpoint;
- duplicate and out-of-order revision/domain events;
- leader failover during proposal, confirmation, tool call, and evidence wait;
- resource fencing across revision changes;
- robot loss and compatible/incompatible reassignment;
- tool acknowledgement without environment change;
- stale observations, Harness timeout, retry, and recovery.

### Web tests

- mission rail remains beside the world and moves below it responsively;
- understanding, changes, steps, capability labels, and guided recovery use text-only rendering;
- professional details are collapsed by default;
- update preview exposes retained/changed/added/paused information;
- focus, keyboard, reduced-motion, and screen-reader status behavior;
- page refresh, reconnect, duplicate, equal, old, and gap revision handling;
- the default user surface does not expose raw JSON, tool codes, or secrets.

### End-to-end RoboCasa acceptance

One authenticated episode must prove:

1. create and approve a two-robot handoff;
2. submit a natural-language update while robot 1 is executing;
3. preserve the already confirmed step;
4. activate the new revision at a safe checkpoint;
5. show the exact human-first capability used at each step;
6. simulate delayed/duplicate events, robot disconnect, tool timeout, stale world evidence, and coordinator failover;
7. complete through Harness-observed physical postconditions;
8. refresh the Console and recover the same revision, step, and evidence view;
9. capture live, recovery, and visual-degraded states without confusing visual health with world/task truth.

## Rollout boundary

The first implementation targets the existing RoboCasa cloud Fleet flow and the shared task service. The local single-robot Console consumes the same revision and task-experience contracts. No real-robot protocol fork is introduced: simulation tools and real tools remain catalogue entries behind the same adapter/environment-observation boundary.
