# Layered Robot Runtime and Middleware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple Agent and Robot Runtime core logic from concrete storage, queues, protobuf, ROS 2, and hardware SDKs while keeping SQLite and in-memory infrastructure as the local-first default.

**Architecture:** Consumer-owned ports protect task and execution invariants; small generic middleware contracts cover queue, events, cache, locking, and traces. Concrete adapters live under `middleware/*`, while the gRPC service alone maps protobuf to semantic Python backend models.

**Tech Stack:** Go 1.26, SQLite via `modernc.org/sqlite`, Go generics, gRPC/protobuf, Python 3.11 dataclasses, ROS 2 Jazzy compatibility, pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-layered-runtime-middleware-design.md`

## Global Constraints

- Preserve the one-process laptop Local Agent and thin Raspberry Pi Robot Runtime topology.
- Do not add PostgreSQL, Redis, Kafka, Docker, or broker runtime dependencies.
- Core packages may depend on root middleware contracts, never concrete middleware adapters.
- Agent code never imports generated protobuf, ROS 2, robot SDK, SQL, Redis, or Kafka packages.
- Raw Camera, LiDAR, IMU, and Joint State streams remain robot-local; Agent models are bounded semantic snapshots.
- Safety, watchdogs, real-time control, and hardware protection remain deterministic and independent of LLM output.
- Existing design specifications and this plan remain durable repository assets.

---

### Task 1: Add middleware ports and a bounded memory queue

**Files:**
- Create: `middleware/contracts.go`
- Create: `middleware/memory/queue.go`
- Create: `middleware/memory/queue_test.go`
- Create: `middleware/memory/events.go`
- Create: `middleware/memory/events_test.go`

**Interfaces:**
- Produces: `middleware.Queue[T]`, `Publisher[T]`, `Subscription[T]`, `Cache`, `Locker`, `TraceStore`, `ExecutionStore`, `StepRecord`, and `StepStatus`.
- Produces: `memory.NewQueue[T](capacity int) *Queue[T]` and `memory.NewEventBus[T]() *EventBus[T]`.

- [ ] **Step 1: Write failing bounded queue and event fan-out tests**

```go
func TestQueueReturnsBackpressureWithoutBlocking(t *testing.T) {
    queue := NewQueue[string](1)
    if err := queue.Enqueue(context.Background(), "task-1"); err != nil { t.Fatal(err) }
    if err := queue.Enqueue(context.Background(), "task-2"); !errors.Is(err, middleware.ErrQueueFull) {
        t.Fatalf("second enqueue error = %v", err)
    }
}

func TestEventBusDeliversToEachSubscriber(t *testing.T) {
    bus := NewEventBus[string]()
    first := bus.Subscribe(1)
    second := bus.Subscribe(1)
    if err := bus.Publish(context.Background(), "TASK_APPROVED"); err != nil { t.Fatal(err) }
    assertReceive(t, first, "TASK_APPROVED")
    assertReceive(t, second, "TASK_APPROVED")
}
```

- [ ] **Step 2: Run tests and verify missing packages fail**

Run: `go test ./middleware/... -v`

Expected: FAIL because middleware contracts and adapters do not exist.

- [ ] **Step 3: Implement dependency-free contracts and memory adapters**

```go
type Queue[T any] interface {
    Enqueue(context.Context, T) error
    Dequeue(context.Context) (T, error)
    Close() error
}

type StepRecord struct {
    TaskID, StepID, IdempotencyKey string
}

type ExecutionStore interface {
    StepStatus(context.Context, string, string) (StepStatus, error)
    MarkStepStarted(context.Context, StepRecord) error
    MarkStepCompleted(context.Context, StepRecord) error
}
```

Use a mutex around queue close/enqueue so concurrent close cannot panic. Queue full returns `ErrQueueFull`; closed dequeue returns `ErrQueueClosed`; context cancellation returns `ctx.Err()`.

- [ ] **Step 4: Run middleware tests**

Run: `go test ./middleware/... -race -v`

Expected: PASS with bounded backpressure, clean close, context cancellation, and per-subscriber delivery.

- [ ] **Step 5: Commit middleware contracts**

```bash
git add middleware
git commit -m "feat: add pluggable middleware contracts"
```

### Task 2: Move SQLite behind middleware adapters

**Files:**
- Move: `edge/localstore/store.go` -> `middleware/sqlite/store.go`
- Move: `edge/localstore/tasks.go` -> `middleware/sqlite/tasks.go`
- Move: `edge/localstore/store_test.go` -> `middleware/sqlite/store_test.go`
- Move: `edge/localstore/tasks_test.go` -> `middleware/sqlite/tasks_test.go`
- Modify: `tasks/store.go`
- Modify: `tasks/service.go`
- Modify: `tasks/memory_store.go`

**Interfaces:**
- Renames: `tasks.Store` -> `tasks.Repository`.
- Produces: `sqlite.Open(path string) (*sqlite.Store, error)` implementing both `tasks.Repository` and `middleware.ExecutionStore`.

- [ ] **Step 1: Write failing adapter conformance and restart tests**

```go
var _ tasks.Repository = (*Store)(nil)
var _ middleware.ExecutionStore = (*Store)(nil)

func TestExecutionRecordSurvivesReopen(t *testing.T) {
    path := filepath.Join(t.TempDir(), "agent.db")
    store, _ := Open(path)
    record := middleware.StepRecord{TaskID: "task-1", StepID: "pick", IdempotencyKey: "pick-1"}
    if err := store.MarkStepStarted(context.Background(), record); err != nil { t.Fatal(err) }
    store.Close()
    reopened, _ := Open(path)
    status, err := reopened.StepStatus(context.Background(), "task-1", "pick")
    if err != nil || status != middleware.StepStarted { t.Fatalf("status=%s err=%v", status, err) }
}
```

- [ ] **Step 2: Run tests and verify the new adapter path is absent**

Run: `go test ./middleware/sqlite -v`

Expected: FAIL because `middleware/sqlite` is not implemented.

- [ ] **Step 3: Move and adapt SQLite implementation**

Keep the existing WAL database and schema. Rename `Status`, `MarkStarted`, and `MarkCompleted` to the `middleware.ExecutionStore` signatures. Keep task state plus task events in the same transaction.

- [ ] **Step 4: Rename the domain repository port**

```go
type Repository interface {
    Create(context.Context, *Task) error
    Get(context.Context, string) (*Task, error)
    Update(context.Context, *Task) error
    List(context.Context) ([]*Task, error)
}
```

Update `tasks.Service` and `MemoryStore` conformance without importing any adapter package.

- [ ] **Step 5: Run repository and adapter tests**

Run: `go test ./tasks ./middleware/sqlite -race -v`

Expected: PASS with task/event atomicity and execution state surviving close/reopen.

- [ ] **Step 6: Commit the adapter move**

```bash
git add tasks middleware/sqlite edge/localstore
git commit -m "refactor: move sqlite behind middleware ports"
```

### Task 3: Inject execution state and queue ports into the application

**Files:**
- Modify: `edge/agent/runner.go`
- Modify: `edge/agent/runner_test.go`
- Modify: `internal/localapp/app.go`
- Modify: `internal/localapp/app_test.go`
- Modify: `cmd/local-agent/main.go`
- Modify: `console/server_test.go`

**Interfaces:**
- Changes: `agent.NewRunner(store middleware.ExecutionStore, grounder Grounder, invoker runtime.Invoker) *Runner`.
- Changes: `localapp.New(service *tasks.Service, runner *agent.Runner, queue middleware.Queue[string]) *App`.

- [ ] **Step 1: Write failing tests using fake ports rather than SQLite/channel internals**

```go
func TestRunnerUsesExecutionStorePort(t *testing.T) {
    journal := newFakeExecutionStore()
    runner := NewRunner(journal, fakeGrounder{}, fakeInvoker{})
    _, err := runner.Run(context.Background(), approvedTask())
    if err != nil { t.Fatal(err) }
    if journal.completed["pick"] == false { t.Fatal("pick was not durably completed") }
}

func TestAppConsumesInjectedQueue(t *testing.T) {
    queue := memory.NewQueue[string](4)
    app := New(service, runner, queue)
    app.Start(ctx)
    // approval + Enqueue must reach the worker through the injected queue.
}
```

- [ ] **Step 2: Run tests and verify concrete dependencies reject the new constructors**

Run: `go test ./edge/agent ./internal/localapp -v`

Expected: FAIL because Runner requires concrete SQLite and App constructs its own channel.

- [ ] **Step 3: Introduce narrow Grounder and Invoker dependencies**

```go
type Grounder interface {
    Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error)
}

type Invoker interface {
    Invoke(context.Context, Command) (Result, error)
}
```

Runner converts validated taskgraph steps to semantic `runtime.Command`; it never passes taskgraph or storage types into the transport layer.

- [ ] **Step 4: Replace the Local App channel with `middleware.Queue[string]`**

Keep the existing queued/active de-duplication maps. Map `middleware.ErrQueueFull` to the existing public `localapp.ErrQueueFull`. `work` blocks on `Dequeue(ctx)` and exits on cancellation or queue close.

- [ ] **Step 5: Select adapters only in the composition root**

```go
store, err := sqlite.Open(filepath.Join(configuration.dataDir, "agent.db"))
queue := memory.NewQueue[string](64)
runner := agent.NewRunner(store, robot, robot)
application := localapp.New(service, runner, queue)
```

- [ ] **Step 6: Run application tests and demo**

Run: `go test ./edge/agent ./internal/localapp ./cmd/local-agent ./console -race -v && bash scripts/demo.sh`

Expected: PASS; demo ends in `SUCCEEDED` with no concrete middleware import in Runner or Local App.

- [ ] **Step 7: Commit port injection**

```bash
git add edge/agent internal/localapp cmd/local-agent console
git commit -m "refactor: inject runtime middleware ports"
```

### Task 4: Strengthen the semantic Robot Capability client

**Files:**
- Modify: `edge/runtime/runtime.go`
- Modify: `edge/runtime/runtime_test.go`
- Modify: `edge/robotclient/client.go`
- Modify: `edge/robotclient/client_test.go`
- Modify: `edge/robotclient/capability_test.go`
- Modify: `console/server.go`
- Modify: `console/server_test.go`

**Interfaces:**
- Produces: capability-name constants, `runtime.Command`, `runtime.Result`, `runtime.InfoProvider`, `runtime.Invoker`, and `runtime.Client`.
- Changes: robotclient `Execute(taskID, SkillStep)` -> `Invoke(runtime.Command)`.

- [ ] **Step 1: Write failing semantic command mapping tests**

```go
func TestInvokeMapsSemanticCommandToWireProtocol(t *testing.T) {
    command := runtime.Command{
        SchemaVersion: "robot.v1", CommandID: "task-1:pick", TaskID: "task-1",
        Capability: runtime.CapabilityPick, TargetRef: "cup-1",
        Parameters: map[string]any{"keepUpright": true},
        Deadline: time.UnixMilli(2_000), Lease: 500*time.Millisecond,
        IdempotencyKey: "pick-1", SafetyProfile: "desktop_standard", ApprovalID: "approval-1",
    }
    // fake gRPC server records the protobuf and returns SUCCEEDED.
    result, err := client.Invoke(context.Background(), command)
    if err != nil || !result.Success { t.Fatalf("result=%#v err=%v", result, err) }
    if recorded.Skill != "manipulation.pick" || recorded.CommandId != "task-1:pick" { t.Fatal(recorded) }
}
```

- [ ] **Step 2: Run runtime/client tests and verify semantic API is absent**

Run: `go test ./edge/runtime ./edge/robotclient -v`

Expected: FAIL because `runtime.Command`, capability constants, and `Invoke` are missing.

- [ ] **Step 3: Add stable semantic capability names**

```go
const (
    CapabilityGetState CapabilityName = "state.get"
    CapabilityNavigate CapabilityName = "navigation.navigate"
    CapabilityMoveArm CapabilityName = "arm.move"
    CapabilityPick CapabilityName = "manipulation.pick"
    CapabilityPlace CapabilityName = "manipulation.place"
    CapabilityEmergencyStop CapabilityName = "safety.emergency_stop"
)
```

Only existing advertised capabilities are executable; constants do not imply availability.

- [ ] **Step 4: Implement semantic command mapping in robotclient**

The adapter alone creates `robotv1.SkillCommand`, receives the event stream, and maps the one terminal event to `runtime.Result`. Preserve timeout, protocol-major validation, cancel, and E-stop behavior.

- [ ] **Step 5: Update Runner and Console to semantic provider interfaces**

Runner asks `InfoProvider.Info`; Console's runtime card uses the same interface. No consumer imports generated protobuf.

- [ ] **Step 6: Run Go runtime, Runner, Console, and contract tests**

Run: `go test ./edge/runtime ./edge/robotclient ./edge/agent ./console -race -v`

Expected: PASS with runtime capability validation before physical invocation.

- [ ] **Step 7: Commit the semantic client**

```bash
git add edge/runtime edge/robotclient edge/agent console
git commit -m "refactor: expose semantic robot capabilities"
```

### Task 5: Decouple Python RobotBackend from protobuf and ROS 2

**Files:**
- Create: `robot/gateway/tangying_robot_gateway/runtime.py`
- Create: `robot/gateway/tests/test_runtime_models.py`
- Modify: `robot/gateway/tangying_robot_gateway/backend.py`
- Modify: `robot/gateway/tangying_robot_gateway/service.py`
- Modify: `robot/gateway/tangying_robot_gateway/safety.py`
- Modify: `robot/gateway/tangying_robot_gateway/xlerobot_backend.py`
- Modify: `robot/ros2_ws/src/tangying_robot_gateway/tangying_ros_gateway/node.py`
- Modify: `robot/gateway/tests/test_service.py`
- Modify: `robot/gateway/tests/test_safety.py`
- Modify: `robot/gateway/tests/test_xlerobot_backend.py`
- Modify: `sim/mujoco/tangying_sim/server.py`

**Interfaces:**
- Produces semantic dataclasses: `Capability`, `RuntimeInfo`, `SemanticState`, `SceneEntity`, `Observation`, `Command`, and `Result`.
- Changes `RobotBackend` to consume/produce only those dataclasses.
- Keeps protobuf mapping exclusively in `RobotRuntimeService` and the simulator's gRPC service.

- [ ] **Step 1: Write failing backend contract tests**

```python
def test_backend_contract_uses_semantic_models():
    backend = RecordingBackend()
    service = RobotRuntimeService(backend)
    command = valid_proto_command()
    events = list(service.execute_for_test(command))
    assert isinstance(backend.last_command, runtime.Command)
    assert backend.last_command.parameters == {"action_chunk": [{"left_arm_1.pos": 1.0}]}
    assert events[-1].type == robot_pb2.SKILL_EVENT_SUCCEEDED

def test_runtime_model_contains_no_raw_sensor_streams():
    fields = {field.name for field in dataclasses.fields(runtime.Observation)}
    assert fields == {"observation_id", "observed_at_ms", "monotonic_time_ns", "entities", "robot_state", "semantic_state"}
```

- [ ] **Step 2: Run tests and verify semantic models are absent**

Run: `.venv/bin/python -m pytest robot/gateway/tests/test_runtime_models.py robot/gateway/tests/test_service.py -q`

Expected: FAIL because backend methods currently use protobuf types.

- [ ] **Step 3: Add immutable semantic backend dataclasses and mapper functions**

`Command.parameters` is a plain dictionary. `Observation` contains low-rate entities, robot state, and semantic state only. Protobuf conversion functions live in `service.py`.

- [ ] **Step 4: Migrate Safety Supervisor and direct backend**

Safety validates semantic command fields and plain action dictionaries. Direct XLeRobot backend returns semantic RuntimeInfo/Observation and never imports `robot_pb2` or protobuf JSON helpers.

- [ ] **Step 5: Migrate the ROS backend**

ROS backend consumes semantic Command and maps it to internal `ExecuteSkill` actions. ROS topics/actions remain entirely inside the ROS package.

- [ ] **Step 6: Remove the simulator's stale Pair method**

Delete the unreachable method referencing removed `PairRequest`/`PairResponse` messages. Keep the simulator's Robot Runtime protocol behavior unchanged.

- [ ] **Step 7: Run Python runtime, ROS source, safety, and simulation tests**

Run: `.venv/bin/python -m pytest robot/gateway/tests tests/contract sim/mujoco/tests tests/e2e -q`

Expected: PASS with no generated protobuf import in `backend.py`, `xlerobot_backend.py`, or the ROS backend.

- [ ] **Step 8: Commit backend decoupling**

```bash
git add robot/gateway robot/ros2_ws/src/tangying_robot_gateway sim/mujoco
git commit -m "refactor: decouple robot backends from grpc"
```

### Task 6: Enforce architecture boundaries and update durable documentation

**Files:**
- Create: `tests/architecture/dependencies_test.go`
- Modify: `tests/test_repository.py`
- Modify: `docs/architecture.md`
- Modify: `docs/protocols.md`
- Modify: `docs/agent-v1.md`
- Create: `docs/middleware.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Produces executable dependency rules based on `go list -json`.
- Produces current documentation linked to the governing specification and this plan.

- [ ] **Step 1: Write a failing rule test plus the real dependency-graph contract**

```go
func TestCorePackagesDoNotImportConcreteInfrastructure(t *testing.T) {
    packages := goList(t, "../../agent", "../../orchestration", "../../tasks", "../../core/...", "../../edge/agent", "../../edge/runtime")
    forbidden := []string{"middleware/sqlite", "middleware/postgres", "middleware/redis", "middleware/kafka", "database/sql", "gen/go/robot"}
    for _, pkg := range packages {
        for _, imported := range pkg.Imports {
            if containsForbidden(imported, forbidden) { t.Errorf("%s imports %s", pkg.ImportPath, imported) }
        }
    }
}

func TestForbiddenImportsReportsConcreteAdapter(t *testing.T) {
    violations := forbiddenImports("example/core", []string{"example/middleware/sqlite"})
    if len(violations) != 1 { t.Fatalf("violations = %#v", violations) }
}
```

- [ ] **Step 2: Run the architecture test and verify existing concrete imports fail**

Run: `go test ./tests/architecture -v`

Expected: FAIL because the dependency-rule parser does not exist. After implementation, the synthetic rule test proves the guard can fail and the real graph must report no violations.

- [ ] **Step 3: Complete dependency cleanup and add repository behavior checks**

Use `go list`, not source-text assertions, for Go import enforcement. Repository checks verify design assets exist, the old `edge/localstore` package is absent, and no PostgreSQL/Redis/Kafka module dependency is present.

- [ ] **Step 4: Rewrite current architecture and middleware docs**

Document the six layers, data/control flows, current adapters, future adapter rules, and why Middleware is horizontal rather than a serial robot command hop. Link both governing design specs and plans.

- [ ] **Step 5: Run complete verification**

Run:

```bash
make generate-check
go test ./...
.venv/bin/python -m pytest -q
make lint
make build
make install-check
bash scripts/demo.sh
.venv/bin/python scripts/run_simulation_acceptance.py --episodes 30 --seed 20260818
```

Expected: every command exits 0; demo reaches `SUCCEEDED`; acceptance reports 30/30 episodes, 18/18 matrix goals, and zero safety violations.

- [ ] **Step 6: Mark the plan delivered and commit**

```bash
git add README.md CHANGELOG.md docs tests
git commit -m "docs: finalize layered robot agent architecture"
```
