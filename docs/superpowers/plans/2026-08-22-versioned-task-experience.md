# Versioned Task Experience and Production Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add immutable natural-language task revisions, Harness-backed human-readable progress, safe mid-execution updates, and a production-delivery documentation set to the shared Fleet and Local Brain system.

**Architecture:** Extend the existing `tasks.Service` aggregate and its memory, SQLite, and MySQL repositories with compare-and-swap revision commits; do not create a second task system. The Fleet Coordinator reconciles revisions against persisted intent state, resource fencing, and authoritative Harness evidence, while a server-owned `task.experience.v1` projector produces safe plain-language views for both cloud and local Consoles. The existing WebGL world remains authoritative for visual context, and a responsive mission rail consumes the new projection without deriving facts or tool meanings in the browser.

**Tech Stack:** Go 1.26, Python 3.11, MySQL 8, SQLite, Redis Streams, gRPC/protobuf, Gorilla WebSocket, vanilla JavaScript, Three.js 0.180.0, Node test runner, pytest, RoboCasa/MuJoCo.

**Spec:** `docs/superpowers/specs/2026-08-22-versioned-task-experience-design.md`

## Global Constraints

- Existing create/get/list/approve/cancel APIs remain compatible; an old persisted task is projected as revision 1.
- Revision content is immutable. Status changes are append-only revision lifecycle events.
- Completed physical steps and their Harness evidence never roll back or change outcome.
- A runtime acknowledgement means “动作已发送”; only a fresh Harness postcondition means “已完成”.
- A running physical command is not revoked by a normal revision. Conflicting changes wait for a declared safe checkpoint or the existing safety-stop/physical-approval path.
- Every update and command carries task ID, task revision, aggregate/event version, step ID, command ID, idempotency key where user initiated, and fencing token for guarded resources.
- Delayed events from older task revisions or lower fencing tokens are ignored.
- The browser never invents tool meanings and never renders unrestricted tool arguments, credentials, bearer/device tokens, or raw error strings in the default view.
- All user-provided strings are rendered with text-only DOM APIs; professional details are collapsed by default.
- Cloud Fleet and Local Brain expose the same revision and `task.experience.v1` contracts.
- RoboCasa and real robots use the same tool/observation/transform boundary; no real-robot-only task protocol is introduced.
- Production documentation must be executable: every command, route, link, configuration name, and troubleshooting probe is covered by repository tests or generated directly from authoritative route/config inventories.

---

## File responsibility map

### Task aggregate and persistence

- `tasks/revision.go`: revision domain types, immutable content, lifecycle events, commands, conflict errors, and stable semantic fingerprints.
- `tasks/reconcile.go`: pure retained/changed/added/paused step matching from prior evidence and a proposed plan.
- `tasks/experience.go`: `task.experience.v1` schema, plain-language fallbacks, secret filtering, and tool activity projection.
- `tasks/service.go`: create revision 1, propose/confirm/activate revisions, CAS conflict mapping, and legacy compatibility.
- `tasks/store.go`: atomic revision commit port shared by memory, SQLite, and MySQL.
- `tasks/memory_store.go`: deterministic in-memory CAS and revision history.
- `middleware/sqlite/store.go`, `middleware/sqlite/tasks.go`: local schema migration and atomic task/revision/event commits.
- `fleet/mysql/store.go`: cloud JSON task persistence plus revision/event tables and aggregate-version CAS.

### Distributed execution and tool evidence

- `fleet/coordinator/revisions.go`: revision basis, safe-point decision, immutable step reconciliation, failover rebuild, and activation after a running intent reaches its checkpoint.
- `fleet/coordinator/coordinator.go`: revision-aware nodes/checkpoints/events, monotonic old-event rejection, and Harness-backed step state.
- `edge/runtime/runtime.go`, `proto/robot/v1/robot.proto`: task revision and step identity on every command; user-facing capability metadata on runtime registration.
- `proto/fleet/v1/fleet.proto`, `core/toolcatalog/catalog.go`, `fleet/registry/registry.go`: display name, purpose, and safe argument allow-list in the immutable tool catalogue.
- `edge/worker/worker.go`: revision-aware claims and structured tool activity events.
- `edge/cloudclient/client.go`: revision-aware claim/complete/fail payloads.

### HTTP, projection, and Console

- `fleet/server.go`, `console/server.go`: revision proposal/confirmation/history/experience endpoints with consistent errors and idempotency.
- `web/index.html`: live-world plus mission-rail DOM, inline update composer, change preview, guided recovery, and collapsed professional details.
- `web/app.js`: task experience fetch/reconnect/order handling and update/confirm interactions.
- `web/styles.css`: responsive rail, mission ribbon, non-color status cues, focus/reduced-motion behavior.
- `web/task_experience_test.mjs`: text-only rendering, update, accessibility, revision ordering, and secret-leak regressions.

### Verification and production delivery

- `tests/e2e/test_robocasa_task_updates.py`: one real two-robot episode with mid-execution update and Harness completion.
- `tests/e2e/test_versioned_task_faults.py`: distributed update, retry, failover, stale evidence, timeout, and fencing matrix.
- `scripts/run_robocasa_harness.py`: signed acceptance summary fields for revision/update/tool-activity evidence.
- `docs/production/README.md`: delivery documentation index and audience map.
- `docs/production/architecture.md`: complete cloud/local/edge/runtime/world/Harness/data-flow design.
- `docs/production/quickstart.md`: zero-to-simulation, cloud, local, browser, robot simulation, and real-robot workflows.
- `docs/production/api-reference.md`: HTTP, WebSocket, gRPC, task, world, device, runtime, and error contracts.
- `docs/production/configuration-and-security.md`: configuration inventory, secrets, auth, certificates, network, backup, and hardening.
- `docs/production/sim-to-real.md`: tool registration, observations, map/transform calibration, safety, and migration checklist.
- `docs/production/operations-and-failures.md`: symptom-to-probe-to-recovery runbook for distributed failures.
- `docs/production/testing-and-acceptance.md`: test layers, acceptance evidence, release gates, and performance interpretation.
- `docs/production/data-contracts.md`: task revision, event, command, world, custody, evidence, and fencing schemas.
- `tests/docs/test_production_docs.py`: link, route, command, config, heading, and no-secret contract tests.

---

### Task 1: Define immutable task revisions and stable step identity

**Files:**
- Create: `tasks/revision.go`
- Create: `tasks/revision_test.go`
- Create: `tasks/reconcile.go`
- Create: `tasks/reconcile_test.go`
- Modify: `tasks/service.go:19-39`
- Modify: `tasks/memory_store.go:58-105`

**Interfaces:**
- Consumes: `manipulation.Intent`, `orchestration.Bundle`, `TaskEvent`, and Harness evidence IDs already stored by the coordinator.
- Produces: `TaskRevision`, `RevisionRecord`, `RevisionCommit`, `RevisionBasis`, `ProposeRevisionCommand`, `ConfirmRevisionCommand`, `SemanticFingerprint(RevisionStep) string`, and `BuildChangeSet(RevisionRecord, []RevisionStep, RevisionBasis) ChangeSet`.

- [ ] **Step 1: Write failing domain and reconciliation tests**

```go
func TestBuildChangeSetRetainsSatisfiedCompatibleStep(t *testing.T) {
	previous := tasks.RevisionRecord{Revision: tasks.TaskRevision{Revision: 1, Steps: []tasks.RevisionStep{{
		StepID: "handoff/sender", SemanticFingerprint: "same", Status: tasks.StepSatisfied,
		HarnessEvidenceIDs: []string{"evidence-1"},
	}}}}
	proposed := []tasks.RevisionStep{
		{StepID: "handoff/sender", SemanticFingerprint: "same"},
		{StepID: "handoff/receiver", SemanticFingerprint: "new"},
	}
	change := tasks.BuildChangeSet(previous, proposed, tasks.RevisionBasis{
		EvidenceValidity: map[string]bool{"handoff/sender": true},
	})
	if !slices.Equal(change.Retained, []string{"handoff/sender"}) ||
		!slices.Equal(change.Added, []string{"handoff/receiver"}) {
		t.Fatalf("change set = %#v", change)
	}
}

func TestBuildChangeSetPausesChangedRunningPhysicalStep(t *testing.T) {
	previous := tasks.RevisionRecord{Revision: tasks.TaskRevision{Revision: 1, Steps: []tasks.RevisionStep{{
		StepID: "handoff/sender", SemanticFingerprint: "old", Status: tasks.StepRunning,
	}}}}
	change := tasks.BuildChangeSet(previous, []tasks.RevisionStep{{
		StepID: "handoff/sender", SemanticFingerprint: "changed",
	}}, tasks.RevisionBasis{RunningStepIDs: []string{"handoff/sender"}})
	if !slices.Equal(change.Paused, []string{"handoff/sender"}) { t.Fatalf("change set = %#v", change) }
}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `go test ./tasks -run 'BuildChangeSet|SemanticFingerprint|Revision' -count=1 -v`

Expected: compilation fails because revision types and matching functions do not exist.

- [ ] **Step 3: Implement exact domain types and fingerprint rules**

```go
type RevisionStatus string
const (
	RevisionProposed RevisionStatus = "PROPOSED"
	RevisionWaitingApproval RevisionStatus = "WAITING_APPROVAL"
	RevisionWaitingSafePoint RevisionStatus = "WAITING_SAFE_POINT"
	RevisionActive RevisionStatus = "ACTIVE"
	RevisionSuperseded RevisionStatus = "SUPERSEDED"
	RevisionRejected RevisionStatus = "REJECTED"
)

type RevisionStep struct {
	StepID string `json:"stepId"`
	IntroducedRevision uint64 `json:"introducedRevision"`
	SemanticFingerprint string `json:"semanticFingerprint"`
	IntentIndex int `json:"intentIndex"`
	Action string `json:"action"`
	RobotID string `json:"robotId,omitempty"`
	ResourceID string `json:"resourceId,omitempty"`
	RequiredPostcondition string `json:"requiredPostcondition"`
	Status StepStatus `json:"status"`
	HarnessEvidenceIDs []string `json:"harnessEvidenceIds,omitempty"`
}

type TaskRevision struct {
	TaskID string `json:"taskId"`
	Revision uint64 `json:"revision"`
	BaseRevision uint64 `json:"baseRevision"`
	ExpectedAggregateVersion uint64 `json:"expectedAggregateVersion"`
	Request string `json:"request"`
	Understanding string `json:"understanding"`
	ChangeSet ChangeSet `json:"changeSet"`
	Intent manipulation.Intent `json:"intent"`
	Plan *orchestration.Bundle `json:"plan,omitempty"`
	Steps []RevisionStep `json:"steps"`
	RiskClass string `json:"riskClass"`
	ApprovalRequired bool `json:"approvalRequired"`
	Creator string `json:"creator"`
	IdempotencyKey string `json:"idempotencyKey"`
	CreatedAt time.Time `json:"createdAt"`
}
```

Fingerprint canonical input is `{action, robotId, resourceId, requiredPostcondition}` with trimmed strings and canonical JSON; use SHA-256 hex. Stable `stepId` is `intent-%03d/<first-12-fingerprint>` unless a matching prior step is retained.

- [ ] **Step 4: Add legacy task fields and deep cloning**

Add to `Task`:

```go
CurrentRevision uint64 `json:"currentRevision"`
AggregateVersion uint64 `json:"aggregateVersion"`
RevisionState RevisionStatus `json:"revisionState"`
```

`NormalizeLegacyTask` returns revision/version 1 and `ACTIVE` when old JSON contains zeros. Clone every revision step, change-set slice, evidence slice, intent, plan, and event payload; tests must mutate returned values and prove store state remains unchanged.

- [ ] **Step 5: Run GREEN and compatibility tests**

Run: `go test ./tasks -count=1`

Expected: all existing task tests plus revision/matching/clone tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add tasks/revision.go tasks/revision_test.go tasks/reconcile.go tasks/reconcile_test.go tasks/service.go tasks/memory_store.go
git commit -m "feat: define immutable task revisions"
```

### Task 2: Add atomic revision persistence to memory, SQLite, and MySQL

**Files:**
- Modify: `tasks/store.go`
- Modify: `tasks/memory_store.go`
- Modify: `middleware/sqlite/store.go`
- Modify: `middleware/sqlite/tasks.go`
- Modify: `middleware/sqlite/tasks_test.go`
- Modify: `fleet/mysql/store.go`
- Modify: `fleet/mysql/store_test.go`
- Modify: `tasks/service_test.go`

**Interfaces:**
- Consumes: Task 1 `RevisionCommit`, immutable revision content, and lifecycle events.
- Produces: repository methods `CreateWithRevision`, `CommitRevision`, `Revision`, and `ListRevisions` with identical CAS/idempotency semantics across three adapters.

- [ ] **Step 1: Write failing shared repository contract tests**

```go
func exerciseRevisionRepository(t *testing.T, repository tasks.Repository) {
	created := seededTaskAndRevision(t)
	if err := repository.CreateWithRevision(context.Background(), created.Task, created.Revision); err != nil { t.Fatal(err) }
	commit := tasks.RevisionCommit{TaskID: created.Task.ID, ExpectedAggregateVersion: 1,
		Task: nextTask(created.Task), NewRevision: proposedRevision(created.Task.ID, 2, "idem-2"),
		LifecycleEvent: tasks.RevisionLifecycleEvent{Revision: 2, Status: tasks.RevisionProposed}}
	if err := repository.CommitRevision(context.Background(), commit); err != nil { t.Fatal(err) }
	if err := repository.CommitRevision(context.Background(), commit); err != nil { t.Fatal("idempotent replay:", err) }
	stale := commit; stale.NewRevision = proposedRevision(created.Task.ID, 3, "idem-3")
	if !errors.Is(repository.CommitRevision(context.Background(), stale), tasks.ErrRevisionConflict) {
		t.Fatal("stale aggregate version must fail closed")
	}
}
```

Add adapter-specific restart tests and verify two concurrent proposals produce exactly one success and one `ErrRevisionConflict`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
go test ./tasks ./middleware/sqlite ./fleet/mysql -run 'Revision|Aggregate|Legacy' -count=1 -v
```

Expected: repository interface and schemas lack revision operations.

- [ ] **Step 3: Extend the repository port**

```go
type Repository interface {
	CreateWithRevision(context.Context, *Task, *TaskRevision) error
	Get(context.Context, string) (*Task, error)
	Update(context.Context, *Task) error
	List(context.Context) ([]*Task, error)
	CommitRevision(context.Context, RevisionCommit) error
	Revision(context.Context, string, uint64) (RevisionRecord, error)
	ListRevisions(context.Context, string) ([]RevisionRecord, error)
}
```

Define `ErrRevisionConflict`, `ErrRevisionNotFound`, and `ErrIdempotencyConflict`. An exact replay of `(taskId,idempotencyKey,request)` returns success; the same key with different content returns `ErrIdempotencyConflict`.

- [ ] **Step 4: Implement SQLite migration and transaction**

Add task columns with guarded `ALTER TABLE` migration: `current_revision INTEGER NOT NULL DEFAULT 1`, `aggregate_version INTEGER NOT NULL DEFAULT 1`, `revision_state TEXT NOT NULL DEFAULT 'ACTIVE'`. Add:

```sql
CREATE TABLE IF NOT EXISTS task_revisions (
  task_id TEXT NOT NULL, revision INTEGER NOT NULL, content_json BLOB NOT NULL,
  idempotency_key TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (task_id, revision), UNIQUE (task_id, idempotency_key),
  FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS task_revision_events (
  task_id TEXT NOT NULL, revision INTEGER NOT NULL, sequence INTEGER NOT NULL,
  status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', payload_json BLOB NOT NULL,
  occurred_at TEXT NOT NULL, PRIMARY KEY (task_id, revision, sequence)
);
```

`CommitRevision` begins one transaction, selects aggregate version, checks idempotency, conditionally updates the task row, inserts immutable content only for a new proposal, appends lifecycle/task events, and commits. No `ON CONFLICT DO UPDATE` is allowed for revision content.

- [ ] **Step 5: Implement MySQL and memory with the same contract**

MySQL adds `aggregate_version BIGINT UNSIGNED NOT NULL DEFAULT 1` to `robot_tasks`, plus `task_revisions` and `task_revision_events` InnoDB tables. Use `SELECT ... FOR UPDATE` and `UPDATE ... WHERE aggregate_version = ?`. Memory store performs the same checks under its existing mutex and deep-clones every input/output.

- [ ] **Step 6: Run repository, restart, race, and legacy tests**

Run:

```bash
go test ./tasks ./middleware/sqlite ./fleet/mysql -count=1
```

Expected: all adapters agree on CAS, idempotency, immutability, restart, and legacy projection.

- [ ] **Step 7: Commit Task 2**

```bash
git add tasks/store.go tasks/memory_store.go middleware/sqlite/store.go middleware/sqlite/tasks.go middleware/sqlite/tasks_test.go fleet/mysql/store.go fleet/mysql/store_test.go tasks/service_test.go
git commit -m "feat: persist task revisions atomically"
```

### Task 3: Implement revision proposal, confirmation, and human-first projection

**Files:**
- Create: `tasks/experience.go`
- Create: `tasks/experience_test.go`
- Modify: `tasks/service.go`
- Modify: `tasks/service_test.go`

**Interfaces:**
- Consumes: Task 2 repository CAS methods and Task 1 reconciliation.
- Produces: `Service.ProposeRevision`, `Service.ConfirmRevision`, `Service.ActivateWaitingRevision`, `Service.ListRevisions`, and `ProjectExperience(ExperienceInput) TaskExperience`.

- [ ] **Step 1: Write failing lifecycle and projection tests**

```go
func TestProposalDoesNotReplaceActiveTaskUntilConfirmed(t *testing.T) {
	service, task := seededService(t)
	proposal, err := service.ProposeRevision(context.Background(), tasks.ProposeRevisionCommand{
		TaskID: task.ID, ExpectedRevision: 1, Request: "最后放到右侧蓝色垫子上",
		IdempotencyKey: "update-1", Creator: "operator/admin",
	}, tasks.RevisionBasis{})
	if err != nil { t.Fatal(err) }
	current, _ := service.Get(context.Background(), task.ID)
	if current.CurrentRevision != 1 || proposal.Revision != 2 || current.RevisionState != tasks.RevisionProposed {
		t.Fatalf("task=%#v proposal=%#v", current, proposal)
	}
}

func TestExperienceDoesNotExposeSecretsOrRawToolCodes(t *testing.T) {
	view := tasks.ProjectExperience(tasks.ExperienceInput{Activities: []tasks.ToolActivityInput{{
		ToolName: "manipulation.pick", Arguments: map[string]any{"targetRef":"red-block", "bearerToken":"secret"},
		Display: tasks.ToolDisplay{DisplayName:"拿稳物品", Purpose:"安全拿起红色方块", SafeArguments:[]string{"targetRef"}},
	}}})
	wire, _ := json.Marshal(view)
	if bytes.Contains(wire, []byte("secret")) || bytes.Contains(wire, []byte("bearerToken")) { t.Fatal(string(wire)) }
	if view.Activities[0].DisplayName != "拿稳物品" { t.Fatalf("view=%#v", view) }
}
```

- [ ] **Step 2: Verify RED**

Run: `go test ./tasks -run 'Proposal|Confirm|Experience|Secret|Legacy' -count=1 -v`

Expected: lifecycle methods and experience schema are absent.

- [ ] **Step 3: Implement service lifecycle**

Exact public commands:

```go
type ProposeRevisionCommand struct { TaskID string; ExpectedRevision uint64; Request, IdempotencyKey, Creator string }
type ConfirmRevisionCommand struct { TaskID string; Revision, ExpectedCurrentRevision uint64; IdempotencyKey, Actor string; WaitForSafePoint, ApprovalRequired bool }

func (s *Service) ProposeRevision(ctx context.Context, command ProposeRevisionCommand, basis RevisionBasis) (*RevisionRecord, error)
func (s *Service) ConfirmRevision(ctx context.Context, command ConfirmRevisionCommand) (*RevisionRecord, error)
func (s *Service) ActivateWaitingRevision(ctx context.Context, taskID string, revision uint64, idempotencyKey string) (*RevisionRecord, error)
```

Proposal parses/plans the new request, produces understanding and revision steps, computes change-set/risk, and commits `REVISION_PROPOSED`. Confirmation commits `WAITING_SAFE_POINT`, `WAITING_APPROVAL`, or `ACTIVE`; activation supersedes the prior revision, copies active request/intent/plan to the compatibility fields on `Task`, increments aggregate version, and appends `REVISION_ACTIVATED`.

- [ ] **Step 4: Implement `task.experience.v1`**

```go
type TaskExperience struct {
	SchemaVersion string `json:"schemaVersion"`
	TaskID string `json:"taskId"`
	Revision uint64 `json:"revision"`
	AggregateVersion uint64 `json:"aggregateVersion"`
	Headline string `json:"headline"`
	OriginalRequest string `json:"originalRequest"`
	Understanding string `json:"understanding"`
	UpdateStatus RevisionStatus `json:"updateStatus"`
	ChangePreview ChangePreview `json:"changePreview"`
	Steps []ExperienceStep `json:"steps"`
	Activities []ToolActivity `json:"activities"`
	Recovery *RecoveryGuidance `json:"recovery,omitempty"`
	AllowedActions []string `json:"allowedActions"`
	Professional ProfessionalDetails `json:"professional"`
}
```

Status copy is server-owned Chinese: `SATISFIED=已完成`, `RUNNING=正在执行`, `AWAITING_EVIDENCE=正在确认结果`, `WAITING_SAFE_POINT=机器人将在安全位置更新任务`. Unknown tool names use “机器人能力” and never echo the unknown raw name outside `professional`.

- [ ] **Step 5: Run task service and projection tests**

Run: `go test ./tasks -count=1`

Expected: proposal/confirmation/idempotency/conflict/legacy/plain-language/secret filtering all pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add tasks/service.go tasks/service_test.go tasks/experience.go tasks/experience_test.go
git commit -m "feat: project versioned tasks for robot owners"
```

### Task 4: Add user-facing tool metadata and revision identity to robot commands

**Files:**
- Modify: `core/toolcatalog/catalog.go`
- Modify: `core/toolcatalog/catalog_test.go`
- Modify: `edge/runtime/runtime.go`
- Modify: `proto/fleet/v1/fleet.proto`
- Modify: `proto/robot/v1/robot.proto`
- Modify: `cmd/edge-worker/main.go`
- Modify: `fleet/gateway/gateway.go`
- Modify: `fleet/gateway/gateway_test.go`
- Modify: `fleet/registry/registry.go`
- Modify: `fleet/registry/registry_test.go`
- Modify: `edge/robotclient/client.go`
- Modify: `edge/robotclient/mapping_test.go`
- Regenerate: `gen/go/fleet/v1/fleet.pb.go`
- Regenerate: `gen/go/robot/v1/robot.pb.go`
- Regenerate: `python/tangying_robot_proto/fleet/v1/fleet_pb2.py`
- Regenerate: `python/tangying_robot_proto/robot/v1/robot_pb2.py`

**Interfaces:**
- Consumes: Task 3 `ToolDisplay` and current immutable catalog revision.
- Produces: catalogue fields `displayName`, `purpose`, `safeArgumentNames`; command fields `taskRevision`, `aggregateVersion`, and `stepId` across Go/protobuf/Python.

- [ ] **Step 1: Write failing canonical-hash and wire tests**

```go
func TestDisplayMetadataParticipatesInCatalogRevision(t *testing.T) {
	base := toolcatalog.Tool{Name:"manipulation.pick", SideEffectClass:toolcatalog.PhysicalAtomic, DisplayName:"拿稳物品"}
	changed := base; changed.DisplayName = "拿起物品"
	a, _ := toolcatalog.Revision([]toolcatalog.Tool{base})
	b, _ := toolcatalog.Revision([]toolcatalog.Tool{changed})
	if a == b { t.Fatal("display contract must be frozen by catalogue revision") }
}

func TestCommandWireCarriesRevisionStepAndFencingIdentity(t *testing.T) {
	wire, err := commandToProto(runtime.Command{SchemaVersion:"robot.v1", CommandID:"cmd-1", TaskID:"task-1",
		TaskRevision:2, AggregateVersion:7, StepID:"handoff/receiver", FencingToken:3})
	if err != nil { t.Fatal(err) }
	if wire.TaskRevision != 2 || wire.AggregateVersion != 7 || wire.StepId != "handoff/receiver" { t.Fatalf("wire=%#v", wire) }
}
```

- [ ] **Step 2: Verify RED**

Run: `go test ./core/toolcatalog ./edge/robotclient ./fleet/gateway ./fleet/registry -run 'Display|RevisionStep' -count=1 -v`

Expected: new catalogue and command fields are missing.

- [ ] **Step 3: Extend proto without renumbering existing fields**

Add `display_name=8`, `purpose=9`, `safe_argument_names=10` to Fleet `ToolDescriptor`. Add `task_revision=17`, `aggregate_version=18`, `step_id=19` to robot `SkillCommand`. Update runtime/catalogue structs and mappings; `safe_argument_names` is sorted and deduplicated during catalogue canonicalization.

- [ ] **Step 4: Generate and verify bindings**

Run: `bash scripts/generate-proto.sh && make generate-check`

Expected: Go and Python generated bindings are current and contain only additive field changes.

- [ ] **Step 5: Add conservative server-owned fallbacks**

Map known capabilities exactly: `navigation.navigate=移动到指定位置`, `arm.move=调整机械臂`, `manipulation.pick=拿稳物品`, `manipulation.place=放下物品`, `observe_scene=查看周围环境`, `state.get=检查机器人状态`, `safety.emergency_stop=立即停止机器人`. Runtime descriptions may refine purpose but cannot override the safe-argument allow-list with unknown names.

- [ ] **Step 6: Run full protocol/catalogue tests and commit**

Run:

```bash
go test ./core/toolcatalog ./edge/runtime ./edge/robotclient ./fleet/gateway ./fleet/registry -count=1
make generate-check
```

```bash
git add core/toolcatalog edge/runtime/runtime.go proto/fleet/v1/fleet.proto proto/robot/v1/robot.proto cmd/edge-worker/main.go fleet/gateway fleet/registry edge/robotclient gen/go python/tangying_robot_proto
git commit -m "feat: describe revisioned robot tool activity"
```

### Task 5: Reconcile active revisions in the Fleet Coordinator and Harness

**Files:**
- Create: `fleet/coordinator/revisions.go`
- Create: `fleet/coordinator/revisions_test.go`
- Modify: `fleet/coordinator/coordinator.go`
- Modify: `fleet/coordinator/coordinator_test.go`
- Modify: `fleet/coordinator/harness_test.go`
- Modify: `fleet/eventlog/store.go`

**Interfaces:**
- Consumes: Task 3 lifecycle, current coordinator checkpoint, leases/fencing, and `worldmodel.Reader` evidence.
- Produces: `Coordinator.RevisionBasis`, `Coordinator.ConfirmRevision`, `Coordinator.ReconcileRevision`, revision-aware `IntentNode`, and monotonic failover checkpoints.

- [ ] **Step 1: Write failing running-update and failover tests**

```go
func TestConfirmRevisionWaitsForRunningIntentThenRetainsSatisfiedStep(t *testing.T) {
	coordinator, service, task := runningTwoRobotTask(t)
	proposal := proposeUpdate(t, service, task.ID, "最后放到右侧蓝色垫子上")
	record, err := coordinator.ConfirmRevision(context.Background(), task.ID, proposal.Revision, 1, "confirm-2")
	if err != nil { t.Fatal(err) }
	if record.Status != tasks.RevisionWaitingSafePoint { t.Fatalf("status=%s", record.Status) }
	completeSenderWithHarnessEvidence(t, coordinator, task.ID)
	revisions, _ := service.ListRevisions(context.Background(), task.ID)
	if revisions[1].Status != tasks.RevisionActive || revisions[1].Revision.ChangeSet.Retained[0] != revisions[0].Revision.Steps[0].StepID {
		t.Fatalf("revisions=%#v", revisions)
	}
}

func TestOlderRevisionCompletionCannotAdvanceNewGraph(t *testing.T) {
	coordinator := revisionedCoordinator(t, 2)
	_, err := coordinator.CompleteIntentRevision(context.Background(), "task-1", 1, 1, "robot-2", 2)
	if !errors.Is(err, coordinator.ErrStaleTaskRevision) { t.Fatalf("err=%v", err) }
}
```

- [ ] **Step 2: Verify RED**

Run: `go test ./fleet/coordinator -run 'Revision|SafePoint|Older|Failover|Evidence' -count=1 -v`

Expected: coordinator nodes and methods have no task revision identity.

- [ ] **Step 3: Extend checkpoint state and nodes**

Add to `IntentNode`: `StepID`, `TaskRevision`, `AggregateVersion`, `SemanticFingerprint`, `CommandID`, `SafeCheckpoint`. Add `TaskRevision` and `AggregateVersion` to `persistedState` and `Snapshot`. Every domain event payload includes task revision, step ID, command ID when present, fencing token, and world revision basis.

- [ ] **Step 4: Implement deterministic reconcile rules**

`RevisionBasis` derives running/satisfied step IDs and validates retained Harness evidence against current world/model/transform identity. `ConfirmRevision` holds the coordinator mutation lock, marks a conflicting proposal `WAITING_SAFE_POINT`, and never cancels the command. After `CompleteIntentRevision` commits Harness satisfaction and releases its resource, it calls `ActivateWaitingRevision`, rebuilds nodes by stable step identity, preserves satisfied evidence, marks obsolete pending nodes `CANCELLED_BY_REVISION`, and publishes new ready work through the existing outbox.

- [ ] **Step 5: Reject delayed and lower-fence events**

Claim/complete/fail require exact active revision and step ID. Completion additionally requires the currently recorded command ID and fencing token. Exact duplicate events return the existing snapshot; older revision, older aggregate version, wrong command, and lower fencing token return typed conflicts without writing state/events/outbox.

- [ ] **Step 6: Verify coordinator, Harness, leases, outbox, and restart**

Run:

```bash
go test ./fleet/coordinator ./fleet/eventlog ./fleet/lease -count=1
```

Expected: safe-point, retained evidence, stale event, duplicate, fencing, crash/reload, and leader failover tests pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add fleet/coordinator/revisions.go fleet/coordinator/revisions_test.go fleet/coordinator/coordinator.go fleet/coordinator/coordinator_test.go fleet/coordinator/harness_test.go fleet/eventlog/store.go
git commit -m "feat: reconcile task revisions at safe checkpoints"
```

### Task 6: Emit structured tool activity and make Edge execution revision-aware

**Files:**
- Modify: `edge/worker/worker.go`
- Modify: `edge/worker/worker_test.go`
- Modify: `edge/cloudclient/client.go`
- Modify: `edge/cloudclient/client_test.go`
- Modify: `edge/agent/runner.go`
- Modify: `edge/agent/runner_test.go`
- Modify: `fleet/server.go`
- Modify: `fleet/server_test.go`

**Interfaces:**
- Consumes: Task 4 command identity and Task 5 revision-aware coordinator methods.
- Produces: structured `TOOL_ACTIVITY` task events and exact revision/step/command/fence claim-completion requests.

- [ ] **Step 1: Write failing event and stale-worker tests**

```go
func TestWorkerReportsToolActivityWithoutClaimingCompletion(t *testing.T) {
	worker, cloud := configuredWorker(t)
	cloud.next = &coordinator.IntentNode{Index:0, StepID:"handoff/sender", TaskRevision:2, AggregateVersion:7, FencingToken:3}
	if err := worker.processTask(context.Background(), "task-1"); err != nil { t.Fatal(err) }
	activity := cloud.event("TOOL_ACTIVITY")
	if activity.Payload["taskRevision"] != uint64(2) || activity.Payload["activityStatus"] != "AWAITING_EVIDENCE" {
		t.Fatalf("activity=%#v", activity)
	}
	if cloud.completedBeforeHarness { t.Fatal("runtime acknowledgement cannot complete physical intent") }
}
```

- [ ] **Step 2: Verify RED**

Run: `go test ./edge/worker ./edge/cloudclient ./edge/agent ./fleet -run 'ToolActivity|TaskRevision|StaleWorker' -count=1 -v`

Expected: payloads contain index/robot only and task events contain raw messages.

- [ ] **Step 3: Carry identity through claim, command, and completion**

Cloud client sends `{robotId,taskRevision,aggregateVersion,stepId,commandId,fencingToken}`. Worker refreshes the task after every claim; it refuses to execute if fetched `CurrentRevision` differs from the node. Command ID and idempotency key include task revision and stable step ID. Edge/runtime and local runner populate the new command fields.

- [ ] **Step 4: Emit structured activity transitions**

Emit `SENDING` before invoke, `RUNNING` when accepted, `AWAITING_EVIDENCE` on runtime success, `CONFIRMED` only after coordinator Harness completion, and `FAILED` on terminal tool error. Payload contains safe identity fields and raw arguments only under the audit event; Task 3 projector filters them through the catalogue allow-list.

- [ ] **Step 5: Run Edge/Fleet tests and commit**

Run: `go test ./edge/... ./fleet/... -count=1`

```bash
git add edge/worker edge/cloudclient edge/agent fleet/server.go fleet/server_test.go
git commit -m "feat: trace revisioned robot tool execution"
```

### Task 7: Expose revision and experience APIs in Fleet and Local Brain

**Files:**
- Modify: `fleet/server.go`
- Modify: `fleet/server_test.go`
- Modify: `console/server.go`
- Modify: `console/server_test.go`
- Modify: `internal/localapp/app.go`
- Modify: `internal/localapp/app_test.go`

**Interfaces:**
- Consumes: Task 3 service/projector, Task 5 coordinator basis, device registry, and world snapshot.
- Produces: four approved revision/experience endpoints with identical JSON/error semantics in Fleet and Local Brain.

- [ ] **Step 1: Write failing HTTP contract tests**

```go
func TestRevisionEndpointsReturnPreviewHistoryAndExperience(t *testing.T) {
	fleet := newTestFleet(t); defer fleet.close()
	task := createTask(t, fleet, multiRobotPrompt, "mujoco")
	proposal := fleet.doJSON(t, "POST", "/v1/tasks/"+task.ID+"/revisions", map[string]any{
		"expectedRevision":1, "request":"最后放到右侧蓝色垫子上", "idempotencyKey":"update-1",
	})
	if proposal.Code != http.StatusCreated { t.Fatalf("status=%d", proposal.Code) }
	history := fleet.doJSON(t, "GET", "/v1/tasks/"+task.ID+"/revisions", nil)
	experience := fleet.doJSON(t, "GET", "/v1/tasks/"+task.ID+"/experience", nil)
	assertJSONSchema(t, history.Body, "task.revisions.v1")
	assertJSONSchema(t, experience.Body, "task.experience.v1")
}
```

Add `409 REVISION_CONFLICT`, duplicate-idempotency replay, `404 TASK_NOT_FOUND`, invalid request, and operator/device authorization tests. Run the same handler contract against `console.Server`.

- [ ] **Step 2: Verify RED**

Run: `go test ./fleet ./console ./internal/localapp -run 'RevisionEndpoint|Experience|Conflict' -count=1 -v`

Expected: routes do not exist.

- [ ] **Step 3: Add exact routes and handler shapes**

```text
POST /v1/tasks/{id}/revisions
POST /v1/tasks/{id}/revisions/{revision}/confirm
GET  /v1/tasks/{id}/revisions
GET  /v1/tasks/{id}/experience
```

Proposal requires `expectedRevision`, non-empty `request`, and non-empty UUID-shaped `idempotencyKey`. Confirmation requires `expectedCurrentRevision` and `idempotencyKey`. Fleet obtains `RevisionBasis`/safe-point state from Coordinator and catalogue displays from Registry; Local Brain derives execution state from task events and its single executor.

- [ ] **Step 4: Preserve realtime and refresh consistency**

Revision lifecycle and tool activities are appended to existing task events and Fleet domain events with monotonic cursors. Existing WebSocket/session generation guards remain; a gap triggers one `GET experience` resync, equal/older cursor events cannot roll back the rendered revision.

- [ ] **Step 5: Run HTTP/auth/local compatibility tests and commit**

Run: `go test ./fleet ./console ./internal/localapp ./tasks -count=1`

```bash
git add fleet/server.go fleet/server_test.go console/server.go console/server_test.go internal/localapp/app.go internal/localapp/app_test.go
git commit -m "feat: expose versioned task experience APIs"
```

### Task 8: Build the non-technical mission rail and inline update flow

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`
- Create: `web/task_experience_test.mjs`
- Modify: `web/app_test.mjs`
- Modify: `web/embed_test.go`

**Interfaces:**
- Consumes: Task 7 `task.experience.v1`, proposal/confirm routes, and existing Fleet world session.
- Produces: mission rail, mission ribbon, step/tool evidence cards, guided recovery, and safe inline update preview.

- [ ] **Step 1: Write failing DOM and interaction tests**

```js
test("mission rail explains understanding, tools and Harness evidence", async () => {
  const app = await bootFleetApp({ experience: fixtureExperience });
  assert.equal(app.document.querySelector("#fleet-mission-understanding").textContent,
    "1号机器人先把方块交给2号机器人，再由2号机器人放到右侧目标区");
  assert.equal(app.document.querySelector("[data-step-id='handoff/sender'] .mission-capability").textContent,
    "拿稳物品");
  assert.match(app.document.querySelector("[data-step-id='handoff/sender'] .mission-evidence").textContent,
    /环境已经确认/);
  assert.equal(app.document.querySelector("details.mission-professional").open, false);
});

test("inline update renders retain change add pause preview before confirm", async () => {
  const app = await bootFleetApp({ proposal: fixtureProposal });
  await app.update("最后放到右侧蓝色垫子上");
  assert.deepEqual([...app.document.querySelectorAll("[data-change-kind]")].map(x => x.dataset.changeKind),
    ["retained", "changed", "added", "paused"]);
  assert.equal(app.confirmRequests.length, 0);
  await app.document.querySelector("#fleet-update-confirm").click();
  assert.equal(app.confirmRequests.length, 1);
});
```

Add tests for `textContent` only, secret strings absent, keyboard focus, `aria-live` scoped to one concise status, reduced motion, professional disclosure, 409 preview, refresh/reconnect/equal/old/gap events, and Canvas/WebGL fallback independence.

- [ ] **Step 2: Verify RED**

Run: `cd web && node --test task_experience_test.mjs app_test.mjs`

Expected: mission rail and update controls are missing.

- [ ] **Step 3: Add mission-rail HTML beside the world**

Use one `fleet-mission-layout` grid containing the existing `fleet-world-stage` and a new `<aside id="fleet-mission-rail">`. Include current revision/title, “机器人理解为”, ordered ribbon list, recovery panel, update textarea, preview, confirm/edit buttons, and one collapsed `<details class="mission-professional">`. Keep emergency stop outside the update flow.

- [ ] **Step 4: Implement safe render/update state machine**

`renderTaskExperience` builds every node via `createElement` and assigns strings with `textContent`. Store `{taskId, revision, aggregateVersion, cursor}`; accept newer revisions, allow same-revision status progress only with newer aggregate/cursor, reject older values, and resync on gaps. Proposal preview does not mutate active cards. `409` keeps the user text and shows “任务已被其他操作更新，请查看最新变化”.

- [ ] **Step 5: Implement responsive visual system**

Desktop uses `minmax(0, 1fr) minmax(320px, 390px)` with world largest; under 980px the rail moves below. Mission ribbon uses shape/icon/text in addition to mint/amber/red/cyan. Focus rings meet 3:1 contrast, controls are at least 44px high on touch layouts, and `prefers-reduced-motion` disables ribbon/step transitions.

- [ ] **Step 6: Run Web, embed, and deterministic bundle checks**

Run:

```bash
cd web && npm ci && npm run build && npm test
cd .. && go test ./web -count=1
sha256sum web/webgl_scene.js
```

Expected: all old WebGL/interaction/session tests plus mission rail tests pass; `webgl_scene.js` remains deterministic and self-contained.

- [ ] **Step 7: Commit Task 8**

```bash
git add web/index.html web/app.js web/styles.css web/task_experience_test.mjs web/app_test.mjs web/embed_test.go web/webgl_scene.js
git commit -m "feat: explain and update robot missions in Console"
```

### Task 9: Test distributed revision failures and guided recovery

**Files:**
- Create: `tests/e2e/test_versioned_task_faults.py`
- Modify: `tests/e2e/fleet_harness.py`
- Modify: `tests/e2e/test_fleet_faults.py`
- Modify: `scripts/run_fleet_harness.py`
- Modify: `docs/production/operations-and-failures.md` only after its creation in Task 11

**Interfaces:**
- Consumes: revision APIs, Coordinator event log/outbox, Edge process controls, world-source controls, and task experience projection.
- Produces: deterministic fault evidence for update conflicts, failover, stale evidence, retries, and resource fencing.

- [ ] **Step 1: Write the failing fault matrix**

```python
@pytest.mark.parametrize("fault", [
    "concurrent_update", "duplicate_update", "delayed_old_completion", "leader_failover_during_proposal",
    "leader_failover_during_confirmation", "edge_disconnect_running", "tool_timeout", "tool_ack_without_world_change",
    "world_source_stale", "resource_release_delay", "lower_fencing_token", "experience_refresh_gap",
])
def test_versioned_update_faults_fail_closed(fleet_stack, fault):
    result = fleet_stack.run_revision_fault(fault)
    assert result.no_completed_step_rolled_back
    assert result.no_old_revision_advanced
    assert result.no_duplicate_physical_command
    assert result.experience_matches_event_replay
    assert result.guidance_is_plain_language
```

- [ ] **Step 2: Run and verify RED**

Run: `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q tests/e2e/test_versioned_task_faults.py`

Expected: harness lacks revision update/fault controls and assertions.

- [ ] **Step 3: Add deterministic fault controls and evidence capture**

Expose process pause/restart, delayed HTTP completion, duplicate payload, stale world source, no-op runtime success, resource lease delay, and event-cursor gap controls. Record task/revision IDs, command IDs, fencing tokens, event versions, world revisions, evidence IDs, and user-safe guidance before/after each fault.

- [ ] **Step 4: Enforce invariant assertions**

Every scenario must assert immutable satisfied history, one active revision, monotonic aggregate/event/world versions, no lower-fence ownership, no acknowledgement-only completion, safe pause while evidence is stale, and replay equality after process restart.

- [ ] **Step 5: Run fault and existing distributed suites**

Run:

```bash
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q \
  tests/e2e/test_versioned_task_faults.py tests/e2e/test_fleet_faults.py tests/e2e/test_robocasa_faults.py
```

- [ ] **Step 6: Commit Task 9**

```bash
git add tests/e2e/test_versioned_task_faults.py tests/e2e/fleet_harness.py tests/e2e/test_fleet_faults.py scripts/run_fleet_harness.py
git commit -m "test: verify distributed task update recovery"
```

### Task 10: Run one real RoboCasa mid-execution update through Harness and Console

**Files:**
- Create: `tests/e2e/test_robocasa_task_updates.py`
- Modify: `tests/e2e/robocasa_harness.py`
- Modify: `scripts/run_robocasa_harness.py`
- Modify: `tests/e2e/test_robocasa_visual_twin.py`
- Update: `tests/e2e/robocasa_golden_capture_anchor.json`
- Replace with authenticated capture: `artifacts/robocasa-harness/round3/`

**Interfaces:**
- Consumes: Task 8 Console, Task 9 fault controls, authenticated browser evidence workflow, and existing two-XLeRobot scene.
- Produces: signed single-episode proof of natural language → revision preview → tools → Harness evidence → physical completion.

- [ ] **Step 1: Write failing real-stack acceptance**

```python
def test_mid_execution_update_preserves_sender_evidence_and_replans_receiver(robocasa_stack):
    task = robocasa_stack.create_and_approve(DEFAULT_HANDOFF_ZH)
    running = robocasa_stack.wait_for_step(task.id, "handoff/sender", "RUNNING")
    proposal = robocasa_stack.propose_update(task.id, running.revision, "最后放到右侧蓝色垫子上")
    assert proposal.changeSet.paused == ["handoff/sender"]
    robocasa_stack.confirm_update(task.id, proposal.revision)
    final = robocasa_stack.wait_for_success(task.id)
    assert final.currentRevision == 2
    assert final.steps[0].status == "SATISFIED"
    assert final.steps[0].harnessEvidenceIds == running.eventual_evidence_ids
    assert final.custody.owner == "environment"
    assert final.custody.fencingToken == 3
```

- [ ] **Step 2: Verify RED**

Run: `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q tests/e2e/test_robocasa_task_updates.py`

Expected: real harness cannot propose or confirm revision 2.

- [ ] **Step 3: Extend authenticated acceptance schema fail closed**

Add signed fields for original request, proposal request, revision history, change preview, step/tool activity timeline, Harness evidence references, command/revision/fencing identity, and final `task.experience.v1`. Validator rejects missing update UI state, changed satisfied evidence, raw secrets/tool arguments in user view, wrong screenshot revision, acknowledgement-only completion, or mismatched current frontend build.

- [ ] **Step 4: Capture one fresh authenticated browser episode**

Use `make robocasa-acceptance-candidate`, the loopback bearer receiver, and a controlled browser. Capture overview, update preview, waiting-safe-point, recovery, final, and visual-degraded states; bind each PNG to nonce/task/revision/world digest. Interact with left pan, right orbit, pointer-anchored zoom, selection/focus, follow/cancel-follow, four visual toggles, update text/preview/confirm, refresh, and fallback.

- [ ] **Step 5: Validate, promote, and prove clean-checkout portability**

Run:

```bash
make robocasa-acceptance-promote
make robocasa-acceptance
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q \
  tests/e2e/test_robocasa_task_updates.py tests/e2e/test_robocasa_visual_twin.py
git archive --format=tar HEAD | tar -xf - -C "$(mktemp -d)"
```

In the extracted tree run `make robocasa-acceptance`; it must pass offline with the tracked pack.

- [ ] **Step 6: Commit Task 10**

```bash
git add tests/e2e/test_robocasa_task_updates.py tests/e2e/robocasa_harness.py tests/e2e/test_robocasa_visual_twin.py scripts/run_robocasa_harness.py tests/e2e/robocasa_golden_capture_anchor.json artifacts/robocasa-harness/round3
git commit -m "test: accept versioned RoboCasa task updates"
```

### Task 11: Produce the production-delivery documentation suite

**Files:**
- Create: `docs/production/README.md`
- Create: `docs/production/architecture.md`
- Create: `docs/production/quickstart.md`
- Create: `docs/production/api-reference.md`
- Create: `docs/production/configuration-and-security.md`
- Create: `docs/production/sim-to-real.md`
- Create: `docs/production/operations-and-failures.md`
- Create: `docs/production/testing-and-acceptance.md`
- Create: `docs/production/data-contracts.md`
- Create: `tests/docs/test_production_docs.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/quickstart.md`
- Modify: `docs/user-console.md`
- Modify: `docs/protocols.md`
- Modify: `docs/production-readiness.md`

**Interfaces:**
- Consumes: the shipped code/routes/config/proto/contracts and Tasks 1–10 test evidence.
- Produces: a navigable, test-backed production handoff manual for developers, operators, integrators, and robot technicians.

- [ ] **Step 1: Write failing documentation contract tests**

```python
def test_production_document_set_is_complete(repo_root):
    required = {
        "architecture.md": ["云端 Fleet", "Local Brain", "Coordinator", "Harness Agent", "WorldModel", "数据流"],
        "quickstart.md": ["从零开始", "RoboCasa", "服务器端", "用户端", "机器人仿真", "机器人实机"],
        "api-reference.md": ["HTTP API", "WebSocket", "gRPC", "错误码", "幂等"],
        "operations-and-failures.md": ["现象", "检查", "恢复", "防止复发"],
    }
    for name, headings in required.items():
        text = (repo_root / "docs/production" / name).read_text()
        assert all(heading in text for heading in headings)

def test_documented_http_routes_match_registered_routes(repo_root):
    registered = extract_go_routes(repo_root / "fleet/server.go") | extract_go_routes(repo_root / "console/server.go")
    documented = extract_documented_routes(repo_root / "docs/production/api-reference.md")
    assert registered <= documented
```

Add tests that every internal Markdown link resolves, every documented `make` target exists, every documented script path is tracked/executable where required, production docs contain no real password/token/private-key material, and all configuration names exist in code or `.env.example` files.

- [ ] **Step 2: Run and verify RED**

Run: `.venv/bin/pytest -q tests/docs/test_production_docs.py`

Expected: the production document set does not exist.

- [ ] **Step 3: Write architecture and data-contract documentation**

`architecture.md` must explain system goals/non-goals; cloud/local deployment diagrams; module-by-module responsibility for Agent/parser/planner/task service/Coordinator/event log/outbox/leases/registry/Edge/Runtime/WorldHub/Harness/Web; create/update/execute/observe/complete/recover sequences; storage ownership; consistency model; security boundaries; scaling and remaining maturity limits. `data-contracts.md` must give field tables and annotated JSON for Task, TaskRevision, RevisionStep, ToolActivity, DomainEvent, Command, ObservationEnvelope, WorldSnapshot, resource custody, Harness verdict, cursor/fencing/idempotency semantics, compatibility and migration.

- [ ] **Step 4: Write zero-to-running quickstart and sim-to-real guide**

`quickstart.md` must provide verified prerequisites and commands for repository setup, RoboCasa installation, asset generation, cloud stack, Local Brain, user login, task create/approve/update, browser controls, logs/stop/restart, success checks, and cleanup. Separate server/operator/simulation/real-robot sections. `sim-to-real.md` must show how to implement/register a tool adapter, advertise user-facing metadata, register observation sources, publish robot/environment state, establish map frames and transform revisions, calibrate XLeRobot, provision mTLS, enforce safety/fencing/idempotency, run dry-run/bench/limited-workspace acceptance, and roll back.

- [ ] **Step 5: Write exhaustive API reference**

For every public Fleet/Local HTTP route document method/path/auth/request/response/status/error/idempotency/example curl. Document world/task WebSockets including ticket, cursor, resync, reconnect and ordering. Document FleetGateway and RobotRuntime gRPC services/messages, catalogue/observation registration, and which interfaces are internal. Include revision proposal/confirm/history/experience examples and `409 REVISION_CONFLICT` recovery.

- [ ] **Step 6: Write operations, security, configuration, testing, and release manuals**

`operations-and-failures.md` uses one table per scenario with detection signal, user-visible behavior, safety invariant, first probes, logs/queries, automated recovery, manual recovery, data that must not be edited, escalation and prevention. Cover auth/cert/clock/DNS/TLS, MySQL/Redis/eventlog/outbox, leader lease, duplicate/out-of-order/gap, robot offline, runtime mismatch, tool/catalog mismatch, timeout/no world change, stale/contradictory observations, map/transform mismatch, custody/fencing conflict, update safe-point conflict, Harness timeout, WebSocket/fallback, GLB/hash/CSP, disk/memory/CPU/GPU, browser performance, emergency stop, process crash and disaster recovery.

`configuration-and-security.md` inventories every env/config variable by component, default, required/secret, validation, rotation and restart effect; documents RBAC, mTLS, operator JWT/tickets, network ports, secret storage, backups, audit retention and hardening. `testing-and-acceptance.md` maps unit/contract/integration/chaos/RoboCasa/browser/real-hardware tests to commands, expected results, evidence pack trust model, release checklist and honest display-vs-submission FPS meaning.

- [ ] **Step 7: Add documentation index and compatibility links**

`docs/production/README.md` provides “我是谁/我该读什么” paths for owner, developer, cloud operator, robot technician, safety reviewer, and integrator. Existing docs become concise entry points with links to the authoritative production pages; preserve historical design/spec links and clearly label them as design history rather than current operating instructions.

- [ ] **Step 8: Run documentation contracts and command smoke checks**

Run:

```bash
.venv/bin/pytest -q tests/docs/test_production_docs.py tests/install/test_readme_contract.py tests/deploy/test_deployment_contract.py
bash -n scripts/*.sh scripts/install/*.sh
go test ./tests/architecture/... -count=1
```

Expected: all links/routes/configs/commands are real, no secrets are present, and architecture boundaries remain valid.

- [ ] **Step 9: Commit Task 11**

```bash
git add docs/production tests/docs README.md docs/architecture.md docs/quickstart.md docs/user-console.md docs/protocols.md docs/production-readiness.md
git commit -m "docs: deliver Robot AgentOS production manuals"
```

### Task 12: Run full release verification and close the delivery

**Files:**
- Modify only when verification exposes an in-scope defect: implementation/test/document files from Tasks 1–11
- Create: `docs/production/release-evidence.md`

**Interfaces:**
- Consumes: every deliverable and test command from Tasks 1–11.
- Produces: reproducible release evidence, current limitations, and a clean reviewed branch.

- [ ] **Step 1: Run generated-code, formatting, and static gates**

Run:

```bash
make generate-check
make build
make lint
git diff --check
```

Expected: generated bindings current, binaries build, format/lint clean, no whitespace errors.

- [ ] **Step 2: Run complete Go, Python, Web, and documentation suites**

Run:

```bash
make test
PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q \
  tests/e2e/test_robocasa_task_updates.py tests/e2e/test_versioned_task_faults.py \
  tests/e2e/test_robocasa_faults.py tests/e2e/test_robocasa_visual_twin.py
cd web && npm ci && npm run build && npm test
```

Expected: every suite passes once in the declared environment; record counts, duration, commit, and bundle hash.

- [ ] **Step 3: Revalidate signed acceptance and clean checkout**

Run:

```bash
make robocasa-acceptance
tmpdir=$(mktemp -d)
git archive --format=tar HEAD | tar -xf - -C "$tmpdir"
(cd "$tmpdir" && make robocasa-acceptance)
```

Expected: both current and clean archive validate the tracked signed evidence without network access or ignored local files.

- [ ] **Step 4: Perform manual browser acceptance**

Start the real RoboCasa stack and verify login; world/visual status; full kitchen and two XLeRobot models; task understanding; ordered mission ribbon; human-readable capability/activity; collapsed professional details; update preview/confirm; safe-point waiting; Harness evidence; final completion; refresh recovery; pointer controls; responsive rail; keyboard/focus; and visual-degraded Canvas fallback. Capture only evidence bound by the authenticated acceptance runner.

- [ ] **Step 5: Write release evidence without overstating maturity**

`release-evidence.md` records commit/hash, platform, test counts, acceptance run/task/nonce references, verified scenarios, operational commands, documentation inventory, known production limits, and the separate real-hardware acceptance still required. It explicitly distinguishes automated renderer submission capacity from actual display rAF and simulation proof from physical safety certification.

- [ ] **Step 6: Request independent review and resolve findings with TDD**

Review scopes: task/repository CAS and immutability; coordinator safe-point/fencing/failover; Harness truth boundary; secret-safe experience API; browser revision ordering/accessibility; signed acceptance; documentation accuracy/coverage. Every accepted finding starts with a reproducing test, then a minimal fix and focused/full regression.

- [ ] **Step 7: Final clean-state verification and commit**

Run:

```bash
git status --short
git diff --check
```

Expected: no untracked dependencies, temporary credentials, running acceptance sessions, or unstaged changes.

```bash
git add docs/production/release-evidence.md
git commit -m "docs: record versioned task release evidence"
```

---

## Plan self-review result

- Spec coverage: create/update/recovery UX, immutable revisions, CAS/idempotency, stable steps, tool activity, Harness evidence, safe checkpoints, fencing, failover, cloud/local APIs, responsive Web, RoboCasa acceptance, and production manuals each map to Tasks 1–12.
- Documentation coverage: the requested architecture, quickstart, complete interfaces, and distributed exception runbook are present; production delivery additionally includes configuration/security, data contracts, sim-to-real integration, testing/acceptance, documentation index, and release evidence.
- Type consistency: `TaskRevision`, `RevisionRecord`, `RevisionCommit`, `RevisionBasis`, `TaskExperience`, revision/aggregate/step/command/fencing identity, and API field names are defined once and consumed under the same names in dependent tasks.
- Placeholder scan: no deferred implementation markers or unspecified error/test steps remain.

## Execution handoff

The user selected inline execution in the current task: after this plan is committed, use `superpowers:executing-plans`, `superpowers:test-driven-development`, and `superpowers:verification-before-completion`. Execute Tasks 1–12 in order with the review/verification gates written above.
