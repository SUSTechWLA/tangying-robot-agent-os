# Distributed Robot AgentOS World Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable cloud-first distributed Robot AgentOS vertical slice where two MuJoCo robots hand one block through a shared handoff zone, the cloud advances only from versioned environment evidence, the Console renders the live world interactively, and deterministic fault scenarios preserve safety and consistency.

**Architecture:** Keep the current Go/Python/static-Web stack and existing RobotRuntime boundary. Add immutable tool catalogs, versioned observation envelopes, a deterministic world projector, persistent coordinator state, object leases with fencing, and an outbox-backed realtime stream. The cloud and Local Brain use the same contracts; MuJoCo and future real robots differ only by registered tool, observation, and transform adapters.

**Tech Stack:** Go 1.26, Python 3.11, MuJoCo 3.3, gRPC/protobuf, MySQL 8, Redis Streams, SQLite, Gorilla WebSocket, vanilla JavaScript Canvas, Node test runner, pytest.

---

## File responsibility map

### New contract packages

- `core/toolcatalog/catalog.go`: canonical runtime tool catalog, validation, deterministic SHA-256 revision.
- `core/observation/catalog.go`: independent observation-source catalog, transform/freshness metadata and deterministic revision.
- `core/observation/envelope.go`: versioned observation and frame-reference envelope validation.
- `core/worldmodel/types.go`: world snapshot, robot/entity/resource state, freshness and delta types.
- `core/worldmodel/projector.go`: deterministic reducer with source-sequence dedupe, UNKNOWN/stale handling and monotonic revision.
- `core/worldmodel/predicate.go`: task predicates used for verified handoff advancement.
- `core/worldmodel/reader.go`: stable snapshot/delta consumer port for Console and future Harness Agent implementations.

### Durable cloud packages

- `fleet/eventlog/store.go`: domain event, outbox and checkpoint ports.
- `fleet/eventlog/memory.go`: deterministic test/dev implementation.
- `fleet/coordinator/store.go`: persisted task-graph snapshot contract.
- `fleet/coordinator/memory_store.go`: dev/test state store.
- `fleet/lease/manager.go`: leader/resource lease contract and fencing grant.
- `fleet/lease/memory.go`: deterministic memory implementation.
- `fleet/redis/lease.go`: Redis Lua implementation for monotonic fencing and ownership.
- `fleet/worldhub/hub.go`: thread-safe world projector façade and bounded delta subscriptions.
- `middleware/sqlite/fleet.go`: SQLite event/state/outbox/checkpoint adapter for Local Brain and restartable e2e tests.
- `fleet/mysql/coordination.go`: production MySQL event/state/outbox/checkpoint adapter.

### Edge, protocol and simulation

- `edge/worker/observation.go`: RobotRuntime observations → `ObservationEnvelope` stream.
- `sim/mujoco/tangying_sim/shared_handoff.py`: simulation-only shared-object bridge between two runtime cells.
- `sim/mujoco/tangying_sim/fleet_server.py`: two gRPC RobotRuntime servers sharing the handoff bridge.
- `sim/mujoco/assets/xlerobot_handoff_r1.xml`: sender cell with reachable shared handoff zone.
- `sim/mujoco/assets/xlerobot_handoff_r2.xml`: receiver cell in the same world frame.

### Realtime UI and verification

- `web/world_view.js`: semantic 3D camera, projection, drawing and pointer interactions.
- `web/world_view_test.mjs`: pure camera and interaction contract tests.
- `tests/e2e/fleet_harness.py`: restartable Fleet/MuJoCo/worker process harness and evidence collector.
- `tests/e2e/test_fleet_handoff.py`: normal shared-block handoff.
- `tests/e2e/test_fleet_faults.py`: deterministic distributed fault matrix.
- `scripts/run_fleet_harness.py`: reproducible evidence-package runner.

## Task 1: Freeze tool catalogs and extend the command fencing contract

**Files:**

- Create: `core/toolcatalog/catalog.go`
- Create: `core/toolcatalog/catalog_test.go`
- Modify: `edge/runtime/runtime.go`
- Modify: `proto/robot/v1/robot.proto`
- Modify: `edge/robotclient/client.go`
- Modify: `edge/robotclient/client_test.go`
- Modify: `sim/mujoco/tangying_sim/server.py`
- Modify: `sim/mujoco/tests/test_server.py`
- Regenerate: `gen/go/robot/v1/robot.pb.go`
- Regenerate: `python/tangying_robot_proto/robot/v1/robot_pb2.py`

- [ ] **Step 1: Write failing canonical-catalog and wire-mapping tests**

```go
func TestRevisionIgnoresAdvertisementOrder(t *testing.T) {
	tools := []toolcatalog.Tool{
		{Name: "manipulation.place", InputParameters: []string{"destinationId"}, SideEffectClass: toolcatalog.PhysicalAtomic},
		{Name: "observe_scene", SideEffectClass: toolcatalog.ReadOnly},
	}
	reversed := []toolcatalog.Tool{tools[1], tools[0]}
	if toolcatalog.Revision(tools) != toolcatalog.Revision(reversed) {
		t.Fatal("equivalent catalogs must have one revision")
	}
	if tools[0].Name != "manipulation.place" {
		t.Fatal("revision calculation mutated the runtime advertisement")
	}
}

func TestCommandToProtoCarriesFrozenExecutionIdentity(t *testing.T) {
	command := runtime.Command{
		SchemaVersion: "robot.v1", CommandID: "cmd-1", TaskID: "task-1", RobotID: "robot-2",
		Capability: runtime.CapabilityPick, CatalogRevision: strings.Repeat("a", 64),
		WorldRevisionBasis: 42, ResourceID: "red-block", FencingToken: 9,
		Deadline: time.Now().Add(time.Minute), Lease: time.Second,
		IdempotencyKey: "task-1/pick", SafetyProfile: "simulation",
	}
	wire, err := commandToProto(command, "simulation")
	if err != nil { t.Fatal(err) }
	if wire.RobotId != "robot-2" || wire.CatalogRevision != command.CatalogRevision ||
		wire.WorldRevisionBasis != 42 || wire.ResourceId != "red-block" || wire.FencingToken != 9 {
		t.Fatalf("wire identity lost: %#v", wire)
	}
}
```

Add a Python test that executes a physical skill with a stale catalog revision or fencing token and expects `TOOL_CATALOG_STALE` or `FENCING_TOKEN_STALE` before `_dispatch` runs.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
go test ./core/toolcatalog ./edge/robotclient -run 'Revision|FrozenExecutionIdentity' -v
.venv/bin/pytest sim/mujoco/tests/test_server.py -k 'catalog or fencing' -q
```

Expected: Go fails because `core/toolcatalog` and the new command fields do not exist; Python fails because the runtime does not validate catalog/fencing identity.

- [ ] **Step 3: Implement the minimal canonical catalog**

```go
package toolcatalog

type SideEffectClass string

const (
	ReadOnly       SideEffectClass = "read_only"
	Idempotent     SideEffectClass = "idempotent"
	PhysicalAtomic SideEffectClass = "physical_atomic"
	Emergency      SideEffectClass = "emergency"
)

type Tool struct {
	Name             string          `json:"name"`
	Description      string          `json:"description,omitempty"`
	InputParameters  []string        `json:"inputParameters,omitempty"`
	OutputParameters []string        `json:"outputParameters,omitempty"`
	SideEffectClass  SideEffectClass `json:"sideEffectClass"`
	SafetyLevel      string          `json:"safetyLevel,omitempty"`
	Available        bool            `json:"available"`
}

type Snapshot struct {
	RobotID       string `json:"robotId"`
	RunnerID      string `json:"runnerId,omitempty"`
	AdapterID     string `json:"adapterId"`
	AdapterVersion string `json:"adapterVersion,omitempty"`
	Revision      string `json:"revision"`
	Tools         []Tool `json:"tools"`
}

func Revision(tools []Tool) string {
	canonical := make([]Tool, len(tools))
	for index, tool := range tools {
		canonical[index] = tool
		canonical[index].InputParameters = append([]string(nil), tool.InputParameters...)
		canonical[index].OutputParameters = append([]string(nil), tool.OutputParameters...)
	}
	sort.Slice(canonical, func(i, j int) bool { return canonical[i].Name < canonical[j].Name })
	for i := range canonical {
		sort.Strings(canonical[i].InputParameters)
		sort.Strings(canonical[i].OutputParameters)
	}
	wire, _ := json.Marshal(canonical)
	sum := sha256.Sum256(wire)
	return hex.EncodeToString(sum[:])
}
```

Add `CatalogRevision`, `WorldRevisionBasis`, `ResourceID`, and `FencingToken` to `runtime.Command`; add `CatalogRevision` and `AdapterVersion` to `runtime.Snapshot`. Extend `SkillCommand` with field numbers 12–16 and `RuntimeInfo` with field numbers 12–13; preserve all existing field numbers.

- [ ] **Step 4: Generate protocol code and implement Go/Python mapping**

Run:

```bash
bash scripts/generate-proto.sh
```

Update `commandToProto`, `snapshotFromProto`, MuJoCo `_capability_infos`, `_fingerprint`, and `_validate`. Runtime validation order must be schema/deadline/lease/idempotency, then catalog revision, then fencing, then safety readiness. Read-only tools may use fencing token zero; `physical_atomic` tools require a non-zero token when `resource_id` is present.

- [ ] **Step 5: Verify GREEN and compatibility**

Run:

```bash
go test ./core/toolcatalog ./edge/runtime ./edge/robotclient -v
.venv/bin/pytest sim/mujoco/tests/test_server.py -q
make generate-check
```

Expected: all pass, generated files are current, old commands without resource ownership remain valid for local single-robot tasks.

- [ ] **Step 6: Commit only Task 1 files**

```bash
git add core/toolcatalog edge/runtime/runtime.go proto/robot/v1/robot.proto edge/robotclient sim/mujoco/tangying_sim/server.py sim/mujoco/tests/test_server.py gen/go/robot/v1 python/tangying_robot_proto/robot/v1
git commit -m "feat: freeze robot tool catalogs and fencing identity"
```

## Task 2: Build the observation contract and deterministic world projector

**Files:**

- Create: `core/observation/catalog.go`
- Create: `core/observation/catalog_test.go`
- Create: `core/observation/envelope.go`
- Create: `core/observation/envelope_test.go`
- Create: `core/worldmodel/types.go`
- Create: `core/worldmodel/projector.go`
- Create: `core/worldmodel/projector_test.go`
- Create: `core/worldmodel/predicate.go`
- Create: `core/worldmodel/predicate_test.go`
- Create: `core/worldmodel/reader.go`

- [ ] **Step 1: Write failing validation, ordering, stale and predicate tests**

```go
func TestProjectorRejectsDuplicateAndOutOfOrderSourceSequence(t *testing.T) {
	p := worldmodel.NewProjector("world-test", time.Second)
	now := time.Unix(100, 0).UTC()
	first := observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: "obs-2", WorldID: "world-test",
		SourceID: "robot-1/sim", RobotID: "robot-1", SourceType: observation.SourceSimGroundTruth,
		SourceSequence: 2, ObservedAt: now, ReceivedAt: now, FrameID: "world",
		TransformRevision: "scene-v1", Kind: observation.EntityUpsert,
		Payload: observation.EntityPayload{EntityID: "red-block", Category: "block", Pose: []float64{0.5, 0.3, 0.8}}, Confidence: 1,
	}
	if _, accepted, err := p.Apply(first); err != nil || !accepted { t.Fatalf("first: %v %v", accepted, err) }
	older := first
	older.ObservationID = "obs-1"
	older.SourceSequence = 1
	older.Payload = observation.EntityPayload{EntityID: "red-block", Category: "block", Pose: []float64{-9, 0.3, 0.8}}
	if snapshot, accepted, err := p.Apply(older); err != nil || accepted || snapshot.Entities["red-block"].Pose[0] != 0.5 {
		t.Fatalf("old event changed world: %#v accepted=%v err=%v", snapshot, accepted, err)
	}
}

func TestMissingFreshEvidenceIsUnknown(t *testing.T) {
	p := seededProjector(t)
	p.SetNow(func() time.Time { return time.Unix(200, 0).UTC() })
	result := worldmodel.EntityInside("red-block", "handoff-zone", 500*time.Millisecond).Evaluate(p.Snapshot())
	if result.Status != worldmodel.PredicateUnknown { t.Fatalf("predicate=%#v", result) }
}

func TestObservationCatalogRevisionIsIndependentFromToolCatalog(t *testing.T) {
	sources := []observation.SourceDescriptor{
		{SourceID: "scene", Kind: observation.EntityUpsert, FrameID: "camera", TransformRevision: "cal-7", MaxAge: 500 * time.Millisecond},
		{SourceID: "proprioception", Kind: observation.RobotStateUpsert, FrameID: "base", TransformRevision: "urdf-3", MaxAge: 100 * time.Millisecond},
	}
	revision, err := observation.CatalogRevision(sources)
	if err != nil || len(revision) != 64 { t.Fatalf("revision=%q err=%v", revision, err) }
	sources[0], sources[1] = sources[1], sources[0]
	reordered, _ := observation.CatalogRevision(sources)
	if reordered != revision { t.Fatalf("%s != %s", reordered, revision) }
}
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
go test ./core/observation ./core/worldmodel -v
```

Expected: packages do not exist.

- [ ] **Step 3: Implement `ObservationEnvelope` validation**

Keep the observation catalog separate from the tool catalog. It describes what can become world evidence, not what can be executed:

```go
type SourceDescriptor struct {
	SourceID          string        `json:"sourceId"`
	Kind              Kind          `json:"kind"`
	FrameID           string        `json:"frameId"`
	TransformRevision string        `json:"transformRevision"`
	MaxRateHz         float64       `json:"maxRateHz"`
	MaxAge            time.Duration `json:"maxAge"`
	Required          bool          `json:"required"`
}

type Catalog struct {
	RobotID string             `json:"robotId"`
	Revision string            `json:"revision"`
	Sources []SourceDescriptor `json:"sources"`
}
```

`CatalogRevision` validates non-empty source/frame/transform identifiers, positive freshness bounds, rejects duplicate source IDs, deep-copies nested fields, sorts by `SourceID`, and hashes canonical JSON. Its revision is never reused as a tool catalog revision.

```go
type Envelope struct {
	SchemaVersion     string            `json:"schemaVersion"`
	ObservationID     string            `json:"observationId"`
	WorldID           string            `json:"worldId"`
	SourceID          string            `json:"sourceId"`
	RobotID           string            `json:"robotId,omitempty"`
	SourceType        SourceType        `json:"sourceType"`
	SourceSequence    uint64            `json:"sourceSequence"`
	ObservedAt        time.Time         `json:"observedAt"`
	ReceivedAt        time.Time         `json:"receivedAt"`
	FrameID           string            `json:"frameId"`
	TransformRevision string            `json:"transformRevision"`
	Kind              Kind              `json:"kind"`
	Payload           any               `json:"payload,omitempty"`
	FrameRef          *FrameRef         `json:"frameRef,omitempty"`
	Confidence        float64           `json:"confidence"`
	Quality           Quality           `json:"quality"`
	Causation         Causation         `json:"causation,omitempty"`
	Provenance        Provenance        `json:"provenance"`
}

func (e Envelope) Validate() error {
	if e.SchemaVersion != "world.observation.v1" || e.ObservationID == "" || e.WorldID == "" || e.SourceID == "" {
		return ErrInvalidEnvelope
	}
	if e.SourceSequence == 0 || e.ObservedAt.IsZero() || e.ReceivedAt.IsZero() || e.FrameID == "" || e.TransformRevision == "" {
		return ErrInvalidEnvelope
	}
	if e.Confidence < 0 || e.Confidence > 1 { return ErrInvalidConfidence }
	if e.FrameRef != nil && e.Payload != nil { return ErrAmbiguousPayload }
	return nil
}
```

- [ ] **Step 4: Implement projector and predicates**

`Projector.Apply` must validate, dedupe by observation ID, reject `sourceSequence <= last[sourceID]`, reject transform revisions that conflict with the accepted source transform, update typed robot/entity/resource state, and increment the global revision exactly once for every accepted event. `Snapshot()` must deep-copy maps and derive freshness without mutating stored facts.

Define the future Harness Agent boundary in `reader.go` without implementing a Harness Agent yet:

```go
type Reader interface {
	Snapshot(context.Context) (Snapshot, error)
	Subscribe(context.Context, uint64) (<-chan Delta, error)
}
```

`worldhub.Hub` in Task 4 must satisfy this interface. Consumers receive only accepted revisions; a retention gap returns `ErrResyncRequired` so a Harness Agent cannot reason from silently incomplete state.

```go
func (p *Projector) Apply(event observation.Envelope) (Snapshot, bool, error) {
	if err := event.Validate(); err != nil { return Snapshot{}, false, err }
	p.mu.Lock()
	defer p.mu.Unlock()
	if _, seen := p.observationIDs[event.ObservationID]; seen { return p.snapshotLocked(), false, nil }
	if event.SourceSequence <= p.sourceSequence[event.SourceID] { return p.snapshotLocked(), false, nil }
	if err := p.reduce(event); err != nil { return p.snapshotLocked(), false, err }
	p.observationIDs[event.ObservationID] = struct{}{}
	p.sourceSequence[event.SourceID] = event.SourceSequence
	p.revision++
	p.cursor = event.ObservationID
	return p.snapshotLocked(), true, nil
}
```

Predicates must include `EntityInside`, `EntityStable`, `RobotHeld`, `ResourceOwner`, `SourceFresh`, plus `All`. Each returns `TRUE`, `FALSE`, or `UNKNOWN` with evidence references and a stable reason code.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
go test ./core/observation ./core/worldmodel -race -v
```

Expected: all tests pass under the race detector; duplicate and out-of-order inputs never change revision.

- [ ] **Step 6: Commit Task 2**

```bash
git add core/observation core/worldmodel
git commit -m "feat: add versioned robot world projection"
```

## Task 3: Persist coordinator state, domain events, checkpoints and outbox

**Files:**

- Create: `fleet/eventlog/store.go`
- Create: `fleet/eventlog/memory.go`
- Create: `fleet/eventlog/store_test.go`
- Create: `fleet/coordinator/store.go`
- Create: `fleet/coordinator/memory_store.go`
- Create: `fleet/lease/manager.go`
- Create: `fleet/lease/memory.go`
- Create: `fleet/lease/manager_test.go`
- Create: `fleet/redis/lease.go`
- Create: `middleware/sqlite/fleet.go`
- Create: `middleware/sqlite/fleet_test.go`
- Create: `fleet/mysql/coordination.go`
- Modify: `fleet/mysql/store.go`
- Modify: `fleet/coordinator/coordinator.go`
- Modify: `fleet/coordinator/coordinator_test.go`

- [ ] **Step 1: Write failing persistence, replay, CAS and fencing tests**

```go
func TestCoordinatorRestoresRunningIntentWithoutDoubleAdvance(t *testing.T) {
	ctx := context.Background()
	store := eventlog.NewMemoryStore()
	leases := lease.NewMemoryManager()
	first := newCoordinatorFixture(t, store, leases)
	node, err := first.NextIntent(ctx, "task-1", "robot-1")
	if err != nil { t.Fatal(err) }
	restarted := newCoordinatorFixture(t, store, leases)
	snapshot, err := restarted.Snapshot(ctx, "task-1")
	if err != nil { t.Fatal(err) }
	if snapshot.Intents[0].Status != coordinator.StatusRunning || snapshot.Intents[0].Claimed != node.Claimed {
		t.Fatalf("restart lost claim: %#v", snapshot.Intents[0])
	}
}

func TestOnlyFencedLeaderCanAdvance(t *testing.T) {
	store := eventlog.NewMemoryStore()
	leases := lease.NewMemoryManager()
	first := newCoordinatorFixture(t, store, leases)
	second := newCoordinatorFixture(t, store, leases)
	first.BecomeLeader(context.Background(), "coordinator-a")
	second.BecomeLeader(context.Background(), "coordinator-b")
	if _, err := first.NextIntent(context.Background(), "task-1", "robot-1"); !errors.Is(err, coordinator.ErrLeadershipLost) {
		t.Fatalf("stale leader advanced: %v", err)
	}
}

func TestTransferInvalidatesPreviousFencingToken(t *testing.T) {
	m := lease.NewMemoryManager()
	first, _ := m.Acquire(context.Background(), "object/red-block", "robot-1", time.Minute)
	second, err := m.Transfer(context.Background(), "object/red-block", "robot-1", "robot-2", first.Token, time.Minute)
	if err != nil { t.Fatal(err) }
	if second.Token <= first.Token { t.Fatalf("tokens %d -> %d", first.Token, second.Token) }
	if m.Validate(context.Background(), "object/red-block", "robot-1", first.Token) == nil {
		t.Fatal("old fencing token remained valid")
	}
}
```

SQLite tests must close/reopen a file database and assert graph state, events, checkpoint and pending outbox survive. MySQL schema tests must assert `fleet_domain_events`, `fleet_graph_states`, `fleet_checkpoints`, and `fleet_outbox` are created in the same initialization path as `robot_tasks`.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
go test ./fleet/eventlog ./fleet/lease ./fleet/coordinator ./middleware/sqlite ./fleet/mysql -run 'Restores|Fencing|Outbox|Checkpoint' -v
```

Expected: new packages/interfaces are missing and restart loses in-memory intent state.

- [ ] **Step 3: Implement store and event contracts**

```go
type DomainEvent struct {
	EventID         string         `json:"eventId"`
	AggregateType  string         `json:"aggregateType"`
	AggregateID    string         `json:"aggregateId"`
	AggregateVersion uint64       `json:"aggregateVersion"`
	EventType       string         `json:"eventType"`
	Payload         map[string]any `json:"payload,omitempty"`
	IdempotencyKey  string         `json:"idempotencyKey"`
	CausationID     string         `json:"causationId,omitempty"`
	CorrelationID   string         `json:"correlationId,omitempty"`
	OccurredAt      time.Time      `json:"occurredAt"`
}

type Store interface {
	LoadState(context.Context, string) (AggregateState, bool, error)
	Commit(context.Context, CommitRequest) error
	ListEvents(context.Context, string, string, uint64, int) ([]DomainEvent, error)
	LatestCheckpoint(context.Context, string) (Checkpoint, bool, error)
	ClaimOutbox(context.Context, int) ([]OutboxEntry, error)
	AckOutbox(context.Context, string) error
}

type CommitRequest struct {
	State           AggregateState `json:"state"`
	ExpectedVersion uint64         `json:"expectedVersion"`
	Events          []DomainEvent  `json:"events"`
	Outbox          []OutboxEntry  `json:"outbox"`
	Checkpoint      *Checkpoint    `json:"checkpoint,omitempty"`
}
```

`Commit` is the only mutation entry point: state CAS, idempotent domain events, outbox rows, and optional checkpoint succeed or roll back together. It returns `ErrVersionConflict` when `ExpectedVersion` does not match. `fleet/coordinator/store.go` is a typed JSON wrapper around this store; it must not perform a second independent write. Memory, SQLite and MySQL adapters use the same conformance tests.

- [ ] **Step 4: Implement lease manager and Redis adapter**

```go
type Grant struct {
	ResourceID string    `json:"resourceId"`
	Owner      string    `json:"owner"`
	Token      uint64    `json:"token"`
	ExpiresAt  time.Time `json:"expiresAt"`
}

type Manager interface {
	Acquire(context.Context, string, string, time.Duration) (Grant, error)
	Renew(context.Context, string, string, uint64, time.Duration) (Grant, error)
	Transfer(context.Context, string, string, string, uint64, time.Duration) (Grant, error)
	Validate(context.Context, string, string, uint64) error
	Release(context.Context, string, string, uint64) error
}
```

Use one Redis Lua script per mutation so owner check, token `INCR`, and TTL update are atomic. Never derive fencing from wall time.

The coordinator acquires `leader/<world-id>` through the same manager. Every state mutation validates the current leader owner/token immediately before `Store.Commit`; loss of leadership returns `ErrLeadershipLost`, stops new dispatch, and lets the new leader replay checkpoint plus events. Resource grants and the leader grant use different keys and TTL policies.

- [ ] **Step 5: Refactor coordinator to persist before publishing**

Every claim/complete/fail/reclaim mutation must:

1. load persisted graph state;
2. compute the next state and domain event;
3. call one `Store.Commit` transaction for state CAS, event, outbox and checkpoint;
4. publish the outbox entry to the robot queue;
5. acknowledge the outbox entry only after queue success.

Existing constructors remain as dev helpers by wiring memory adapters. Remove the claim that coordinator state needs no durable store.

- [ ] **Step 6: Verify GREEN, restart and race behavior**

Run:

```bash
go test ./fleet/eventlog ./fleet/lease ./fleet/coordinator ./middleware/sqlite ./fleet/mysql -race -v
```

Expected: restart preserves RUNNING state; concurrent claims yield one winner; a stale leader cannot advance; old tokens fail; outbox survives publisher failure.

- [ ] **Step 7: Commit Task 3**

```bash
git add fleet/eventlog fleet/coordinator fleet/lease fleet/redis/lease.go middleware/sqlite/fleet.go middleware/sqlite/fleet_test.go fleet/mysql
git commit -m "feat: persist fleet coordination and fencing leases"
```

## Task 4: Stream versioned observations from Edge into a shared WorldHub

**Files:**

- Create: `fleet/worldhub/hub.go`
- Create: `fleet/worldhub/hub_test.go`
- Create: `edge/worker/observation.go`
- Create: `edge/worker/observation_test.go`
- Modify: `proto/fleet/v1/fleet.proto`
- Modify: `edge/cloudclient/client.go`
- Modify: `edge/cloudclient/link.go`
- Modify: `fleet/gateway/gateway.go`
- Modify: `fleet/gateway/gateway_test.go`
- Modify: `fleet/registry/registry.go`
- Modify: `fleet/registry/registry_test.go`
- Modify: `edge/worker/worker.go`
- Modify: `edge/worker/telemetry.go`
- Modify: `cmd/edge-worker/main.go`
- Regenerate: `gen/go/fleet/v1/fleet.pb.go`

- [ ] **Step 1: Write failing conversion and WorldHub subscription tests**

```go
func TestBuildObservationsUsesMonotonicPerSourceSequence(t *testing.T) {
	w := workerWithRuntimeSnapshots(t, twoSnapshots())
	first, err := w.BuildObservations(context.Background())
	if err != nil { t.Fatal(err) }
	second, err := w.BuildObservations(context.Background())
	if err != nil { t.Fatal(err) }
	if first[0].SourceSequence+1 != second[0].SourceSequence { t.Fatalf("sequences %d %d", first[0].SourceSequence, second[0].SourceSequence) }
	if first[0].Provenance.Adapter != "mujoco" || first[0].TransformRevision == "" { t.Fatalf("bad envelope %#v", first[0]) }
}

func TestHubPublishesOnlyAcceptedWorldRevisions(t *testing.T) {
	hub := worldhub.New("world-test", time.Second, 64)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sub, err := hub.Subscribe(ctx, 0)
	if err != nil { t.Fatal(err) }
	event := validEntityObservation(1)
	if _, err := hub.Ingest(context.Background(), event); err != nil { t.Fatal(err) }
	if _, err := hub.Ingest(context.Background(), event); err != nil { t.Fatal(err) }
	delta := receiveDelta(t, sub)
	if delta.Revision != 1 { t.Fatalf("revision=%d", delta.Revision) }
	assertNoSecondDelta(t, sub)
}
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
go test ./fleet/worldhub ./edge/worker ./fleet/gateway -run 'Observations|HubPublishes' -v
```

Expected: `worldhub` and observation conversion do not exist.

- [ ] **Step 3: Extend Fleet protobuf without removing telemetry compatibility**

Add `ObservationEnvelope`, `ObservationEntity`, `ObservationQuality`, `ObservationProvenance`, `FrameReference`, and a new `LinkMessage.observation` oneof member. Add `tool_catalog_revision`, `tool_catalog`, `observation_catalog_revision`, `observation_sources`, and `adapter_version` to `RegisterRequest`. Preserve `TelemetrySample` during migration. The two revision fields must never alias or overwrite one another.

```proto
message ObservationEnvelope {
  string schema_version = 1;
  string observation_id = 2;
  string world_id = 3;
  string source_id = 4;
  string robot_id = 5;
  string source_type = 6;
  uint64 source_sequence = 7;
  int64 observed_unix_ms = 8;
  int64 received_unix_ms = 9;
  string frame_id = 10;
  string transform_revision = 11;
  string kind = 12;
  google.protobuf.Struct payload = 13;
  double confidence = 14;
  string task_id = 15;
  string command_id = 16;
}
```

- [ ] **Step 4: Implement observation publication and WorldHub ingestion**

`edge/worker/observation.go` emits separate robot-state and entity events at the configured rate. Frame bytes continue through the existing frame cache initially, while the observation stream carries freshness and frame metadata. `Link.SendObservation` over mTLS is the production path. `POST /v1/observations` exists only for local/e2e fallback, defaults disabled in cloud deployment, and requires a short-lived robot-bound session established by the mTLS gateway. The gateway authenticates `robot_id` against the certificate/session before ingestion.

Extend `registry.Device` with separate immutable `ToolCatalogRevision`, `ToolCatalog`, `ObservationCatalogRevision`, and `ObservationSources` fields. On reconnect, a changed revision creates a new advertised snapshot; tasks already claimed keep their frozen revision and fail closed if the runtime no longer serves it.

- [ ] **Step 5: Verify GREEN and legacy telemetry compatibility**

Run:

```bash
bash scripts/generate-proto.sh
go test ./fleet/worldhub ./edge/worker ./edge/cloudclient ./fleet/gateway -race -v
make generate-check
```

Expected: accepted observations create one delta each; telemetry endpoints and frame cache tests remain green.

- [ ] **Step 6: Commit Task 4**

```bash
git add fleet/worldhub edge/worker edge/cloudclient fleet/gateway fleet/registry cmd/edge-worker proto/fleet/v1 gen/go/fleet/v1 scripts/generate-proto.sh
git commit -m "feat: stream edge observations into the fleet world"
```

## Task 5: Create a true logical shared-block MuJoCo handoff fixture

**Files:**

- Create: `sim/mujoco/tangying_sim/shared_handoff.py`
- Create: `sim/mujoco/tangying_sim/fleet_server.py`
- Create: `sim/mujoco/tests/test_shared_handoff.py`
- Create: `sim/mujoco/assets/xlerobot_handoff_r1.xml`
- Create: `sim/mujoco/assets/xlerobot_handoff_r2.xml`
- Modify: `sim/mujoco/tangying_sim/world.py`
- Modify: `sim/mujoco/tangying_sim/tools.py`
- Modify: `sim/mujoco/tangying_sim/model.py`
- Modify: `scripts/gen_scene_variant.py`
- Modify: `scripts/fleet-sim.sh`

- [ ] **Step 1: Write failing shared-object tests**

```python
def test_one_logical_block_moves_from_sender_to_receiver_cell():
    bridge, sender, receiver = seeded_handoff_worlds(seed=7)
    assert sender.has_object("red-block")
    assert not receiver.has_object("red-block")

    assert sender.pick("red-block").success
    assert sender.place("handoff-zone").success
    assert bridge.owner("red-block") == "environment"
    assert not sender.has_object("red-block")
    assert receiver.has_object("red-block")

    assert receiver.pick("red-block").success
    assert bridge.owner("red-block") == "robot-2"
    assert not sender.has_object("red-block")


def test_stale_transfer_token_cannot_reactivate_sender_copy():
    bridge, sender, receiver = seeded_handoff_worlds(seed=7)
    first = bridge.acquire("red-block", "robot-1")
    second = bridge.transfer("red-block", "robot-1", "environment", first)
    with pytest.raises(StaleFencingToken):
        bridge.transfer("red-block", "robot-1", "robot-2", first)
    assert second > first
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv/bin/pytest sim/mujoco/tests/test_shared_handoff.py -q
```

Expected: shared handoff module and fixture scenes do not exist.

- [ ] **Step 3: Implement the simulation-only shared object bridge**

```python
@dataclass
class SharedObject:
    entity_id: str
    owner: str
    token: int
    pose: tuple[float, float, float]

class SharedHandoffBridge:
    def __init__(self) -> None:
        self._lock = RLock()
        self._objects = {"red-block": SharedObject("red-block", "robot-1", 1, SENDER_START)}
        self._worlds: dict[str, TabletopWorld] = {}

    def transfer(self, entity_id: str, expected_owner: str, new_owner: str, token: int) -> int:
        with self._lock:
            state = self._objects[entity_id]
            if state.owner != expected_owner or state.token != token:
                raise StaleFencingToken(entity_id)
            state.owner = new_owner
            state.token += 1
            self._sync_copies(state)
            return state.token
```

The two MJCF cells use the same world coordinates for `handoff-zone`. Only the active owner cell exposes the block in `entities()` and `has_object`; inactive copies are parked outside the workspace. The bridge transfer at a successful place is atomic with the simulator's placement commit. This is a simulation adapter concern and is never visible in the cloud command contract.

- [ ] **Step 4: Serve two Runtime endpoints from one shared fixture process**

`python -m tangying_sim.fleet_server --robot-1 127.0.0.1:50051 --robot-2 127.0.0.1:50052 --human-speed 0.02` creates two `TabletopWorld` instances linked by one bridge and starts two gRPC servers. Update `scripts/fleet-sim.sh` to start this process instead of unrelated object copies.

- [ ] **Step 5: Verify GREEN and visual observability**

Run:

```bash
.venv/bin/pytest sim/mujoco/tests/test_shared_handoff.py sim/mujoco/tests/test_world.py sim/mujoco/tests/test_rendering.py -q
bash -n scripts/fleet-sim.sh
```

Expected: only one logical `red-block` is observable; both robot camera frames render; stale transfer cannot resurrect the sender copy.

- [ ] **Step 6: Commit Task 5**

```bash
git add sim/mujoco/tangying_sim sim/mujoco/tests sim/mujoco/assets/xlerobot_handoff_r1.xml sim/mujoco/assets/xlerobot_handoff_r2.xml scripts/gen_scene_variant.py scripts/fleet-sim.sh
git commit -m "feat: simulate a shared multi-robot handoff object"
```

## Task 6: Gate the distributed task graph on world evidence and object leases

**Files:**

- Modify: `skills/manipulation/intent.go`
- Modify: `agent/intent/parser.go`
- Modify: `agent/intent/parser_test.go`
- Modify: `core/taskgraph/state.go`
- Modify: `core/taskgraph/state_test.go`
- Modify: `fleet/coordinator/coordinator.go`
- Modify: `fleet/coordinator/coordinator_test.go`
- Modify: `edge/cloudclient/client.go`
- Modify: `edge/worker/worker.go`
- Modify: `edge/worker/worker_test.go`
- Modify: `fleet/server.go`
- Modify: `fleet/server_test.go`

- [ ] **Step 1: Write failing natural-language and world-gate tests**

```go
func TestParseSharedBlockHandoff(t *testing.T) {
	parsed, err := NewDeterministicParser().Parse("让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区")
	if err != nil { t.Fatal(err) }
	intents := parsed.Tasks()
	if len(intents) != 2 || intents[0].RobotID != "robot-1" || intents[1].RobotID != "robot-2" {
		t.Fatalf("intents=%#v", intents)
	}
	if intents[0].Destination.Category != manipulation.CategoryHandoffZone || intents[1].Destination.Relation != "right_side" {
		t.Fatalf("destinations=%#v %#v", intents[0].Destination, intents[1].Destination)
	}
}

func TestIntentCompletionWaitsForFreshStableWorldEvidence(t *testing.T) {
	fixture := coordinatorWithHandoffTask(t)
	node := claimIntent(t, fixture, "robot-1")
	_, err := fixture.coordinator.CompleteIntent(context.Background(), "task-1", node.Index, "robot-1")
	if !errors.Is(err, coordinator.ErrWorldNotReady) { t.Fatalf("err=%v", err) }
	fixture.ingestBlockInHandoff(false)
	_, err = fixture.coordinator.CompleteIntent(context.Background(), "task-1", node.Index, "robot-1")
	if !errors.Is(err, coordinator.ErrWorldNotReady) { t.Fatalf("unstable err=%v", err) }
	fixture.ingestBlockInHandoff(true)
	snapshot, err := fixture.coordinator.CompleteIntent(context.Background(), "task-1", node.Index, "robot-1")
	if err != nil || snapshot.Intents[1].FencingToken <= node.FencingToken { t.Fatalf("snapshot=%#v err=%v", snapshot, err) }
}
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
go test ./agent/intent ./fleet/coordinator ./edge/worker -run 'Handoff|WorldEvidence' -v
```

Expected: handoff/target-zone language is unsupported and coordinator accepts completion without consulting the world.

- [ ] **Step 3: Add handoff intent semantics**

Add `CategoryHandoffZone`, `CategoryTargetZone`, and optional `Source` selector to `manipulation.Intent`. Preserve existing JSON compatibility. Extend the deterministic parser for `交接区`, `目标区`, `接过`, and the exact acceptance sentence; keep generic existing pick/place behavior unchanged.

Add the approved recovery states to the existing task state machine and test both the recovery loop and safe terminal paths:

```go
const (
	StateWaitingForObservation TaskState = "WAITING_FOR_OBSERVATION"
	StateRecovering            TaskState = "RECOVERING"
	StateBlocked               TaskState = "BLOCKED"
	StateFailedSafe            TaskState = "FAILED_SAFE"
)

func TestObservationRecoveryPath(t *testing.T) {
	path := []TaskState{StateExecuting, StateWaitingForObservation, StateRecovering, StateExecuting}
	for index := 0; index < len(path)-1; index++ {
		if !CanTransition(path[index], path[index+1]) {
			t.Fatalf("transition %s -> %s rejected", path[index], path[index+1])
		}
	}
	if !CanTransition(StateWaitingForObservation, StateBlocked) ||
		!CanTransition(StateRecovering, StateFailedSafe) {
		t.Fatal("safe terminal recovery transitions are missing")
	}
}
```

The transition table adds `EXECUTING -> WAITING_FOR_OBSERVATION -> RECOVERING -> EXECUTING`, `WAITING_FOR_OBSERVATION -> BLOCKED`, and `RECOVERING -> BLOCKED | FAILED_SAFE`; it must retain the existing manual-clearance rule for `SAFETY_STOPPED`.

- [ ] **Step 4: Add lease and predicate fields to coordinator nodes**

```go
type IntentNode struct {
	Index          int          `json:"index"`
	Action         string       `json:"action"`
	RobotID        string       `json:"robotId,omitempty"`
	Claimed        string       `json:"claimed,omitempty"`
	Status         IntentStatus `json:"status"`
	ResourceID     string       `json:"resourceId,omitempty"`
	FencingToken   uint64       `json:"fencingToken,omitempty"`
	CatalogRevision string      `json:"catalogRevision,omitempty"`
	WorldRevision  uint64       `json:"worldRevision,omitempty"`
	PredicateState string       `json:"predicateState,omitempty"`
	Started        time.Time    `json:"startedAt,omitempty"`
	Finished       time.Time    `json:"finishedAt,omitempty"`
	Error          string       `json:"error,omitempty"`
}
```

`NextIntent` freezes the selected robot's tool catalog revision, then acquires the object and relevant zone lease before returning the node. Edge copies that revision into every physical `runtime.Command`; a stale or missing runtime catalog fails closed before motion. `CompleteIntent` evaluates the latest predicate. For the sender it requires block inside handoff zone, stable for two accepted observations, sender held empty, and fresh sources. It then commits `BLOCK_AVAILABLE` and transfers fencing to robot-2. The final intent requires block inside target zone, stable, receiver held empty, then releases leases.

- [ ] **Step 5: Teach Edge to retry world-not-ready without replaying physical work**

After the local seven-step plan succeeds, the worker calls completion with the same idempotency key. HTTP `409 WORLD_NOT_READY` transitions to a bounded wait/retry loop that only resubmits completion; it never invokes pick/place again. The loop stops on context cancellation, lease loss, or retry budget.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
go test ./agent/intent ./skills/manipulation ./fleet/coordinator ./edge/cloudclient ./edge/worker ./fleet -race -v
```

Expected: world evidence gates completion, token transfer is monotonic, and repeated completion is idempotent.

- [ ] **Step 7: Commit Task 6**

```bash
git add skills/manipulation agent/intent core/taskgraph/state.go core/taskgraph/state_test.go fleet/coordinator edge/cloudclient edge/worker fleet/server.go fleet/server_test.go
git commit -m "feat: coordinate shared block handoff from world evidence"
```

## Task 7: Expose snapshot-plus-cursor realtime world APIs

**Files:**

- Modify: `fleet/auth/auth.go`
- Modify: `fleet/auth/auth_test.go`
- Modify: `fleet/server.go`
- Modify: `fleet/server_test.go`
- Modify: `cmd/fleet-control-plane/main.go`
- Modify: `deploy/cloud/nginx.conf`
- Modify: `docs/fleet-cloud.md`

- [ ] **Step 1: Write failing snapshot, gap and authenticated WebSocket tests**

```go
func TestWorldSnapshotUsesProjectorRevisionAndUnknownHealth(t *testing.T) {
	server, token, hub := fleetServerWithWorld(t)
	hub.Ingest(context.Background(), validObservation(1))
	request := authenticatedRequest(http.MethodGet, "/v1/world", token, nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	var snapshot worldmodel.Snapshot
	json.NewDecoder(response.Body).Decode(&snapshot)
	if snapshot.Revision != 1 || snapshot.EventCursor == "" { t.Fatalf("snapshot=%#v", snapshot) }
}

func TestWorldWebSocketReplaysAfterCursorAndSignalsGap(t *testing.T) {
	fixture := websocketFixture(t, 2)
	fixture.ingest(1, 2, 3)
	client := fixture.connectWithTicket(1)
	first := client.readDelta()
	if first.Revision != 2 { t.Fatalf("revision=%d", first.Revision) }
	fixture.ingest(4, 5, 6)
	gap := fixture.connectWithTicket(1).readControl()
	if gap.Type != "RESYNC_REQUIRED" { t.Fatalf("control=%#v", gap) }
}

func TestObservationFallbackRejectsOperatorTokenAndOversizedBody(t *testing.T) {
	server, operatorToken, _ := fleetServerWithWorld(t)
	body := bytes.NewReader(bytes.Repeat([]byte("x"), maxObservationBodyBytes+1))
	request := authenticatedRequest(http.MethodPost, "/v1/observations", operatorToken, body)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, request)
	if response.Code != http.StatusForbidden && response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
}
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
go test ./fleet ./fleet/auth -run 'WorldSnapshot|WorldWebSocket' -v
```

Expected: `/v1/world` still assembles telemetry ad hoc and no realtime endpoint exists.

- [ ] **Step 3: Add short-lived one-time WebSocket tickets**

Add authenticated `POST /v1/auth/ws-ticket` returning a random single-use ticket with 60-second TTL scoped to operator and fleet. Do not put the long-lived bearer token in the URL. The WebSocket upgrade consumes the ticket atomically.

- [ ] **Step 4: Serve authoritative snapshot and deltas**

Routes:

```text
GET  /v1/world
POST /v1/auth/ws-ticket
GET  /v1/world/events/ws?ticket=...&after_revision=...
POST /v1/observations
```

On subscription, replay retained deltas after `after_revision`; if retention cannot cover the gap, send `RESYNC_REQUIRED` and close. Use origin checks and existing security headers. Nginx must pass `Upgrade`, `Connection`, and disable proxy buffering for the world stream.

Bound observation requests before JSON decoding (256 KiB, nesting depth 16, bounded entity/attribute counts), reject unknown fields and non-finite numeric values, and authorize the body `robot_id` against the mTLS-derived robot session. Operator bearer tokens cannot ingest robot observations. Log stable event/error codes and hashes only; never log tickets, bearer values, certificate private material, or raw camera bytes.

- [ ] **Step 5: Wire WorldHub into cloud composition**

`cmd/fleet-control-plane` constructs one `worldhub.Hub`, passes it to gateway/server/coordinator, and starts an outbox publisher. Add `FLEET_WORLD_ID`, `FLEET_WORLD_FRESHNESS`, `FLEET_WORLD_DELTA_RETENTION`, and cloud-default-off `FLEET_HTTP_OBSERVATION_INGEST` with bounded defaults.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
go test ./fleet ./fleet/auth ./fleet/worldhub ./cmd/fleet-control-plane -race -v
bash -n scripts/fleet-up.sh
```

Expected: snapshots use monotonically increasing projector revision; tickets are single-use; cursor replay and resync behavior pass.

- [ ] **Step 7: Commit Task 7**

```bash
git add fleet/auth fleet/server.go fleet/server_test.go cmd/fleet-control-plane deploy/cloud/nginx.conf docs/fleet-cloud.md
git commit -m "feat: stream authoritative fleet world deltas"
```

## Task 8: Implement the interactive semantic 3D Console

**Files:**

- Create: `web/world_view.js`
- Create: `web/world_view_test.mjs`
- Modify: `web/index.html`
- Modify: `web/styles.css`
- Modify: `web/app.js`
- Modify: `web/app_test.mjs`
- Modify: `web/observability_test.go`

- [ ] **Step 1: Write failing camera and realtime-client tests**

```javascript
test("left drag pans while right drag orbits", () => {
  const camera = new WorldCamera({ yaw: 0.5, pitch: 0.4, distance: 4, target: [0, 0, 0] });
  const initialYaw = camera.yaw;
  camera.drag({ button: 0, dx: 40, dy: 20, viewport: [1200, 600] });
  assert.equal(camera.yaw, initialYaw);
  assert.notDeepEqual(camera.target, [0, 0, 0]);
  const panned = [...camera.target];
  camera.drag({ button: 2, dx: 40, dy: 20, viewport: [1200, 600] });
  assert.notEqual(camera.yaw, initialYaw);
  assert.deepEqual(camera.target, panned);
});

test("wheel zoom preserves the world point under the pointer", () => {
  const camera = fittedCamera();
  const before = camera.unprojectToGround(810, 320, 1200, 600);
  camera.zoomAt(-240, 810, 320, 1200, 600);
  const after = camera.unprojectToGround(810, 320, 1200, 600);
  assert.ok(distance(before, after) < 1e-6, `${before} -> ${after}`);
});

test("revision gap stops rendering and requests a fresh snapshot", async () => {
  const client = realtimeHarness({ snapshotRevision: 10 });
  client.receive({ type: "WORLD_DELTA", revision: 12 });
  assert.equal(client.state, "RESYNCING");
  assert.equal(client.snapshotRequests, 1);
});
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
node --test web/world_view_test.mjs web/app_test.mjs
```

Expected: `WorldCamera`, realtime revision state, and new view module do not exist.

- [ ] **Step 3: Implement the camera and renderer as an offline local module**

`web/world_view.js` exposes `WorldCamera`, `WorldRenderer`, and `WorldRealtimeClient` through `globalThis.TangyingWorld`. It must not import a CDN. `WorldRenderer` draws world grid, work surfaces, zones, entities, robots, trajectories, held objects, leases and freshness badges from `WorldSnapshot` only.

Pointer bindings:

```javascript
canvas.addEventListener("pointerdown", beginWorldDrag);
canvas.addEventListener("pointermove", moveWorldDrag);
canvas.addEventListener("pointerup", endWorldDrag);
canvas.addEventListener("pointercancel", endWorldDrag);
canvas.addEventListener("contextmenu", (event) => event.preventDefault());
canvas.addEventListener("wheel", zoomWorldAtPointer, { passive: false });
canvas.addEventListener("dblclick", focusWorldEntity);
window.addEventListener("keydown", (event) => { if (event.key.toLowerCase() === "f") resetWorldView(); });
```

Use pointer capture. Clamp pitch/distance. Persist the camera in local storage only after validated finite values.

- [ ] **Step 4: Replace Fleet polling with snapshot + WebSocket deltas**

`showFleetDashboard` obtains a ticket, fetches `/v1/world`, renders it, then connects after that revision. Interpolate robot/entity pose between the last two accepted snapshots via `requestAnimationFrame`; do not interpolate lease, activity, task state or freshness. Keep 30-second HTTP snapshot polling only as a safety audit, not the primary update path.

Frames remain a separate 2–10 FPS evidence layer. Add `observedAt` and stale classes; a stale frame never marks the semantic world stale and vice versa.

- [ ] **Step 5: Verify GREEN and static embed behavior**

Run:

```bash
node --check web/world_view.js
node --check web/app.js
node --test web/world_view_test.mjs web/app_test.mjs
go test ./web -v
```

Expected: camera contract tests pass; index embeds `world_view.js`; stale/revision tests pass.

- [ ] **Step 6: Commit Task 8**

```bash
git add web/world_view.js web/world_view_test.mjs web/index.html web/styles.css web/app.js web/app_test.mjs web/observability_test.go
git commit -m "feat: add realtime interactive fleet world view"
```

## Task 9: Add deterministic fault injection and end-to-end evidence packages

**Files:**

- Create: `tests/e2e/fleet_harness.py`
- Create: `tests/e2e/test_fleet_handoff.py`
- Create: `tests/e2e/test_fleet_faults.py`
- Create: `scripts/run_fleet_harness.py`
- Modify: `tests/e2e/test_fleet_cloud.py`
- Modify: `scripts/fleet-sim.sh`
- Modify: `.gitignore`

- [ ] **Step 1: Write the failing normal handoff e2e**

```python
def test_natural_language_shared_block_handoff(fleet_harness):
    task = fleet_harness.create_and_approve(
        "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区"
    )
    final = fleet_harness.wait_task(task["id"], "SUCCEEDED")
    world = fleet_harness.world()
    assert world["entities"]["red-block"]["relations"]["inside"] == "right-target-zone"
    assert world["robots"]["robot-1"]["held"] == ""
    assert world["robots"]["robot-2"]["held"] == ""
    assert world["resources"]["red-block"]["owner"] == ""
    assert fleet_harness.event_types(task["id"]).count("BLOCK_AVAILABLE") == 1
    assert fleet_harness.physical_command_count("red-block", "manipulation.pick") == 2
    fleet_harness.write_evidence("normal-handoff")
```

- [ ] **Step 2: Write the failing fault-matrix parametrization**

```python
@pytest.mark.parametrize("fault,expected", [
    ("observation_duplicate_reorder", "SUCCEEDED"),
    ("edge_disconnect_reconnect", "SUCCEEDED"),
    ("worker_crash_after_place", "SUCCEEDED"),
    ("coordinator_restart", "SUCCEEDED"),
    ("redis_outage", "RECOVERING"),
    ("stale_fencing_token", "SUCCEEDED"),
    ("receiver_offline_after_handoff", "BLOCKED"),
    ("camera_loss_and_ui_reconnect", "SUCCEEDED"),
    ("external_block_move", "BLOCKED"),
])
def test_distributed_fault_preserves_invariants(fleet_harness, fault, expected):
    run = fleet_harness.run_fault(fault, seed=20260820)
    assert run.task_state == expected
    assert run.world_revision_monotonic
    assert run.block_owner_count <= 1
    assert run.stale_command_accepts == 0
    assert not run.false_success
    fleet_harness.write_evidence(fault)
```

- [ ] **Step 3: Run and verify RED**

Run:

```bash
.venv/bin/pytest tests/e2e/test_fleet_handoff.py tests/e2e/test_fleet_faults.py -q -x
```

Expected: harness and shared-handoff behavior do not exist.

- [ ] **Step 4: Implement deterministic process/fault controls in the test harness**

The harness owns explicit PIDs and free ports. It can pause/restart edge workers and the Fleet process, wrap observation requests to duplicate/reorder/drop selected sequence numbers, inject a stale command directly into the sim runtime, disable the queue publisher, disable camera frames, and move the simulated block through a simulation-only test control. Production binaries expose no unauthenticated fault endpoint.

Use SQLite Fleet persistence for restart tests and the memory queue/outbox publisher for deterministic Redis-outage simulation. A separate Docker integration test covers the Redis Lua adapter when Docker is available.

- [ ] **Step 5: Implement evidence collection**

`scripts/run_fleet_harness.py --scenario all --output artifacts/fleet-harness/<run-id>` runs normal plus nine scenarios and writes:

```text
task.json
events.jsonl
world-final.json
leases.json
robot-1-command-journal.json
robot-2-command-journal.json
faults.json
console-screenshot.png
summary.json
```

Add only `artifacts/fleet-harness/` to `.gitignore`; never ignore the whole `artifacts/` tree.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
.venv/bin/pytest tests/e2e/test_fleet_handoff.py tests/e2e/test_fleet_faults.py -q
.venv/bin/python scripts/run_fleet_harness.py --scenario all --output artifacts/fleet-harness/manual
```

Expected: normal scenario succeeds; each fault reaches its declared state; `summary.json` reports all invariants true and the screenshot is a valid PNG.

- [ ] **Step 7: Commit Task 9**

```bash
git add tests/e2e scripts/run_fleet_harness.py scripts/fleet-sim.sh .gitignore
git commit -m "test: verify distributed fleet recovery scenarios"
```

## Task 10: Reuse the contracts in Local Brain and real-robot adapters

**Files:**

- Modify: `internal/localapp/app.go`
- Modify: `internal/localapp/app_test.go`
- Modify: `console/server.go`
- Modify: `console/server_test.go`
- Modify: `middleware/sqlite/store.go`
- Modify: `robot/gateway/tangying_robot_gateway/service.py`
- Modify: `robot/gateway/tangying_robot_gateway/backend.py`
- Modify: `robot/gateway/tangying_robot_gateway/xlerobot_backend.py`
- Modify: `robot/gateway/tests/test_runtime_boundary.py`
- Create: `robot/gateway/tests/test_tool_observation_contract.py`
- Modify: `docs/middleware.md`
- Modify: `docs/agent-v1.md`

- [ ] **Step 1: Write failing Local Brain and real-adapter contract tests**

```go
func TestLocalAndFleetWorldUseTheSameSchemaVersion(t *testing.T) {
	local := newLocalTestApp(t)
	response := local.get("/v1/world")
	var snapshot worldmodel.Snapshot
	json.NewDecoder(response.Body).Decode(&snapshot)
	if snapshot.SchemaVersion != "world.snapshot.v1" { t.Fatalf("schema=%q", snapshot.SchemaVersion) }
}
```

```python
def test_xlerobot_adapter_rejects_stale_fencing_before_motion(runtime_service):
    runtime_service.register_resource("red-block", owner="robot-local", token=8)
    result = execute(runtime_service, skill="manipulation.pick", resource_id="red-block", fencing_token=7)
    assert result.code == "FENCING_TOKEN_STALE"
    assert runtime_service.backend.motion_count == 0

def test_observation_catalog_reports_frames_and_transform_revision(runtime_service):
    info = runtime_service.get_runtime_info()
    assert info.catalog_revision
    assert {source.source_id for source in info.observation_sources} >= {"proprioception", "scene"}
    assert all(source.transform_revision for source in info.observation_sources)
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
go test ./internal/localapp ./console -run 'LocalAndFleetWorld' -v
.venv/bin/pytest robot/gateway/tests/test_tool_observation_contract.py -q
```

Expected: Local Console has no common WorldHub endpoint and the real gateway lacks catalog/fencing/observation metadata.

- [ ] **Step 3: Wire Local Brain to the common world contracts**

Local app constructs `worldhub.Hub` with SQLite event/checkpoint storage and publishes its existing RobotRuntime observations into it. Serve the same `/v1/world` snapshot and `/v1/world/events/ws` semantics over the local authenticated/loopback profile. Local execution needs no Redis leader lease, but SQLite fencing tokens remain monotonic.

- [ ] **Step 4: Make the XLeRobot gateway a conforming adapter example**

Advertise the same catalog revision and observation-source metadata as MuJoCo. Keep actual perception readiness fail-closed: if no configured scene observation provider can verify placement, the capability remains blocked and a task cannot report physical success. Validate fencing and idempotency before any backend motion call.

- [ ] **Step 5: Verify GREEN**

Run:

```bash
go test ./internal/localapp ./console ./middleware/sqlite -race -v
.venv/bin/pytest robot/gateway/tests -q
```

Expected: Local and cloud snapshots share schema/semantics; XLeRobot contract rejects stale commands without moving.

- [ ] **Step 6: Commit Task 10**

```bash
git add internal/localapp console middleware/sqlite robot/gateway docs/middleware.md docs/agent-v1.md
git commit -m "feat: align local and real robot world contracts"
```

## Task 11: Align product docs, deployment, browser evidence and release gates

**Files:**

- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture.md`
- Modify: `docs/distributed-agentos.md`
- Modify: `docs/multi-robot.md`
- Modify: `docs/fleet-cloud.md`
- Modify: `docs/fleet-paper-loop.md`
- Modify: `docs/install/alicloud-cloud.md`
- Modify: `deploy/cloud/.env.example`
- Modify: `deploy/cloud/docker-compose.yml`
- Modify: `Makefile`
- Modify: `tests/test_repository.py`
- Modify: `tests/deploy/test_deployment_contract.py`

- [ ] **Step 1: Write failing documentation/deployment contract tests**

```python
def test_readme_leads_with_cloud_product_and_keeps_offline_local_brain():
    readme = Path("README.md").read_text()
    assert "联网即用" in readme[:2500]
    assert "无网络" in readme[:2500]
    assert "./scripts/fleet-sim.sh handoff" in readme

def test_cloud_env_exposes_world_and_coordination_controls():
    env = Path("deploy/cloud/.env.example").read_text()
    for key in (
        "FLEET_WORLD_ID", "FLEET_WORLD_FRESHNESS", "FLEET_WORLD_DELTA_RETENTION",
        "FLEET_LEADER_LEASE", "FLEET_RESOURCE_LEASE",
    ):
        assert key in env
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
.venv/bin/pytest tests/test_repository.py tests/deploy/test_deployment_contract.py -q
```

Expected: current README still leads with local-first positioning and new environment keys are absent.

- [ ] **Step 3: Update product and operational documentation**

README starts with cloud联网即用, then documents the offline Local Brain as the second deployment. Describe the handoff command, expected world/task evidence, Console controls, limits of semantic occupancy versus real SLAM, and real adapter registration. Preserve historical design documents as history; current architecture docs point to the new approved design.

Add Make targets:

```make
fleet-handoff:
	bash scripts/fleet-sim.sh handoff

fleet-chaos:
	.venv/bin/python scripts/run_fleet_harness.py --scenario all --output artifacts/fleet-harness/manual
```

- [ ] **Step 4: Run the complete fresh verification gate**

Run in this exact order and save the command output in the handoff report:

```bash
make generate-check
make build
make fleet-build
make test
make lint
.venv/bin/pytest tests/e2e/test_fleet_handoff.py tests/e2e/test_fleet_faults.py -q
.venv/bin/python scripts/run_fleet_harness.py --scenario all --output artifacts/fleet-harness/final
make install-check
make sim2real-check
git diff --check
```

Expected: every command exits 0; the final evidence summary reports normal handoff success and all fault invariants true.

- [ ] **Step 5: Inspect the live user-facing Console**

Start the Fleet stack and shared simulation, open the Cloud Console in a browser, and verify against `/v1/world`:

1. left drag changes only camera target;
2. right drag changes only yaw/pitch;
3. wheel zoom keeps the pointer world anchor stable;
4. browser refresh restores view and latest revision;
5. red block, robot held state, lease owner and task stage match the API;
6. camera loss shows `CAMERA STALE` while the semantic world continues;
7. world stream loss removes `LIVE` and triggers resync;
8. save `artifacts/fleet-harness/final/console-screenshot.png`.

- [ ] **Step 6: Update changelog and commit release-facing files**

```bash
git add README.md CHANGELOG.md docs deploy/cloud Makefile tests/test_repository.py tests/deploy/test_deployment_contract.py
git commit -m "docs: document cloud distributed robot handoff"
```

## Final self-review checklist

- [ ] Every approved design section maps to at least one task above.
- [ ] The shared block is one logical entity; separate MuJoCo cells cannot both expose it.
- [ ] A physical tool result never bypasses a world predicate.
- [ ] Coordinator state, checkpoint, event and outbox survive restart.
- [ ] Fencing tokens are monotonic and old tokens fail both cloud and runtime validation.
- [ ] World revisions and per-source sequences never go backwards.
- [ ] Console world and camera freshness remain independent.
- [ ] Local Brain and cloud use the same world schema and tool/observation contracts.
- [ ] No production fault endpoint, secret, generated evidence or `.superpowers` file is committed.
- [ ] Final claims quote fresh test, browser and evidence outputs.
