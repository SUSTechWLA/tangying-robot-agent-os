# Local-First Robot Agent Runtime Design

**Date:** 2026-08-18
**Status:** Approved

## 1. Goal

Turn Tangying Robot Agent OS into a local-first product that a user can install on one laptop, configure with an OpenAI-compatible LLM API, pair with one Raspberry Pi robot, and operate without a hosted control plane or deployment middleware.

The laptop is the only business-state authority. The Raspberry Pi remains a thin, fail-closed robot runtime that owns hardware access, command validation, watchdog behavior, cancellation, and the latched emergency stop.

## 2. Product Experience

The primary installation and daily workflow is:

```text
install desktop Local Agent
  -> open local Console
  -> configure LLM API endpoint, key, and model
  -> discover or enter a Raspberry Pi address
  -> pair over SSH once
  -> create, approve, execute, and inspect tasks locally
```

The desktop service binds to `127.0.0.1` by default and serves both the API and Console. The only expected internet dependency is the user's selected LLM API. The deterministic parser remains available when the LLM cannot be reached.

## 3. Non-Goals

- Multi-tenant cloud accounts, hosted task history, remote fleet management, and public API exposure are not part of the first local-first release.
- PostgreSQL, Redis, Kafka, MQTT, Kubernetes, and a cloud message broker are not required.
- ROS 2 is not on the default XLeRobot path. Existing ROS 2 packages may remain as an optional compatibility directory until a later removal decision.
- The LLM never produces raw protocol messages or bypasses the deterministic command compiler and safety checks.
- The Raspberry Pi does not store natural-language requests, full task plans, LLM prompts, API keys, or user history.

## 4. Target Architecture

```text
User / browser
  -> Laptop Local Agent (one Go process)
       - local HTTP API and embedded Console
       - LLM intent parser and planner
       - deterministic fallback planner
       - task state machine and approval
       - grounding, policy integration, and execution loop
       - telemetry and event fan-out
       - SQLite task, event, settings, robot, and step state
       - mTLS Robot Runtime client
       -> OpenAI-compatible LLM API
       -> optional local perception/policy providers
       -> mTLS gRPC
            Raspberry Pi Robot Runtime (one Python service)
              - capability and health reporting
              - bounded observations
              - deterministic Safety Supervisor
              - bounded action-chunk execution
              - command watchdog, cancel, and latched E-stop
              - minimal idempotency and safety journal
              - XLeRobot USB driver
```

The cloud control plane is removed from the runtime topology. Former cloud packages that contain reusable agent, planner, task, API, or storage logic are moved or renamed according to their local responsibility.

## 5. Laptop Component Boundaries

### 5.1 `internal/localapp`

Owns process startup and shutdown. It loads configuration, opens SQLite, constructs the LLM parser and planner, connects the robot client, starts the task executor, and serves the loopback HTTP API. It is the only composition root.

### 5.2 `agent`

Owns OpenAI-compatible LLM calls and deterministic language parsing. It returns validated domain intents only. Provider failures fall back to deterministic parsing when the request is supported; otherwise they produce an explicit local task error.

### 5.3 `orchestration`

Turns validated intents and the capability catalog into task-plan templates. It validates every model-produced tool and argument against the installed catalog. Safety fields are excluded from the model schema.

### 5.4 `tasks`

Owns task creation, approval, transitions, events, cancellation, and metrics. It has no lease-claim API because there is exactly one local executor. Every mutation is persisted before it is broadcast to the Console.

### 5.5 `runtime`

Owns the single local execution queue. It grounds a task, materializes safety fields, refreshes robot capabilities, invokes one step at a time, records step state, and reconciles interrupted executions on restart.

Only one physical task may execute at a time per paired robot. Read-only observations may continue while no physical command is active.

### 5.6 `robot/client`

Is the only package that knows the gRPC wire protocol. Agent, orchestration, task, Console, and storage code depend on semantic runtime interfaces rather than generated protobuf types.

### 5.7 `storage/sqlite`

Implements all desktop persistence in one WAL-mode database. It provides transaction boundaries for task state plus events and for step state plus command identity.

### 5.8 `console`

Serves the embedded web application, a local JSON API, and WebSocket/SSE event updates. It never calls the robot directly; every operation goes through the task or runtime application service.

## 6. Desktop Persistence

The existing SQLite step store becomes the single application database. Its logical schema contains:

### `settings`

- `key TEXT PRIMARY KEY`
- `value BLOB NOT NULL`
- `sensitive INTEGER NOT NULL`
- `updated_at TEXT NOT NULL`

API keys are stored through an operating-system credential adapter when available. The database stores only the credential reference. A file-backed `0600` fallback is allowed for supported headless Linux installations and is clearly reported by `doctor`.

### `robots`

- `id TEXT PRIMARY KEY`
- `name TEXT NOT NULL`
- `address TEXT NOT NULL`
- `server_name TEXT NOT NULL`
- `ca_path TEXT NOT NULL`
- `cert_path TEXT NOT NULL`
- `key_path TEXT NOT NULL`
- `certificate_fingerprint TEXT NOT NULL`
- `last_seen_at TEXT`
- `created_at TEXT NOT NULL`

### `tasks`

- `id TEXT PRIMARY KEY`
- `request TEXT NOT NULL`
- `adapter TEXT NOT NULL`
- `intent_json BLOB NOT NULL`
- `plan_json BLOB NOT NULL`
- `state TEXT NOT NULL`
- `approved INTEGER NOT NULL`
- `approval_id TEXT`
- `error_code TEXT`
- `error_message TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

### `task_events`

- `task_id TEXT NOT NULL`
- `sequence INTEGER NOT NULL`
- `type TEXT NOT NULL`
- `step_id TEXT`
- `message TEXT`
- `payload_json BLOB`
- `occurred_at TEXT NOT NULL`
- primary key `(task_id, sequence)`

### `step_runs`

- `task_id TEXT NOT NULL`
- `step_id TEXT NOT NULL`
- `command_id TEXT NOT NULL`
- `idempotency_key TEXT NOT NULL`
- `command_fingerprint TEXT NOT NULL`
- `status TEXT NOT NULL`
- `result_json BLOB`
- `updated_at TEXT NOT NULL`
- primary key `(task_id, step_id)`
- unique `idempotency_key`

Task state and its corresponding event are written in one transaction. A step is marked started before network dispatch and completed only after a terminal robot event is persisted.

## 7. Local HTTP API

The Console retains stable task-oriented endpoints where useful:

- `GET /healthz`
- `GET /v1/config/status`
- `PUT /v1/config/llm`
- `GET /v1/robots`
- `POST /v1/robots/pair`
- `GET /v1/runtime`
- `GET /v1/telemetry`
- `POST /v1/tasks`
- `GET /v1/tasks`
- `GET /v1/tasks/{id}`
- `POST /v1/tasks/{id}/approve`
- `POST /v1/tasks/{id}/cancel`
- `GET /v1/tasks/{id}/events`
- `GET /v1/events/ws`
- `GET /v1/orchestration/metrics`

The following distributed-control endpoints are removed:

- agent claim
- task lease renewal
- remote telemetry publication
- remote task state mutation
- remote event append

Task execution is scheduled directly after local creation or approval. Physical tasks enter `WAITING_APPROVAL`; read-only tasks may run immediately.

## 8. Laptop-to-Raspberry-Pi Protocol

The laptop always initiates a direct TLS 1.3 mutual-authentication gRPC connection. The Raspberry Pi never needs to connect back to the laptop and no relay is required.

The runtime service is:

```protobuf
service RobotRuntime {
  rpc GetRuntimeInfo(GetRuntimeInfoRequest) returns (RuntimeInfo);
  rpc Observe(ObserveRequest) returns (stream Observation);
  rpc ExecuteSkill(SkillCommand) returns (stream SkillEvent);
  rpc Cancel(CancelRequest) returns (CancelResult);
  rpc EmergencyStop(EStopRequest) returns (EStopResult);
}
```

The unused network `Pair` RPC and `PolicyInference` service are removed. Pairing is a deployment bootstrap operation and policy inference is a laptop-local capability.

### 8.1 Runtime information

`RuntimeInfo` includes robot identity, adapter, runtime and protocol versions, driver/calibration readiness, current semantic state, blockers, and structured capability descriptors.

### 8.2 Observations

`Observe` supports named streams and a caller-provided maximum rate. Semantic state and entities are low-rate. Compressed images are opt-in, bounded in size, and rate-limited. Raw high-frequency joint control remains local to the Raspberry Pi driver.

### 8.3 Execution

The laptop runs perception and policy inference, then sends a high-level skill with an optional bounded `action_chunk`. A chunk normally represents 200-1000 milliseconds of motion. The Raspberry Pi executes the chunk at the driver's local control frequency.

Every physical command contains locally generated:

- schema and protocol version
- task and command ID
- idempotency key and deterministic command fingerprint
- target reference and validated parameters
- approval ID
- absolute deadline
- short execution lease
- safety profile

Model output cannot set or override these fields.

### 8.4 Runtime safety journal

The Raspberry Pi persists only a bounded safety journal containing the emergency-stop latch and the most recent command fingerprints and terminal outcomes. The journal prevents a laptop reconnect or process restart from blindly replaying an uncertain physical command. It is not a task database.

## 9. Pairing and Discovery

The desktop attempts mDNS discovery using `_tangying-robot._tcp.local` and accepts a manual hostname or IP fallback.

Initial pairing uses SSH because it provides an inspectable host-key confirmation and a practical secure bootstrap channel:

1. The user selects a discovered robot or enters an address.
2. The desktop invokes the existing SSH pairing workflow.
3. A laptop-owned CA signs a robot server certificate and a laptop client certificate.
4. Only the robot server key, server certificate, and client CA are installed on the Raspberry Pi.
5. The CA private key and laptop client key never leave the laptop.
6. The desktop verifies the gRPC server identity and displays the certificate fingerprint.

Daily operation uses gRPC only. Certificate expiry is surfaced in the Console and `doctor`; renewal repeats the SSH bootstrap deliberately.

## 10. Process Lifecycle

### Laptop startup

1. Load non-secret configuration and credential references.
2. Open and migrate SQLite.
3. Reconcile any task or step left in an in-progress state.
4. Start the robot connection manager.
5. Start the single physical execution worker.
6. Start the loopback HTTP server and Console.

The Console remains usable when the robot or LLM provider is offline. It reports each dependency independently.

### Raspberry Pi startup

1. Load the persisted E-stop and command safety journal.
2. Open the configured serial devices.
3. Load and validate calibration.
4. initialize the driver in a non-moving state.
5. Start the mTLS gRPC runtime.
6. Advertise readiness only after every required safety check succeeds.

A restart never clears an E-stop or automatically resumes motion.

## 11. Failure and Recovery Semantics

### Laptop-to-robot disconnect

The Raspberry Pi stops the active action when its short lease expires. The laptop records an uncertain/recoverable step and, after reconnecting, compares its step record with the Pi safety journal. It either accepts the recorded terminal outcome, runs verification, or asks the user to intervene. It never automatically repeats an uncertain physical action.

### Laptop sleep or Local Agent crash

The robot stops at lease expiry. On restart the Local Agent performs reconciliation before accepting another physical task.

### LLM provider failure

Supported requests fall back to the deterministic parser/planner. Unsupported requests remain local failures with a clear provider error and do not reach the robot.

### SQLite failure

No new physical command is dispatched if the step-start transaction cannot be committed. A failure to persist a terminal event stops further execution and requires recovery.

### Cancel

Cancel requests a controlled stop of one active command and leaves the robot recoverable when the driver confirms the stop.

### Emergency stop

Emergency stop immediately asks the driver to stop and persists a latch. Failure inside the driver stop path does not clear the logical latch. Remote APIs never expose clear/reset. A physically present operator must clear the condition locally.

### Version mismatch

The laptop compares protocol major versions before execution. A major mismatch permits health display but blocks physical commands with a clear upgrade message.

## 12. Security Boundary

- The local HTTP server binds to loopback by default.
- gRPC uses TLS 1.3 mutual authentication outside explicit simulation mode.
- LLM API keys never enter prompts, logs, task events, or the Raspberry Pi.
- LLM output is parsed into domain types and validated against the installed capability catalog.
- The local command compiler owns approval, deadline, lease, idempotency, and safety fields.
- The Raspberry Pi independently validates all motion-related fields and action values.
- Mobile-base velocity keys remain disabled for the desktop manipulation profile.
- Remote E-stop is allowed; remote E-stop reset is forbidden.

## 13. Removal and Migration

### Remove from the default product

- `cmd/cloud-control-plane`
- PostgreSQL task storage and schema
- cloud Docker image and Docker Compose deployment
- cloud installer role and cloud environment template
- `edge/cloudclient`
- claim and lease-renewal loops
- cloud telemetry publication
- separate cloud and local service documentation

### Reuse locally

- LLM parser and planner
- task state machine and task events
- orchestration metrics
- embedded Console
- SQLite step recovery
- semantic Robot Runtime interface
- gRPC robot client
- deterministic Safety Supervisor
- XLeRobot direct backend and driver
- MuJoCo implementation of the same robot protocol

### Compatibility

The first local-first release does not promise wire compatibility with the cloud control-plane API. Robot protocol changes retain one transitional generated-code cycle and explicit protocol version reporting. Existing ROS 2 directories remain buildable where practical but are excluded from default install, tests, and documentation.

The current uncommitted v1 work is treated as source material, not discarded wholesale. Agent, orchestration, runtime-capability, telemetry, direct-backend, safety, simulation, and Console improvements are migrated into the local-first boundaries.

## 14. Installation and Operations

The supported roles become:

- `local`: laptop Local Agent, SQLite, Console, credentials, and user service
- `robot-pi`: thin direct Robot Runtime, XLeRobot dependencies, driver, safety journal, and system service
- `sim`: local development installation with MuJoCo and the Local Agent

`cloud` is rejected with a migration message rather than silently installing obsolete services.

The primary commands are:

```text
robot-agent configure local
robot-agent pair ROBOT_HOST --ssh-user USER
robot-agent doctor local
robot-agent doctor robot-pi
robot-agent start local
robot-agent stop local
robot-agent status local
robot-agent logs local --follow
robot-agent production-check robot-pi
robot-agent demo
```

Installation preserves the SQLite database, certificates, and configuration during upgrades.

## 15. Testing Strategy

### Unit tests

- SQLite migrations, transactions, task/event ordering, and step idempotency
- local task creation, approval, transitions, cancellation, and restart reconciliation
- LLM configuration and deterministic fallback behavior
- command compiler ownership of all safety fields
- robot protocol mapping and version checks
- Raspberry Pi command validation, journal persistence, watchdog, cancellation, and E-stop persistence

### Contract tests

- Local API no longer exposes distributed claim, lease, state mutation, or telemetry publication
- gRPC mTLS requirements and protocol-version compatibility
- bounded image and action-chunk limits
- repeated command identity returns the recorded result; identity conflicts fail closed

### Integration tests

- one-process Local Agent with in-process MuJoCo runtime
- task create -> approval -> execute -> verify -> persisted terminal state
- Local Agent restart during planning and between steps
- simulated network loss during a physical chunk
- LLM failure with deterministic fallback
- browser Console loading and receiving local task events

### Acceptance tests

- `./install.sh sim --yes && robot-agent demo` completes without Docker or PostgreSQL
- `./install.sh local --yes` installs one user service and opens a healthy local Console
- a paired Raspberry Pi reports capabilities and refuses an unapproved or expired command
- laptop disconnect causes bounded-time motion stop
- E-stop remains latched across Raspberry Pi service restart
- the full Go and Python suites, lint checks, and simulation acceptance pass

## 16. Design Documentation as a Product Asset

Architecture specifications, implementation plans, protocol notes, safety decisions, and migration records are durable development assets. Architecture simplification must not delete them merely because the corresponding runtime is removed.

- `docs/superpowers/specs/` retains approved design snapshots.
- `docs/superpowers/plans/` retains the implementation plans that explain how each design was delivered.
- `docs/architecture.md` describes the current supported architecture and links back to the governing specification.
- A superseded document receives a visible status notice and a link to its replacement; its historical content remains available.
- New protocol or safety decisions that materially change a boundary are recorded before implementation.
- Repository and documentation tests verify that the current architecture, installation guide, and governing design links remain consistent.

This local-first design and its implementation plan remain in the repository after the migration is complete.

## 17. Delivery Sequence

The migration is implemented in testable vertical slices:

1. Add a durable SQLite task store and local application service.
2. Run API, Console, orchestration, execution, and telemetry in one Local Agent process.
3. Remove cloud claim/lease behavior and replace it with a local execution queue.
4. Tighten the Robot Runtime protocol, safety journal, and restart semantics.
5. Move policy/action-chunk production to the laptop boundary.
6. Simplify install roles, services, configuration, and documentation.
7. Delete obsolete cloud runtime code after all behavior is covered locally.
8. Run complete simulation, protocol, installer, and repository verification.

Each slice leaves a runnable local simulation path. Physical execution remains fail-closed throughout the migration.
