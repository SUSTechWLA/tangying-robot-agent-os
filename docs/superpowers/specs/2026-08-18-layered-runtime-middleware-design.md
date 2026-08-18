# Layered Robot Runtime and Pluggable Middleware Design

**Date:** 2026-08-18  
**Status:** Approved for implementation under the user's autonomous-execution authorization  
**Builds on:** `docs/superpowers/specs/2026-08-18-local-first-runtime-design.md`

## 1. Goal

Strengthen Tangying Robot Agent OS into a layered system where:

- the Agent makes semantic decisions;
- Robot Runtime exposes reliable robot capabilities and low-rate semantic state;
- Middleware supplies replaceable infrastructure without leaking vendor SDKs into core logic;
- ROS 2 remains an internal robotics integration bus;
- real-time control and hardware protection remain deterministic and independent of the LLM.

The upgrade must preserve the local-first product: SQLite and in-memory adapters remain the default, while PostgreSQL, Redis, Kafka, or other infrastructure can be added later without changing Agent business logic.

## 2. Current Problems

The existing system already has a useful `edge/runtime` capability boundary and a thin gRPC Robot Runtime, but several dependencies still cross intended layers:

1. `edge/agent.Runner` depends on concrete `edge/localstore.Store` and its SQLite-oriented execution-state types.
2. `internal/localapp` owns a concrete Go channel instead of depending on a queue port.
3. task persistence is abstract at the service boundary, but the concrete SQLite adapter lives under `edge`, obscuring that it is middleware rather than robot-edge code.
4. there is no explicit shared contract for cache, event publication, stream/queue, locking, or trace storage, so future implementations risk being called directly from Agent code.
5. Python `RobotBackend` implementations accept and return generated protobuf types, coupling ROS 2 and direct hardware adapters to the gRPC wire contract.
6. the simulator contains a stale `Pair` method for messages no longer present in the protocol.
7. architecture constraints are described in prose but not checked through the Go package dependency graph.

## 3. Selected Approach

Use ports and adapters with consumer-owned domain repositories plus small shared middleware primitives.

```text
User / Console
  -> Agent + Task Application Services
       -> Robot Runtime semantic port
       -> TaskRepository port
       -> ExecutionStore port
       -> Queue / EventPublisher / Cache / Locker / TraceStore ports
            -> middleware/sqlite (default persistence)
            -> middleware/memory (default queue/events; tests)
            -> future middleware/postgres
            -> future middleware/redis
            -> future middleware/kafka
       -> gRPC Robot Runtime adapter
            -> Robot Runtime service + Safety Supervisor
                 -> semantic RobotBackend port
                      -> ROS2 backend OR direct robot SDK backend
                           -> real-time controller / driver
                                -> hardware
```

Middleware is a horizontal infrastructure dependency of the application and Runtime. It is not a mandatory serial hop between Robot Runtime and ROS 2.

## 4. Layer Responsibilities

### 4.1 Agent and orchestration

Owns:

- natural-language intent parsing;
- task decomposition and semantic capability selection;
- plan validation and recovery decisions;
- operator approval workflow;
- low-frequency state interpretation.

Must not:

- import SQL, Redis, Kafka, ROS 2, gRPC generated types, robot SDKs, serial drivers, or GPIO libraries;
- generate raw ROS messages;
- run high-frequency control loops;
- set authoritative deadline, lease, idempotency, or safety fields through model output.

### 4.2 Robot Runtime / Capability

The Go `edge/runtime` package is the stable Agent-facing semantic port. It owns:

- well-known capability identifiers such as `state.get`, `navigation.navigate`, `arm.move`, `manipulation.pick`, `manipulation.place`, and `safety.emergency_stop`;
- runtime information and low-rate semantic state;
- semantic command, result, cancel, and E-stop operations;
- protocol compatibility and current-capability validation.

Only a transport adapter such as `edge/robotclient` maps these values to protobuf/gRPC.

The first release advertises only capabilities actually implemented. Constants for future semantic capabilities define naming policy; they do not claim runtime availability.

### 4.3 Middleware abstraction

The root `middleware` package contains small infrastructure contracts and dependency-free data types:

- `Queue[T]`: bounded application work delivery;
- `Publisher[T]` and `Subscription[T]`: transient local event fan-out;
- `Cache`: optional expiring byte-value cache;
- `Locker`: optional coordination lease;
- `TraceStore`: append/query trace events;
- `ExecutionStore`: durable step-start/step-complete recovery state.

Domain-specific task persistence remains a `tasks.Repository` owned by the task application because a generic CRUD store would leak persistence concepts and weaken domain invariants.

Current adapters:

- `middleware/sqlite`: implements `tasks.Repository` and `middleware.ExecutionStore` in the same local database;
- `middleware/memory`: implements the local task queue and event publisher; it may also contain test-only repository adapters.

PostgreSQL, Redis, and Kafka are not added as dependencies now. Future adapters follow these mappings:

| Infrastructure | Expected ports | Constraint |
| --- | --- | --- |
| PostgreSQL | `tasks.Repository`, `ExecutionStore`, `TraceStore` | SQL/driver imports stay inside `middleware/postgres` |
| Redis | `Cache`, `Locker`, optional Queue | Redis SDK imports stay inside `middleware/redis` |
| Kafka | Publisher/Subscription or stream adapter | Kafka SDK imports stay inside `middleware/kafka`; durable task state is not inferred from Kafka alone |

The composition root is the only code that selects adapters.

### 4.4 ROS 2

ROS 2 is internal to the robot computer. It integrates perception, SLAM, navigation, arm actions, `ros2_control`, and ecosystem packages. The Agent does not see Topic, Service, Action, QoS, node, or message types.

The ROS backend converts semantic Runtime backend commands to ROS actions and fuses ROS sensor outputs into low-rate semantic observations. Camera, LiDAR, IMU, joint state, and control-frequency data remain inside robot processes.

### 4.5 Real-time controller and hardware

The driver/controller owns servo-rate loops, trajectory interpolation, current/position/velocity bounds, watchdog stop, calibration, and hardware faults. LLM or Agent latency never gates the real-time loop.

Safety Supervisor independently validates every physical command, enforces approval/deadline/short lease/idempotency/value bounds, and retains the emergency-stop latch. Remote reset remains forbidden.

## 5. Go Interfaces and Dependency Direction

### 5.1 Middleware contracts

```go
package middleware

type Queue[T any] interface {
    Enqueue(context.Context, T) error
    Dequeue(context.Context) (T, error)
    Close() error
}

type Publisher[T any] interface {
    Publish(context.Context, T) error
}

type ExecutionStore interface {
    StepStatus(context.Context, taskID, stepID string) (StepStatus, error)
    MarkStepStarted(context.Context, StepRecord) error
    MarkStepCompleted(context.Context, StepRecord) error
}
```

Cache, lock, and trace contracts follow the same small-interface rule. No contract mentions a vendor, network topology, SQL query, Redis keyspace, or Kafka partition.

### 5.2 Task repository

```go
package tasks

type Repository interface {
    Create(context.Context, *Task) error
    Get(context.Context, string) (*Task, error)
    Update(context.Context, *Task) error
    List(context.Context) ([]*Task, error)
}
```

`tasks.Service` depends only on this interface. State mutations and their audit events remain one adapter transaction.

### 5.3 Runtime client

```go
package runtime

type Client interface {
    Info(context.Context) (Info, error)
    Observe(context.Context, ObservationQuery) (Observation, error)
    Invoke(context.Context, Command) (Result, error)
    Cancel(context.Context, commandID, reason string) (bool, error)
    EmergencyStop(context.Context, reason string) error
}
```

Grounding is an Agent concern expressed as a separate `Grounder` interface. `edge/agent.Runner` depends on `Grounder`, `runtime.Client`, and `middleware.ExecutionStore`, never on the gRPC client or SQLite adapter types.

## 6. Python Runtime Backend Boundary

Create transport-neutral dataclasses in `robot/gateway/tangying_robot_gateway/runtime.py`:

- `Capability`;
- `RuntimeInfo`;
- `SemanticState`;
- `SceneEntity`;
- `Observation`;
- `Command`;
- `Result`.

`RobotBackend` consumes/produces only these models. `RobotRuntimeService` is the sole protobuf mapper. ROS 2 and direct XLeRobot backends therefore remain stable if gRPC fields or protobuf generation change.

The Safety Supervisor consumes the same semantic `Command`. It stays below the Agent and above the backend/driver, independent of both LLM and ROS transport.

## 7. Data Flow

### Task creation and execution

1. Agent parses intent and selects semantic capabilities from the catalog.
2. `tasks.Service` persists the task through `tasks.Repository`.
3. approval enqueues the task through `middleware.Queue[string]`.
4. Local App dequeues one task and invokes `edge/agent.Runner`.
5. Runner refreshes Runtime info, grounds against a low-rate observation, validates availability, and creates deterministic safety controls.
6. `runtime.Client.Invoke` sends a semantic command through the gRPC adapter.
7. Robot Runtime maps protobuf to semantic backend command, runs Safety Supervisor, and calls ROS 2 or the direct SDK backend.
8. driver/controller executes the bounded action at its local frequency.
9. terminal state is durably recorded before the next physical step.

### Sensor and state flow

```text
Camera / LiDAR / IMU / Joint State (high rate)
  -> robot-local perception / state estimation / SLAM / controller
  -> fused Robot Observation + Semantic State (bounded, low rate)
  -> Robot Runtime Observe
  -> Agent grounding, recovery, Console telemetry
```

Raw control-frequency streams are never a Middleware event topic for the Agent.

## 8. Failure Semantics

- SQLite write failure prevents dispatch of a new physical command.
- queue closure stops the local worker cleanly; queue full returns a stable backpressure error.
- event fan-out failure does not roll back committed task state; the repository remains authoritative.
- cache miss is normal and must not affect correctness.
- lock/coordination is unused in the single-user default; future distributed adapters must use fencing tokens before physical coordination is allowed.
- middleware adapter startup failure is reported before the Local Agent accepts tasks.
- Robot Runtime disconnect lets the short robot lease expire; uncertain physical steps are never auto-replayed.
- ROS 2 or SDK failures become semantic Runtime error codes and do not expose Python/ROS exception types to the Agent.

## 9. Architecture Enforcement

Repository tests use `go list -json` to inspect real package imports. Core packages (`agent`, `orchestration`, `tasks`, `core/*`, `edge/agent`, `edge/runtime`) may import root middleware contracts but must not import:

- `middleware/sqlite`, `middleware/postgres`, `middleware/redis`, `middleware/kafka`;
- `database/sql` or concrete database drivers;
- Redis or Kafka SDKs;
- generated robot protobuf packages;
- ROS 2 or robot SDK packages.

The composition root and adapter packages are exempt because selecting and implementing infrastructure is their responsibility.

Python tests verify that `RobotBackend`, ROS backend, and XLeRobot direct backend use semantic runtime models instead of protobuf messages.

## 10. Migration Sequence

1. add middleware contracts and tested in-memory queue;
2. move SQLite implementation from `edge/localstore` to `middleware/sqlite` and rename task `Store` to `Repository`;
3. inject Queue and ExecutionStore into Local App and Runner;
4. strengthen Go Robot Runtime `Client`, semantic command/state models, and robotclient mapping;
5. introduce Python semantic backend models and migrate service, safety, direct, and ROS backends;
6. add package-boundary enforcement and update durable architecture documents;
7. run complete tests, lint, builds, local demo, and simulation acceptance.

Every slice keeps the local SQLite + memory product runnable. No PostgreSQL, Redis, Kafka, broker, or new network service is required for the default install.

## 11. Acceptance Criteria

- Agent/task/runtime core packages compile without importing any concrete middleware adapter or vendor SDK.
- `edge/agent.Runner` accepts interfaces and no longer references SQLite types.
- Local App uses an injected queue port with bounded memory as the default.
- SQLite is visibly located and selected as a middleware adapter.
- task and step state survive process restart exactly as before.
- Robot Runtime capabilities remain semantic and include naming constants for navigation, arm, manipulation, state, and safety domains.
- Python backend implementations no longer use generated protobuf types.
- ROS 2 remains optional and internal; the direct backend still works without ROS 2.
- raw high-rate sensors are absent from Agent-facing models.
- there are no PostgreSQL, Redis, or Kafka runtime dependencies in the default product.
- full Go/Python tests, lint, build, installer checks, demo, and 30-episode simulation acceptance pass.

## 12. Durable Design Assets

This specification, the implementation plan derived from it, the earlier local-first specification, and current architecture documentation remain versioned repository assets. Future middleware adapters or Robot Runtime protocol changes must update the current architecture and link to a new decision record rather than erasing prior design history.
