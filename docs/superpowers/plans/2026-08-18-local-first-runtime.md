# Local-First Robot Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hosted control-plane topology with one laptop Local Agent process backed by SQLite and a thin, fail-closed Raspberry Pi Robot Runtime.

**Status:** Delivered and verified on 2026-08-18. This plan is retained as a durable development-design and migration record.

**Architecture:** The laptop composes task persistence, LLM orchestration, approval, execution, telemetry, and the embedded Console in one process. It connects directly to the Raspberry Pi over mTLS gRPC; the Pi retains only bounded command execution, hardware safety, watchdogs, and a small safety journal.

**Tech Stack:** Go 1.24, `net/http`, SQLite via `modernc.org/sqlite`, gRPC/protobuf, Python 3.11, grpcio, pytest, embedded HTML/CSS/JavaScript, launchd/systemd.

**Spec:** `docs/superpowers/specs/2026-08-18-local-first-runtime-design.md`

## Global Constraints

- The laptop is the only business-state authority.
- The default desktop service binds to `127.0.0.1` and requires no hosted middleware.
- The only expected internet dependency is an OpenAI-compatible LLM API selected by the user.
- The deterministic parser remains available when the LLM provider is unavailable.
- The Raspberry Pi stores no prompts, API keys, natural-language requests, full plans, or user history.
- Physical commands remain fail-closed and require deterministic approval, deadline, lease, idempotency, and safety fields.
- Remote E-stop reset is forbidden; the E-stop latch survives a Raspberry Pi service restart.
- Existing approved specifications and implementation plans remain durable repository assets.
- Preserve the current uncommitted v1 agent, runtime, simulation, safety, direct-backend, telemetry, and Console work while changing its deployment boundary.

## File Structure

- `edge/localstore/store.go`: open and migrate the unified desktop SQLite database.
- `edge/localstore/tasks.go`: implement durable task, event, and step persistence.
- `internal/localapp/app.go`: compose and own the local task execution worker.
- `console/server.go`: expose only loopback-local product APIs and the embedded Console.
- `cmd/local-agent/main.go`: start the one-process desktop product.
- `proto/robot/v1/robot.proto`: define the thin Robot Runtime protocol.
- `edge/runtime/runtime.go`: keep the Agent-facing semantic robot contract.
- `edge/robotclient/client.go`: map semantic calls to the gRPC protocol.
- `robot/gateway/tangying_robot_gateway/journal.py`: persist bounded Pi safety state.
- `robot/gateway/tangying_robot_gateway/service.py`: enforce journaled idempotency and stop behavior.
- `robot/gateway/tangying_robot_gateway/run_direct_edge.py`: start the one Pi runtime service.
- `scripts/install/local.sh`: install one laptop service and local Console.
- `scripts/install/robot-pi.sh`: install only the direct thin runtime by default.
- `web/index.html`, `web/app.js`, `web/styles.css`: configure and operate the local product.
- `docs/architecture.md`, `README.md`, and `docs/install/*.md`: document only the supported local-first topology while retaining historical specs/plans.

---

### Task 1: Make SQLite the durable task and execution store

**Files:**
- Modify: `edge/localstore/store.go`
- Create: `edge/localstore/tasks.go`
- Create: `edge/localstore/tasks_test.go`
- Modify: `cloud/orchestrator/store.go`
- Modify: `cloud/orchestrator/service_test.go`

**Interfaces:**
- Consumes: `orchestrator.Task`, `orchestrator.TaskEvent`, and `orchestrator.Store`.
- Produces: `localstore.Open(path) (*Store, error)` whose result implements `orchestrator.Store`, plus transactional `UpdateWithEvent(context.Context, *Task, TaskEvent) error` and durable step command metadata.

- [x] **Step 1: Write failing SQLite persistence tests**

```go
func TestTaskPersistsAcrossReopen(t *testing.T) {
    path := filepath.Join(t.TempDir(), "agent.db")
    store, err := Open(path)
    if err != nil { t.Fatal(err) }
    task := &orchestrator.Task{ID: "task-1", Request: "把红色杯子放进右侧收纳盒", State: taskgraph.StateReady}
    if err := store.Create(context.Background(), task); err != nil { t.Fatal(err) }
    if err := store.Close(); err != nil { t.Fatal(err) }

    reopened, err := Open(path)
    if err != nil { t.Fatal(err) }
    defer reopened.Close()
    actual, err := reopened.Get(context.Background(), "task-1")
    if err != nil { t.Fatal(err) }
    if actual.Request != task.Request || actual.State != task.State {
        t.Fatalf("reopened task = %#v", actual)
    }
}

func TestTaskUpdateAndEventAreAtomic(t *testing.T) {
    store := openTestStore(t)
    task := &orchestrator.Task{ID: "task-1", State: taskgraph.StateReady}
    if err := store.Create(context.Background(), task); err != nil { t.Fatal(err) }
    task.State = taskgraph.StateObserving
    event := orchestrator.TaskEvent{Type: "STATE_CHANGED", Message: "local execution started"}
    if err := store.UpdateWithEvent(context.Background(), task, event); err != nil { t.Fatal(err) }
    actual, err := store.Get(context.Background(), task.ID)
    if err != nil { t.Fatal(err) }
    if actual.State != taskgraph.StateObserving || len(actual.Events) != 1 {
        t.Fatalf("task/event transaction = %#v", actual)
    }
}
```

- [x] **Step 2: Run the tests and verify they fail because task persistence is absent**

Run: `go test ./edge/localstore -run 'TestTaskPersistsAcrossReopen|TestTaskUpdateAndEventAreAtomic' -v`

Expected: FAIL because `Store` does not implement task CRUD or `UpdateWithEvent`.

- [x] **Step 3: Add normalized SQLite migrations and task CRUD**

Create `tasks`, `task_events`, and expanded `step_runs` tables in the existing WAL-mode database. Marshal intent, plan, payload, and result values as JSON. Use a SQL transaction for `UpdateWithEvent`; allocate event sequence with `MAX(sequence) + 1` inside that transaction.

```go
func (s *Store) UpdateWithEvent(ctx context.Context, task *orchestrator.Task, event orchestrator.TaskEvent) error {
    tx, err := s.db.BeginTx(ctx, nil)
    if err != nil { return err }
    defer tx.Rollback()
    sequence, err := nextEventSequence(ctx, tx, task.ID)
    if err != nil { return err }
    event.Sequence = sequence
    if event.OccurredAt.IsZero() { event.OccurredAt = time.Now().UTC() }
    if err := updateTaskRow(ctx, tx, task); err != nil { return err }
    if err := insertEventRow(ctx, tx, task.ID, event); err != nil { return err }
    return tx.Commit()
}
```

- [x] **Step 4: Run store and orchestrator tests**

Run: `go test ./edge/localstore ./cloud/orchestrator -v`

Expected: PASS with task data surviving close/reopen and ordered events loaded from `task_events`.

- [x] **Step 5: Commit the durable local store**

```bash
git add edge/localstore cloud/orchestrator/store.go cloud/orchestrator/service_test.go
git commit -m "feat: persist local tasks in sqlite"
```

### Task 2: Replace distributed claim/lease execution with one local worker

**Files:**
- Create: `internal/localapp/app.go`
- Create: `internal/localapp/app_test.go`
- Modify: `cloud/orchestrator/service.go`
- Modify: `edge/agent/runner.go`
- Delete: `edge/cloudclient/client.go`
- Delete: `edge/cloudclient/client_test.go`

**Interfaces:**
- Consumes: `*orchestrator.Service`, `*agent.Runner`, and semantic `runtime.Robot` behavior.
- Produces: `localapp.New(service, runner) *App`, `Start(context.Context)`, `Enqueue(taskID string) error`, and `Cancel(taskID string) error`.

- [x] **Step 1: Write failing local-worker tests**

```go
func TestApprovedTaskRunsWithoutClaimOrLease(t *testing.T) {
    service, runner, robot := newTestRuntime(t)
    app := New(service, runner)
    ctx, cancel := context.WithCancel(context.Background())
    defer cancel()
    app.Start(ctx)

    task, err := service.Create(ctx, "把红色杯子放进右侧收纳盒", "mujoco")
    if err != nil { t.Fatal(err) }
    if _, err := service.Approve(ctx, task.ID); err != nil { t.Fatal(err) }
    if err := app.Enqueue(task.ID); err != nil { t.Fatal(err) }

    completed := waitForState(t, service, task.ID, taskgraph.StateSucceeded)
    if completed.LeaseID != "" || completed.LeasedTo != "" {
        t.Fatalf("local task retained distributed lease: %#v", completed)
    }
    if robot.executeCalls == 0 { t.Fatal("robot was not executed") }
}

func TestUnapprovedPhysicalTaskDoesNotRun(t *testing.T) {
    service, runner, robot := newTestRuntime(t)
    app := New(service, runner)
    task, _ := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
    if err := app.Enqueue(task.ID); !errors.Is(err, ErrApprovalRequired) {
        t.Fatalf("enqueue error = %v", err)
    }
    if robot.executeCalls != 0 { t.Fatal("unapproved task reached robot") }
}
```

- [x] **Step 2: Verify tests fail before the local worker exists**

Run: `go test ./internal/localapp -v`

Expected: FAIL because the package and `App` execution worker are absent.

- [x] **Step 3: Implement a single bounded local queue**

Use a buffered channel of task IDs plus an in-memory de-duplication set. Load each task from SQLite immediately before execution, require approval, transition through the existing state machine, call `runner.Run`, and atomically persist state events. On startup, reconcile non-terminal in-progress states to `RECOVERABLE_FAILURE` rather than replaying them.

```go
type App struct {
    service *orchestrator.Service
    runner  *agent.Runner
    queue   chan string
    mu      sync.Mutex
    queued  map[string]struct{}
    active  map[string]context.CancelFunc
}

func (a *App) Enqueue(taskID string) error {
    task, err := a.service.Get(context.Background(), taskID)
    if err != nil { return err }
    if !task.Approved { return ErrApprovalRequired }
    a.mu.Lock()
    defer a.mu.Unlock()
    if _, exists := a.queued[taskID]; exists { return nil }
    a.queued[taskID] = struct{}{}
    a.queue <- taskID
    return nil
}
```

- [x] **Step 4: Remove cloud publication from the runner path and wire telemetry in memory**

Set `runner.Telemetry` to call `service.PublishTelemetry` directly. Delete lease renewal and cloud task state publication. Preserve per-step SQLite idempotency and capability refresh.

- [x] **Step 5: Run worker, runner, and orchestrator tests**

Run: `go test ./internal/localapp ./edge/agent ./cloud/orchestrator -v`

Expected: PASS with no HTTP claim or lease calls.

- [x] **Step 6: Commit local execution**

```bash
git add internal/localapp cloud/orchestrator edge/agent edge/cloudclient
git commit -m "feat: execute tasks in the local agent"
```

### Task 3: Serve the API, Console, and executor from one Local Agent process

**Files:**
- Create: `console/server.go`
- Create: `console/server_test.go`
- Modify: `cmd/local-agent/main.go`
- Modify: `cmd/local-agent/main_test.go`
- Modify: `deploy/config/local.env.example`
- Modify: `deploy/laptop/com.tangying.robot-agent.plist`
- Modify: `deploy/laptop/tangying-robot-local-agent.service`

**Interfaces:**
- Consumes: local task service, `localapp.App`, robot client configuration, LLM provider configuration, and embedded `web.Handler()`.
- Produces: one loopback HTTP server and no dependency on `CLOUD_URL`.

- [x] **Step 1: Write failing route and configuration tests**

```go
func TestLocalRoutesExcludeDistributedControl(t *testing.T) {
    server := newLocalTestServer(t)
    assertStatus(t, server, "GET", "/healthz", nil, http.StatusOK)
    assertStatus(t, server, "GET", "/v1/tasks", nil, http.StatusOK)
    assertStatus(t, server, "POST", "/v1/agents/laptop/claim", nil, http.StatusNotFound)
    assertStatus(t, server, "POST", "/v1/leases/lease-1/renew", `{}`, http.StatusNotFound)
    assertStatus(t, server, "POST", "/v1/telemetry", `{}`, http.StatusMethodNotAllowed)
}

func TestLocalConfigUsesLoopbackAndLLMSettings(t *testing.T) {
    cfg, err := parseConfig([]string{"--listen", "127.0.0.1:8787", "--llm-model", "gpt-5-mini"})
    if err != nil { t.Fatal(err) }
    if cfg.listen != "127.0.0.1:8787" || cfg.llmModel != "gpt-5-mini" { t.Fatalf("config = %#v", cfg) }
    if cfg.cloudURL != "" { t.Fatalf("cloud URL remains: %q", cfg.cloudURL) }
}
```

- [x] **Step 2: Verify the tests fail on the current distributed server**

Run: `go test ./console ./cmd/local-agent -v`

Expected: FAIL because `console` is absent and Local Agent still requires `CLOUD_URL`.

- [x] **Step 3: Implement the local-only server**

Expose health, task list/get/create/approve/cancel, telemetry read, metrics, and event streaming. Creation persists locally; approval calls `executor.Enqueue`. Cancellation calls `executor.Cancel`. Do not register claim, lease, remote state, remote event append, or telemetry publication routes.

- [x] **Step 4: Compose the one-process Local Agent**

Open SQLite, build parser/planner from `AGENT_BASE_URL`, `AGENT_API_KEY`, `AGENT_MODEL`, and `AGENT_PROVIDER`, create the robot client and runner, start `localapp.App`, then serve `console.NewServer` at `127.0.0.1:8787` by default. Shut down HTTP, executor, robot connection, and database on SIGINT/SIGTERM.

- [x] **Step 5: Update laptop services and local configuration**

Remove `CLOUD_URL`. Add `LOCAL_LISTEN`, `AGENT_PROVIDER`, `AGENT_BASE_URL`, `AGENT_API_KEY`, `AGENT_MODEL`, `AGENT_ORCHESTRATION_SAMPLES`, and the existing robot mTLS settings. Ensure service templates launch only `local-agent`.

- [x] **Step 6: Run API, command, and service contract tests**

Run: `go test ./console ./cmd/local-agent ./internal/localapp -v && python3 -m pytest tests/deploy/test_deployment_contract.py -q`

Expected: PASS with the Console served locally and no cloud URL.

- [x] **Step 7: Commit the one-process desktop product**

```bash
git add console cmd/local-agent deploy/config/local.env.example deploy/laptop
git commit -m "feat: serve the product from one local process"
```

### Task 4: Tighten the Robot Runtime protocol for direct host-to-Pi control

**Files:**
- Modify: `proto/robot/v1/robot.proto`
- Modify generated files under: `gen/go/robot/v1/`
- Modify generated files under: `python/tangying_robot_proto/robot/v1/`
- Modify: `edge/runtime/runtime.go`
- Modify: `edge/robotclient/client.go`
- Modify: `edge/robotclient/client_test.go`
- Modify: `tests/contract/test_proto_schema.py`
- Modify: `robot/gateway/tangying_robot_gateway/service.py`

**Interfaces:**
- Consumes: existing runtime capabilities, observation, execution, cancel, and E-stop behavior.
- Produces: `RobotRuntime.GetRuntimeInfo`, `Observe`, `ExecuteSkill`, `Cancel`, and `EmergencyStop`; removes network pairing and reverse policy inference.

- [x] **Step 1: Write failing protocol contract tests**

```python
def test_robot_runtime_protocol_is_thin_and_host_initiated():
    services = robot_pb2.DESCRIPTOR.services_by_name
    assert set(services) == {"RobotRuntime"}
    methods = {method.name for method in services["RobotRuntime"].methods}
    assert methods == {"GetRuntimeInfo", "Observe", "ExecuteSkill", "Cancel", "EmergencyStop"}
    info = robot_pb2.RuntimeInfo(protocol_version="1.0", runtime_version="0.2.0")
    assert info.protocol_version == "1.0"
```

```go
func TestSnapshotRejectsProtocolMajorMismatch(t *testing.T) {
    snapshot := runtime.Snapshot{ProtocolVersion: "2.0"}
    if !errors.Is(snapshot.ValidateProtocol("1.0"), runtime.ErrProtocolIncompatible) {
        t.Fatal("major mismatch was accepted")
    }
}
```

- [x] **Step 2: Run contract tests and verify old services cause failure**

Run: `python3 -m pytest tests/contract/test_proto_schema.py -q && go test ./edge/runtime ./edge/robotclient -v`

Expected: FAIL because the schema still exposes `RobotGateway`, `Pair`, and `PolicyInference` and has no protocol version.

- [x] **Step 3: Update the protobuf schema and regenerate bindings**

Rename the service to `RobotRuntime`; replace `GetCapabilities` with `GetRuntimeInfo`; add `protocol_version`, `runtime_version`, semantic state, readiness, blockers, and capability fields to `RuntimeInfo`; remove `Pair`, `PairRequest`, `PairResponse`, `PolicyInference`, `ObservationBatch`, `Action`, and `ActionChunk` messages that no runtime method uses.

Run: `./scripts/generate-proto.sh`

- [x] **Step 4: Update semantic mapping and compatibility checks**

```go
func (s Snapshot) ValidateProtocol(expected string) error {
    expectedMajor, _, _ := strings.Cut(expected, ".")
    actualMajor, _, _ := strings.Cut(s.ProtocolVersion, ".")
    if expectedMajor == "" || actualMajor == "" || expectedMajor != actualMajor {
        return fmt.Errorf("%w: laptop=%s robot=%s", ErrProtocolIncompatible, expected, s.ProtocolVersion)
    }
    return nil
}
```

The client may display mismatched runtime health but must reject physical execution before sending `ExecuteSkill`.

- [x] **Step 5: Update Python runtime and all protocol clients**

Implement `GetRuntimeInfo`, register `RobotRuntimeServicer`, and keep the same mTLS requirement. Remove the placeholder network `Pair` method.

- [x] **Step 6: Run generation, Go, Python, and contract tests**

Run: `make generate && go test ./edge/runtime ./edge/robotclient ./... && python3 -m pytest tests/contract robot/gateway/tests -q`

Expected: PASS with no `Pair` or `PolicyInference` service in the descriptor.

- [x] **Step 7: Commit the protocol boundary**

```bash
git add proto gen/go python/tangying_robot_proto edge/runtime edge/robotclient robot/gateway tests/contract
git commit -m "refactor: define a thin robot runtime protocol"
```

### Task 5: Persist Raspberry Pi safety state and enforce uncertain-command recovery

**Files:**
- Create: `robot/gateway/tangying_robot_gateway/journal.py`
- Create: `robot/gateway/tests/test_journal.py`
- Modify: `robot/gateway/tangying_robot_gateway/safety.py`
- Modify: `robot/gateway/tangying_robot_gateway/service.py`
- Modify: `robot/gateway/tangying_robot_gateway/run_direct_edge.py`
- Modify: `deploy/config/robot-pi.env.example`
- Modify: `deploy/raspberry-pi/tangying-robot-edge-direct.service`

**Interfaces:**
- Consumes: command fingerprints, terminal `SkillEvent` sequences, and E-stop state changes.
- Produces: `RuntimeJournal(path, max_commands=128)`, persistent replay/conflict lookup, and restart-stable E-stop state.

- [x] **Step 1: Write failing journal and restart tests**

```python
def test_estop_latch_survives_reopen(tmp_path):
    path = tmp_path / "runtime-journal.json"
    journal = RuntimeJournal(path)
    journal.set_estop(True, "REMOTE_EMERGENCY_STOP")
    reopened = RuntimeJournal(path)
    assert reopened.estop_latched is True
    assert reopened.estop_reason == "REMOTE_EMERGENCY_STOP"

def test_terminal_command_replays_but_conflict_is_rejected(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime-journal.json", max_commands=2)
    journal.record("key-1", "fingerprint-a", [{"type": "SUCCEEDED", "code": "OK"}])
    assert journal.lookup("key-1", "fingerprint-a").status == "replay"
    assert journal.lookup("key-1", "fingerprint-b").status == "conflict"
```

- [x] **Step 2: Verify journal tests fail because state is only in memory**

Run: `python3 -m pytest robot/gateway/tests/test_journal.py robot/gateway/tests/test_safety.py -q`

Expected: FAIL because `RuntimeJournal` does not exist and E-stop state is transient.

- [x] **Step 3: Implement an atomic bounded JSON journal**

Write a versioned JSON document to a sibling temporary file, `fsync`, then `os.replace`. Store only E-stop state and the newest 128 command records. Use restrictive file permissions. Reject malformed or future-version journals by starting fail-closed with a visible blocker.

- [x] **Step 4: Integrate journal state into service and safety**

Load the E-stop latch before reporting capabilities. Persist the latch before invoking the backend stop. Look up an idempotency key before execution; replay an identical completed command, reject a fingerprint conflict, and record terminal events before returning them to the laptop.

- [x] **Step 5: Remove Raspberry Pi policy-provider execution**

The direct backend must require the laptop-provided bounded `action_chunk` for physical skills. Remove `ROBOT_POLICY_PROVIDER` from Pi configuration and startup. Retain perception/verification providers only where they are local hardware integrations; policy inference belongs on the laptop.

- [x] **Step 6: Run safety, backend, and deployment tests**

Run: `python3 -m pytest robot/gateway/tests tests/deploy/test_deployment_contract.py -q`

Expected: PASS with E-stop persistence and no Pi policy-provider configuration.

- [x] **Step 7: Commit Pi safety persistence**

```bash
git add robot/gateway deploy/config/robot-pi.env.example deploy/raspberry-pi/tangying-robot-edge-direct.service tests/deploy
git commit -m "feat: persist robot runtime safety state"
```

### Task 6: Simplify installation to local, robot-pi, and sim roles

**Files:**
- Modify: `install.sh`
- Modify: `scripts/install/common.sh`
- Modify: `scripts/install/local.sh`
- Modify: `scripts/install/robot-pi.sh`
- Modify: `scripts/install/sim.sh`
- Modify: `internal/robotagent/app.go`
- Modify: `internal/robotagent/app_test.go`
- Modify: `Makefile`
- Delete: `scripts/install/cloud.sh`
- Delete: `deploy/cloud/Dockerfile`
- Delete: `deploy/cloud/schema.sql`
- Delete: `deploy/docker-compose.yml`
- Delete: `deploy/config/cloud.env.example`
- Modify: `tests/install/test_bootstrap_contract.py`
- Modify: `tests/install/test_readme_contract.py`
- Modify: `tests/deploy/test_deployment_contract.py`

**Interfaces:**
- Consumes: existing role-based installer and `robot-agent` lifecycle CLI.
- Produces: supported `local`, `robot-pi`, and `sim` roles; `cloud` exits with a migration explanation.

- [x] **Step 1: Write failing role and dependency tests**

```python
def test_supported_roles_are_local_robot_pi_and_sim():
    result = run_install("--help")
    assert "local" in result.stdout
    assert "robot-pi" in result.stdout
    assert "sim" in result.stdout
    assert "cloud" not in supported_role_line(result.stdout)

def test_repository_has_no_default_cloud_runtime_assets():
    assert not Path("deploy/docker-compose.yml").exists()
    assert not Path("deploy/cloud").exists()
    assert not Path("scripts/install/cloud.sh").exists()
```

- [x] **Step 2: Verify installer tests fail on the four-role topology**

Run: `python3 -m pytest tests/install tests/deploy -q`

Expected: FAIL because cloud installation and Docker/PostgreSQL assets still exist.

- [x] **Step 3: Remove cloud installation and make direct Pi runtime the default**

Update help, role validation, receipts, doctor output, services, and package installation. `./install.sh cloud` must return nonzero with: `cloud role was removed; install the local role on the user's laptop`.

- [x] **Step 4: Build only product binaries**

Remove `cloud-control-plane` from `make build`, installers, demo startup, and service management. Build `robot-agent` and `local-agent`; simulation starts MuJoCo plus the same Local Agent binary.

- [x] **Step 5: Run installation and CLI tests**

Run: `go test ./internal/robotagent ./cmd/robot-agent -v && python3 -m pytest tests/install tests/deploy -q`

Expected: PASS with no Docker or PostgreSQL prerequisite.

- [x] **Step 6: Commit installation simplification**

```bash
git add install.sh scripts/install internal/robotagent Makefile deploy tests/install tests/deploy
git commit -m "refactor: simplify deployment to local first"
```

### Task 7: Make the Console configure and operate the local product

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`
- Modify: `web/embed.go`
- Modify: `console/server.go`
- Modify: `console/server_test.go`
- Modify: `docs/user-console.md`

**Interfaces:**
- Consumes: `/v1/config/status`, `/v1/runtime`, `/v1/tasks`, `/v1/telemetry`, and local event streaming.
- Produces: first-run LLM configuration, robot connection status, task history, approvals, cancellation, and local health views.

- [x] **Step 1: Write failing API and static-asset contract tests**

```go
func TestConfigStatusNeverReturnsAPIKey(t *testing.T) {
    server := newConfiguredLocalTestServer(t, "secret-key")
    response := requestJSON(t, server, "GET", "/v1/config/status", nil)
    body := response.Body.String()
    if strings.Contains(body, "secret-key") || strings.Contains(body, "apiKey") {
        t.Fatalf("configuration response leaked secret: %s", body)
    }
}
```

```python
def test_console_is_local_first():
    page = Path("web/index.html").read_text()
    script = Path("web/app.js").read_text()
    assert "LLM API" in page
    assert "机器人连接" in page
    assert "CLOUD_URL" not in page + script
```

- [x] **Step 2: Run tests and verify missing first-run configuration**

Run: `go test ./console -v && python3 -m pytest tests/install/test_readme_contract.py -q`

Expected: FAIL because configuration and runtime status APIs are not represented in the Console.

- [x] **Step 3: Add local settings and runtime cards**

Show LLM provider status without echoing the key; support endpoint/model/key updates; show robot address, certificate fingerprint, runtime/protocol versions, readiness, blockers, and reconnect state. Keep the task, telemetry, scene, audit, and metrics panels.

- [x] **Step 4: Use one global local event connection**

Replace per-task cloud WebSocket assumptions with `/v1/events/ws`. On task or telemetry events, refresh the relevant local resource. The UI must remain navigable when robot and LLM dependencies are offline.

- [x] **Step 5: Run Console and API tests**

Run: `go test ./console ./web -v && python3 -m pytest tests/install/test_readme_contract.py -q`

Expected: PASS with no secret in API responses or browser storage.

- [x] **Step 6: Commit the local Console**

```bash
git add web console docs/user-console.md
git commit -m "feat: add local setup and runtime console"
```

### Task 8: Remove obsolete runtime code and align all durable documentation

**Files:**
- Delete: `cmd/cloud-control-plane/`
- Delete: `cloud/api/`
- Delete: `cloud/orchestrator/postgres_store.go`
- Move reusable package responsibilities from: `cloud/agent/`, `cloud/intent/`, `cloud/orchestration/`, `cloud/orchestrator/`
- Create or update: `agent/`, `orchestration/`, `tasks/`, `console/`
- Modify all affected Go imports and tests.
- Modify: `go.mod`
- Modify: `go.sum`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/protocols.md`
- Modify: `docs/agent-v1.md`
- Modify: `docs/orchestration.md`
- Modify: `docs/production-readiness.md`
- Modify: `docs/install/local.md`
- Modify: `docs/install/cloud.md`
- Modify: `docs/install/robot-pi.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_repository.py`

**Interfaces:**
- Consumes: all local-first behavior from Tasks 1-7.
- Produces: responsibility-based package names, no PostgreSQL dependency, current architecture documentation linked to the retained governing design and plan.

- [x] **Step 1: Write failing repository architecture tests**

```python
def test_repository_runtime_is_local_first():
    assert not Path("cmd/cloud-control-plane").exists()
    assert not Path("cloud/api").exists()
    assert not Path("cloud/orchestrator/postgres_store.go").exists()
    go_mod = Path("go.mod").read_text()
    assert "github.com/jackc/pgx" not in go_mod

def test_current_docs_link_governing_design_assets():
    architecture = Path("docs/architecture.md").read_text()
    assert "docs/superpowers/specs/2026-08-18-local-first-runtime-design.md" in architecture
    assert Path("docs/superpowers/plans/2026-08-18-local-first-runtime.md").exists()
```

- [x] **Step 2: Verify architecture tests fail while obsolete runtime remains**

Run: `python3 -m pytest tests/test_repository.py -q`

Expected: FAIL because cloud runtime packages and the pgx dependency remain.

- [x] **Step 3: Move reusable code to responsibility-based packages**

Use `agent`, `orchestration`, and `tasks` import paths. Keep generated protocol packages unchanged. Update consumers and tests in one mechanical change, then delete the emptied cloud runtime directories.

- [x] **Step 4: Remove PostgreSQL dependencies and tidy modules**

Run: `go mod tidy`

Verify: `rg -n 'pgx|DATABASE_URL|cloud-control-plane|CLOUD_URL|docker-compose' --glob '!docs/superpowers/**' .` returns only explicit migration/history notes.

- [x] **Step 5: Rewrite current docs and mark historical cloud docs as superseded**

Make README and current architecture start with the one-laptop product. Preserve historical design and plan documents. Replace `docs/install/cloud.md` content with a short superseded notice linking to `docs/install/local.md` and the governing local-first design; do not delete the historical file.

- [x] **Step 6: Run complete fresh verification**

Run:

```bash
make generate
go test ./...
python3 -m pytest -q
make lint
make build
./install.sh sim --dry-run --yes
```

Expected: every command exits 0; tests report zero failures; build produces `robot-agent` and `local-agent` but no cloud-control-plane binary; sim dry-run contains no Docker or PostgreSQL step.

- [x] **Step 7: Run simulation acceptance**

Run: `.venv/bin/python scripts/run_simulation_acceptance.py --episodes 3 --seed 20260818`

Expected: all episodes finish `SUCCEEDED`; a failed environment prerequisite is reported as a blocker rather than being treated as product success.

- [x] **Step 8: Commit the completed local-first architecture**

```bash
git add agent orchestration tasks console cmd cloud go.mod go.sum README.md CHANGELOG.md docs tests
git commit -m "refactor: complete local-first robot agent architecture"
```
