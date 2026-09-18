// Package coordinator is the cloud-side multi-robot task coordinator.
//
// A distributed task is an ordered list of intents (subtasks). Every intent
// may be bound to a robot id; an unbound intent can be claimed by any online
// worker. Intent k becomes claimable only after every intent before it has
// SUCCEEDED — this is the event-driven cross-robot refresh: when robot A
// finishes its subtask, the coordinator refreshes the next subtask to READY,
// and robot B's worker can then claim it.
//
// Step-level execution stays on the edge worker: it grounds the intent
// against its robot's scene, materializes the deterministic plan and reports
// the intent outcome here. The Robot Runtime never sees the coordinator.
package coordinator

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/harness"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// IntentStatus mirrors taskgraph.NodeStatus for one subtask node.
type IntentStatus string

const (
	StatusPending   IntentStatus = "PENDING"
	StatusReady     IntentStatus = "READY"
	StatusRunning   IntentStatus = "RUNNING"
	StatusSucceeded IntentStatus = "SUCCEEDED"
	StatusFailed    IntentStatus = "FAILED"
	StatusCancelled IntentStatus = "CANCELLED"

	// StatusUnknownOutcome means the claim lease lapsed while the intent was
	// RUNNING, so whether the robot acted is not established.
	//
	// It is deliberately not StatusReady. Returning the intent to the claimable
	// pool would let another worker repeat a physical action whose first attempt
	// may already have happened — the one thing core/closedloop forbids for an
	// unknown outcome, and the reason its UnknownOutcome class carries a
	// permanent retry prohibition. A claim lease lapsing tells us the worker
	// stopped reporting; it does not tell us the robot stood still.
	//
	// Only ReconcileIntent moves an intent out of this state, and it records who
	// decided and why.
	StatusUnknownOutcome IntentStatus = "UNKNOWN_OUTCOME"
)

// IntentNode is one subtask node of the distributed task graph.
type IntentNode struct {
	Index               int          `json:"index"`
	StepID              string       `json:"stepId,omitempty"`
	TaskRevision        uint64       `json:"taskRevision,omitempty"`
	AggregateVersion    uint64       `json:"aggregateVersion,omitempty"`
	SemanticFingerprint string       `json:"semanticFingerprint,omitempty"`
	CommandID           string       `json:"commandId,omitempty"`
	SafeCheckpoint      bool         `json:"safeCheckpoint,omitempty"`
	Action              string       `json:"action"`
	RobotID             string       `json:"robotId,omitempty"` // bound robot, "" = any worker
	Claimed             string       `json:"claimed,omitempty"` // worker that claimed it
	Status              IntentStatus `json:"status"`
	ResourceID          string       `json:"resourceId,omitempty"`
	FencingToken        uint64       `json:"fencingToken,omitempty"`
	CatalogRevision     string       `json:"catalogRevision,omitempty"`
	WorldRevision       uint64       `json:"worldRevision,omitempty"`
	EntitySourceID      string       `json:"entitySourceId,omitempty"`
	EntitySequence      uint64       `json:"entitySequenceBasis,omitempty"`
	EntityCount         uint64       `json:"entityObservationCountBasis,omitempty"`
	RobotSourceID       string       `json:"robotSourceId,omitempty"`
	RobotSequence       uint64       `json:"robotSequenceBasis,omitempty"`
	// VerificationBasis records what confirmed this intent's completion. Without
	// it, "a harness confirmed the block is in the zone" and "the worker said so"
	// are the same record.
	VerificationBasis string    `json:"verificationBasis,omitempty"`
	PredicateState    string    `json:"predicateState,omitempty"`
	HarnessStatus     string    `json:"harnessStatus,omitempty"`
	HarnessReason     string    `json:"harnessReason,omitempty"`
	HarnessEvidence   []string  `json:"harnessEvidenceIds,omitempty"`
	Started           time.Time `json:"startedAt,omitempty"`
	Finished          time.Time `json:"finishedAt,omitempty"`
	Error             string    `json:"error,omitempty"`
	// Reconciled* record the person who resolved an UNKNOWN_OUTCOME intent.
	//
	// They exist because the alternative — a bare status flip back to READY —
	// leaves no answer to "who decided it was safe to try again, and on what
	// grounds". That question is the whole reason the intent needed a person.
	ReconciledBy      string    `json:"reconciledBy,omitempty"`
	ReconciledAt      time.Time `json:"reconciledAt,omitempty"`
	ReconcileDecision string    `json:"reconcileDecision,omitempty"`
	ReconcileNote     string    `json:"reconcileNote,omitempty"`
}

// Snapshot is the coordinator view of one distributed task.
type Snapshot struct {
	TaskID           string       `json:"taskId"`
	TaskRevision     uint64       `json:"taskRevision"`
	AggregateVersion uint64       `json:"aggregateVersion"`
	Request          string       `json:"request"`
	State            string       `json:"state"` // task-level state from the task store
	Intents          []IntentNode `json:"intents"`
	Updated          time.Time    `json:"updatedAt"`
	Robots           []string     `json:"robots"`
}

// ErrIntentNotFound is returned for unknown intent indexes.
var ErrIntentNotFound = errors.New("intent not found")

var ErrLeadershipLost = errors.New("coordinator leadership lease lost")

var ErrWorldNotReady = errors.New("world evidence is not ready")

// ErrClaimExpired is returned when a worker reports a result for an intent whose
// claim lease lapsed before the report arrived. Such a report cannot be applied:
// the coordinator has already stopped trusting that it owns the step, and the
// robot may have been re-tasked since. The intent waits in StatusUnknownOutcome
// for an explicit reconciliation rather than being silently re-run.
var ErrClaimExpired = errors.New("intent claim lease expired with the outcome unknown")

// ErrReconcileRequired is returned for a completion whose intent is sitting in
// StatusUnknownOutcome.
var ErrReconcileRequired = errors.New("intent outcome is unknown and must be reconciled")

// ErrIntentUnverifiable is returned when a completion would have to be accepted
// on the worker's word alone for an intent that changes the scene.
//
// It exists because the alternative was silence. The coordinator checked world
// evidence only for the shared-block handoff, and recorded every other intent as
// SUCCEEDED without consulting the world at all — so an intent that moved an
// object and an intent that was confirmed to have moved it produced the same
// record. A deployment with a world configured can verify these intents; one that
// verifies none of them should say so rather than look like one that does.
var ErrIntentUnverifiable = errors.New("no world verification is available for this intent")

// IntentClaimLease is how long a worker may hold an intent claim before the
// coordinator stops trusting it. A crashed or disconnected worker therefore
// never blocks the task forever.
//
// It no longer means "and then the intent can be claimed again". When the lease
// lapses the intent's outcome becomes unknown, because the worker may have
// dispatched the command before it went quiet, so the intent moves to
// StatusUnknownOutcome and waits for ReconcileIntent. See reclaimStaleLocked.
const IntentClaimLease = 2 * time.Minute

// ReconcileDecision is what a person concluded about an intent whose outcome was
// never established.
type ReconcileDecision string

const (
	// ReconcileNeverActed means the person established that the action did not
	// happen, so the step may be attempted again. This is the only decision that
	// returns an intent to the claimable pool.
	ReconcileNeverActed ReconcileDecision = "NEVER_ACTED"
	// ReconcileAbandon means the person gave up on the step: the world may have
	// changed, so the task cannot continue down this path. The intent is failed
	// rather than retried.
	ReconcileAbandon ReconcileDecision = "ABANDON"
)

// Coordinator tracks intent-node state per task. It is safe for concurrent
// use and rebuilds its view from the persisted task when a task is first
// seen, so the coordinator itself needs no durable store: task intent and
// plan data live in the task repository.
type Coordinator struct {
	service     *tasks.Service
	store       eventlog.Store
	mu          sync.Mutex
	graphs      map[string]*taskState
	now         func() time.Time
	claimLease  time.Duration
	enqueue     func(ctx context.Context, taskID string, robotIDs []string) error
	lease       lease.Manager
	leader      lease.Grant
	world       worldmodel.Reader
	worldWriter interface {
		Ingest(context.Context, observation.Envelope) (worldmodel.Delta, error)
	}
	worldMaxAge   time.Duration
	resources     lease.Manager
	resourceTTL   time.Duration
	catalogLookup func(context.Context, string) (string, error)
}

type taskState struct {
	taskID           string
	version          uint64
	taskRevision     uint64
	aggregateVersion uint64
	intents          []IntentNode
}

type persistedState struct {
	TaskID           string       `json:"taskId"`
	TaskRevision     uint64       `json:"taskRevision"`
	AggregateVersion uint64       `json:"aggregateVersion"`
	Intents          []IntentNode `json:"intents"`
}

// New builds a coordinator over the task service.
func New(service *tasks.Service) *Coordinator {
	return NewWithLease(service, IntentClaimLease)
}

// NewWithLease builds a coordinator with a custom intent claim lease.
func NewWithLease(service *tasks.Service, claimLease time.Duration) *Coordinator {
	return NewWithStore(service, claimLease, eventlog.NewMemoryStore())
}

// NewWithStore builds a restartable coordinator over one atomic event/state
// store. Reusing the store across coordinator instances preserves claims.
func NewWithStore(service *tasks.Service, claimLease time.Duration, store eventlog.Store) *Coordinator {
	if store == nil {
		store = eventlog.NewMemoryStore()
	}
	return &Coordinator{service: service, store: store, graphs: map[string]*taskState{}, now: time.Now, claimLease: claimLease}
}

// WithEnqueue installs a callback that re-publishes a task id when an intent
// becomes READY after a refresh. The cloud wires this to the per-robot
// queue router: when robot A finishes its intent, the next intent's robot B
// receives the task id again — the event-driven cross-robot handoff.
func (c *Coordinator) WithEnqueue(enqueue func(ctx context.Context, taskID string, robotIDs []string) error) *Coordinator {
	c.enqueue = enqueue
	return c
}

// WithWorld installs the same versioned world reader used by the Console and
// future Harness Agents. Handoff completion then fails closed until its
// postconditions are fresh and stable.
func (c *Coordinator) WithWorld(reader worldmodel.Reader, maxAge time.Duration) *Coordinator {
	c.world = reader
	if writer, ok := reader.(interface {
		Ingest(context.Context, observation.Envelope) (worldmodel.Delta, error)
	}); ok {
		c.worldWriter = writer
	}
	if maxAge <= 0 {
		maxAge = 5 * time.Second
	}
	c.worldMaxAge = maxAge
	return c
}

func (c *Coordinator) WithResourceLeases(manager lease.Manager, ttl time.Duration) *Coordinator {
	c.resources = manager
	if ttl <= 0 {
		ttl = IntentClaimLease
	}
	c.resourceTTL = ttl
	return c
}

func (c *Coordinator) WithCatalogLookup(lookup func(context.Context, string) (string, error)) *Coordinator {
	c.catalogLookup = lookup
	return c
}

// WithLeadership acquires the single-writer lease for a world. Callers renew
// it externally; every mutation validates the fencing token before commit.
func (c *Coordinator) WithLeadership(ctx context.Context, manager lease.Manager, worldID, coordinatorID string, ttl time.Duration) error {
	if manager == nil {
		return errors.New("leadership lease manager is required")
	}
	grant, err := manager.Acquire(ctx, "leader/"+worldID, coordinatorID, ttl)
	if err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.lease = manager
	c.leader = grant
	return nil
}

func (c *Coordinator) RenewLeadership(ctx context.Context, ttl time.Duration) error {
	c.mu.Lock()
	manager := c.lease
	grant := c.leader
	c.mu.Unlock()
	if manager == nil {
		return errors.New("leadership lease is not configured")
	}
	renewed, err := manager.Renew(ctx, grant.ResourceID, grant.Owner, grant.Token, ttl)
	if err != nil {
		return fmt.Errorf("%w: %v", ErrLeadershipLost, err)
	}
	c.mu.Lock()
	c.leader = renewed
	c.mu.Unlock()
	return nil
}

// reclaimStaleLocked moves stale RUNNING intents to UNKNOWN_OUTCOME when their
// claim lease expired. Callers must hold c.mu.
//
// It used to return them to READY, on the reasoning that "a crashed or
// disconnected worker must never block the task forever". That reasoning is
// right about the worker and wrong about the robot: a lapsed lease tells us the
// worker stopped reporting, not that the robot stood still. The command may have
// been dispatched and the arm may have moved, so putting the intent back in the
// claimable pool re-arms a physical action whose first attempt may already have
// happened.
//
// That is precisely the failure core/closedloop refuses to allow: its
// UnknownOutcome class carries a permanent automatic-retry prohibition, because
// the safe next step for "the world may have changed and we do not know how" is
// a person establishing what happened, not a replay. So the lease lapse now ends
// in a state that is visible, not claimable, and reversible only by an explicit
// ReconcileIntent that records who decided and why.
//
// The operator's goal — a dead worker must not block the task forever — is kept:
// reconciliation is one call, and it can return the intent to READY when the
// person establishes that nothing was dispatched.
func (c *Coordinator) reclaimStaleLocked(state *taskState) bool {
	if c.claimLease <= 0 {
		return false
	}
	now := c.now().UTC()
	changed := false
	for index := range state.intents {
		node := &state.intents[index]
		if node.Status != StatusRunning || node.Started.IsZero() {
			continue
		}
		if now.Sub(node.Started) <= c.claimLease {
			continue
		}
		node.Status = StatusUnknownOutcome
		// Claimed is deliberately kept. "Which worker held it when the lease
		// lapsed" is the first question reconciliation asks, and clearing it
		// would erase the only record of who might know what happened.
		node.Finished = now
		node.Error = "claim lease expired with the outcome unknown; reconcile before acting again"
		changed = true
	}
	return changed
}

// ensure loads the task and (re)builds intent nodes. Rebuilds are idempotent:
// existing nodes keep their status, so restarts do not lose in-flight state.
func (c *Coordinator) ensure(ctx context.Context, taskID string) (*taskState, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if state, ok := c.graphs[taskID]; ok {
		return state, nil
	}
	stored, ok, err := c.store.LoadState(ctx, taskID)
	if err != nil {
		return nil, err
	}
	if ok {
		var persisted persistedState
		if err := json.Unmarshal(stored.Data, &persisted); err != nil {
			return nil, fmt.Errorf("decode persisted coordinator state: %w", err)
		}
		state := &taskState{taskID: persisted.TaskID, version: stored.Version, taskRevision: persisted.TaskRevision,
			aggregateVersion: persisted.AggregateVersion, intents: persisted.Intents}
		if state.taskID == "" {
			state.taskID = taskID
		}
		if state.taskRevision == 0 {
			task, taskErr := c.service.Get(ctx, taskID)
			if taskErr != nil {
				return nil, taskErr
			}
			state.taskRevision = task.CurrentRevision
			state.aggregateVersion = task.AggregateVersion
			legacy := append([]IntentNode(nil), state.intents...)
			c.applyRevisionIdentity(ctx, state, task)
			for index := range state.intents {
				if index >= len(legacy) {
					break
				}
				identity := state.intents[index]
				state.intents[index] = legacy[index]
				state.intents[index].Index = index
				state.intents[index].StepID = identity.StepID
				state.intents[index].TaskRevision = identity.TaskRevision
				state.intents[index].AggregateVersion = identity.AggregateVersion
				state.intents[index].SemanticFingerprint = identity.SemanticFingerprint
				if state.intents[index].Status == StatusRunning && state.intents[index].CommandID == "" {
					state.intents[index].CommandID = commandIdentity(taskID, identity.TaskRevision, identity.StepID)
				}
			}
		}
		c.graphs[taskID] = state
		return state, nil
	}
	task, err := c.service.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	state := &taskState{taskID: taskID, taskRevision: task.CurrentRevision, aggregateVersion: task.AggregateVersion}
	c.applyRevisionIdentity(ctx, state, task)
	if err := c.persistLocked(ctx, state, "FLEET_GRAPH_CREATED", taskID+"/graph-created", nil, nil); err != nil {
		if !errors.Is(err, eventlog.ErrVersionConflict) {
			return nil, err
		}
		stored, found, loadErr := c.store.LoadState(ctx, taskID)
		if loadErr != nil || !found {
			return nil, loadErr
		}
		var persisted persistedState
		if loadErr = json.Unmarshal(stored.Data, &persisted); loadErr != nil {
			return nil, loadErr
		}
		state = &taskState{taskID: persisted.TaskID, version: stored.Version, taskRevision: persisted.TaskRevision,
			aggregateVersion: persisted.AggregateVersion, intents: persisted.Intents}
	}
	c.graphs[taskID] = state
	return state, nil
}

// NextIntent returns the first claimable intent for the given worker:
// PENDING (or READY), not yet claimed, bound to the worker or unbound, and
// with every earlier intent SUCCEEDED. Claiming moves it to RUNNING.
func (c *Coordinator) NextIntent(ctx context.Context, taskID, robotID string) (*IntentNode, error) {
	if robotID == "" {
		return nil, errors.New("worker robot id is required")
	}
	if err := c.validateLeadership(ctx); err != nil {
		return nil, err
	}
	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	state, err = c.reloadIfNeeded(ctx, state)
	if err != nil {
		return nil, err
	}
	before := cloneTaskState(state)
	c.reclaimStaleLocked(state)
	for index := range state.intents {
		var acquiredGrant *lease.Grant
		node := &state.intents[index]
		if node.Status != StatusPending && node.Status != StatusReady {
			continue
		}
		if node.RobotID != "" && node.RobotID != robotID {
			continue
		}
		if !c.prefixSucceeded(state, index) {
			return nil, nil
		}
		task, taskErr := c.service.Get(ctx, taskID)
		if taskErr != nil {
			return nil, taskErr
		}
		intents := task.Intent.Tasks()
		if c.catalogLookup != nil {
			revision, lookupErr := c.catalogLookup(ctx, robotID)
			if lookupErr != nil {
				return nil, lookupErr
			}
			if revision == "" {
				return nil, errors.New("robot tool catalog revision is required")
			}
			node.CatalogRevision = revision
		}
		if index < len(intents) && isSharedBlockHandoff(intents[index]) {
			if c.resources == nil {
				return nil, errors.New("shared handoff resource leases are not configured")
			}
			if node.ResourceID == "" {
				node.ResourceID = sharedBlockResourceID
			}
			if node.FencingToken == 0 {
				grant, leaseErr := c.resources.Acquire(ctx, node.ResourceID, robotID, c.resourceTTL)
				if leaseErr != nil {
					return nil, leaseErr
				}
				node.FencingToken = grant.Token
				acquiredGrant = &grant
			}
			if validateErr := c.resources.Validate(ctx, node.ResourceID, robotID, node.FencingToken); validateErr != nil {
				return nil, validateErr
			}
		}
		node.Started = c.now().UTC()
		if c.world != nil {
			world, worldErr := c.world.Snapshot(ctx)
			if worldErr != nil {
				return nil, worldErr
			}
			node.WorldRevision = world.Revision
			if entity, ok := world.Entities["red-block"]; ok {
				node.EntitySourceID = entity.Evidence.SourceID
				node.EntitySequence = entity.Evidence.SourceSequence
				node.EntityCount = entity.ObservationCount
			}
			if robot, ok := world.Robots[robotID]; ok {
				node.RobotSourceID = robot.Evidence.SourceID
				node.RobotSequence = robot.Evidence.SourceSequence
			}
		}
		node.Status = StatusRunning
		node.Claimed = robotID
		node.CommandID = commandIdentity(taskID, node.TaskRevision, node.StepID)
		node.SafeCheckpoint = false
		if err := c.persistLocked(ctx, state, "INTENT_CLAIMED", fmt.Sprintf("%s/intent/%d/claim/%d", taskID, index, state.version+1), map[string]any{
			"intentIndex": index, "robotId": robotID, "stepId": node.StepID,
			"commandId": node.CommandID, "fencingToken": node.FencingToken, "worldRevision": node.WorldRevision,
		}, nil); err != nil {
			if acquiredGrant != nil {
				_ = c.resources.Release(ctx, acquiredGrant.ResourceID, acquiredGrant.Owner, acquiredGrant.Token)
			}
			*state = *before
			return nil, err
		}
		if acquiredGrant != nil {
			if worldErr := c.publishResource(ctx, *acquiredGrant); worldErr != nil {
				return nil, worldErr
			}
		}
		c.advanceTaskState(ctx, state.taskID, taskgraph.StateExecuting)
		return node, nil
	}
	return nil, nil
}

// advanceTaskState walks a legal state-machine path from the task's current
// state to the target. Intermediate hops are chosen from the allowed
// transitions so cloud coordination never jumps states the task state
// machine forbids. Errors are best-effort: concurrent transitions from other
// paths may already have reached the target.
// advanceTaskState walks a legal state-machine path from the task's current
// state to the target. Intermediate hops are chosen from the allowed
// transitions so cloud coordination never jumps states the task state
// machine forbids. Errors are best-effort: the task may already be at or
// past a hop (e.g. a concurrent claim advanced it first).
func (c *Coordinator) advanceTaskState(ctx context.Context, taskID string, target taskgraph.TaskState) {
	var path []taskgraph.TaskState
	switch target {
	case taskgraph.StateExecuting:
		path = []taskgraph.TaskState{taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting}
	case taskgraph.StateSucceeded:
		path = []taskgraph.TaskState{taskgraph.StateVerifying, taskgraph.StateSucceeded}
	case taskgraph.StateFailed:
		path = []taskgraph.TaskState{taskgraph.StateRecoverableFailure, taskgraph.StateFailed}
	default:
		return
	}
	for _, hop := range path {
		_ = c.service.Transition(ctx, taskID, hop, "coordinator")
	}
}

// prefixSucceeded reports whether every intent before index has SUCCEEDED.
func (c *Coordinator) prefixSucceeded(state *taskState, index int) bool {
	for i := 0; i < index; i++ {
		if state.intents[i].Status != StatusSucceeded {
			return false
		}
	}
	return true
}

// CompleteIntent is the compatibility entry point for older workers. It
// snapshots the server-owned command identity and then uses the same fenced
// completion path as revision-aware workers.
func (c *Coordinator) CompleteIntent(ctx context.Context, taskID string, index int, robotID string) (*Snapshot, error) {
	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	state, err = c.reloadIfNeeded(ctx, state)
	if err != nil {
		c.mu.Unlock()
		return nil, err
	}
	if index < 0 || index >= len(state.intents) {
		c.mu.Unlock()
		return nil, fmt.Errorf("%w: %d", ErrIntentNotFound, index)
	}
	node := state.intents[index]
	c.mu.Unlock()
	if node.TaskRevision > 1 {
		return nil, fmt.Errorf("%w: revision-aware completion is required for revision %d", ErrIntentIdentityConflict, node.TaskRevision)
	}
	return c.CompleteIntentRevision(ctx, taskID, node.TaskRevision, node.AggregateVersion, index,
		node.StepID, robotID, node.CommandID, node.FencingToken)
}

// CompleteIntentRevision accepts a physical completion only when every
// immutable command coordinate still matches the active coordinator node.
// Delayed packets from superseded revisions therefore cannot advance the
// current graph.
func (c *Coordinator) CompleteIntentRevision(
	ctx context.Context,
	taskID string,
	taskRevision uint64,
	aggregateVersion uint64,
	index int,
	stepID string,
	robotID string,
	commandID string,
	fencingToken uint64,
) (*Snapshot, error) {
	if robotID == "" {
		return nil, errors.New("worker robot id is required")
	}
	if err := c.validateLeadership(ctx); err != nil {
		return nil, err
	}
	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	state, err = c.reloadIfNeeded(ctx, state)
	if err != nil {
		return nil, err
	}
	if index < 0 || index >= len(state.intents) {
		return nil, fmt.Errorf("%w: %d", ErrIntentNotFound, index)
	}
	node := &state.intents[index]
	if taskRevision != state.taskRevision || taskRevision != node.TaskRevision {
		return nil, fmt.Errorf("%w: got %d, active %d", ErrStaleTaskRevision, taskRevision, state.taskRevision)
	}
	if aggregateVersion != node.AggregateVersion || stepID != node.StepID || commandID != node.CommandID {
		return nil, fmt.Errorf("%w: revision=%d step=%q command=%q", ErrIntentIdentityConflict, taskRevision, stepID, commandID)
	}
	if fencingToken != node.FencingToken {
		return nil, fmt.Errorf("%w: fencing token %d does not match %d", ErrIntentIdentityConflict, fencingToken, node.FencingToken)
	}
	if node.Status == StatusSucceeded && node.Claimed == robotID {
		return c.snapshotLocked(ctx, state)
	}
	before := cloneTaskState(state)
	c.reclaimStaleLocked(state)
	node = &state.intents[index]
	if node.Status == StatusUnknownOutcome {
		// The lease lapsed before this report arrived. Refusing is the honest
		// answer: the coordinator already stopped trusting that this worker owned
		// the step, and it cannot tell a report that raced the lease from one
		// that arrives after the step was handed to someone else. The error names
		// the state, because "you are too late to claim this" and "the robot may
		// have moved and nobody knows" call for different next steps.
		*state = *before
		return nil, fmt.Errorf("%w: intent %d held by %s since %s; reconcile it before reporting a result",
			ErrClaimExpired, index, node.Claimed, node.Started.UTC().Format(time.RFC3339))
	}
	if node.Claimed != robotID || node.Status != StatusRunning {
		*state = *before
		return nil, fmt.Errorf("intent %d is not running on %s", index, robotID)
	}
	task, err := c.service.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	intents := task.Intent.Tasks()
	var verifiedWorld worldmodel.Snapshot
	var harnessVerdict harness.Verdict
	sharedHandoff := index < len(intents) && isSharedBlockHandoff(intents[index])
	// What is going to confirm this completion, decided before the checks rather
	// than inferred from whether they ran.
	verificationBasis := ""
	if index < len(intents) && changesTheScene(intents[index]) {
		switch {
		case c.world == nil:
			// A coordinator without a world cannot check anything. That is a
			// supported deployment, and this is the record of what it means: the
			// worker's word, named as such.
			verificationBasis = "WORKER_REPORT_NO_WORLD"
		case !sharedHandoff:
			// A world is configured and this intent changes the scene, but the
			// only predicate chain the coordinator has is the shared-block
			// handoff's. Accepting the completion here would be accepting it on
			// the worker's word while a world sat unused — the silent skip that
			// made every non-handoff success unverifiable and indistinguishable
			// from a verified one.
			return nil, fmt.Errorf("%w: intent %d (%s) changes the scene and this coordinator has no predicate for it",
				ErrIntentUnverifiable, index, intents[index].Action)
		}
	} else {
		verificationBasis = "READ_ONLY_INTENT"
	}
	if sharedHandoff && c.world != nil {
		verifiedWorld, err = c.world.Snapshot(ctx)
		if err != nil {
			return nil, err
		}
		zoneID := "handoff-zone"
		if intents[index].Destination.Category == manipulation.CategoryTargetZone {
			zoneID = "right-target-zone"
		}
		harnessVerdict = harness.New(c.worldMaxAge).Evaluate(harness.Input{
			Intent: harness.Intent{Action: intents[index].Action, RobotID: robotID},
			Basis: harness.EvidenceBasis{
				WorldRevision: node.WorldRevision, EntityObservationCount: node.EntityCount,
				EntitySourceID: node.EntitySourceID, EntitySourceSequence: node.EntitySequence,
				RobotSourceID: node.RobotSourceID, RobotSourceSequence: node.RobotSequence,
				CommandStartedAt: node.Started,
			},
			Snapshot: verifiedWorld, EntityID: "red-block", DestinationID: zoneID, RobotID: robotID,
			ExpectedResource: harness.ExpectedResource{
				ResourceID: node.ResourceID, Owner: robotID, FencingToken: node.FencingToken,
			},
			RequiredStableObservations: 2,
		})
		if harnessVerdict.Status != harness.Satisfied {
			return nil, fmt.Errorf("%w: %s (%s)", ErrWorldNotReady, harnessVerdict.Status, harnessVerdict.Reason)
		}
		node.WorldRevision = verifiedWorld.Revision
		node.PredicateState = "VERIFIED"
		node.HarnessStatus = string(harnessVerdict.Status)
		node.HarnessReason = harnessVerdict.Reason
		node.HarnessEvidence = append([]string(nil), harnessVerdict.EvidenceIDs...)
		verificationBasis = "HARNESS_" + string(harnessVerdict.Status)
	}
	node.VerificationBasis = verificationBasis
	node.Status = StatusSucceeded
	node.SafeCheckpoint = true
	node.Finished = c.now().UTC()
	var outbox []eventlog.OutboxEntry
	var outboxID string
	var enqueueRobots []string
	var transitionedGrant *lease.Grant
	var releaseAfterPublish bool
	eventType := "INTENT_SUCCEEDED"
	if index+1 < len(state.intents) {
		next := &state.intents[index+1]
		if next.Status == StatusPending && c.prefixSucceeded(state, index+1) {
			if sharedHandoff && intents[index].Destination.Category == manipulation.CategoryHandoffZone {
				if c.resources == nil {
					*state = *before
					return nil, errors.New("shared handoff resource leases are not configured")
				}
				grant, transferErr := c.resources.Transfer(
					ctx, sharedBlockResourceID, robotID, next.RobotID, node.FencingToken, c.resourceTTL,
				)
				if transferErr != nil {
					*state = *before
					return nil, transferErr
				}
				next.ResourceID = grant.ResourceID
				next.FencingToken = grant.Token
				next.WorldRevision = verifiedWorld.Revision
				next.PredicateState = "BLOCK_AVAILABLE"
				transitionedGrant = &grant
				eventType = "BLOCK_AVAILABLE"
			}
			next.Status = StatusReady
			enqueueRobots = []string{next.RobotID}
			if next.RobotID == "" {
				enqueueRobots = []string{""}
			}
			payload, marshalErr := json.Marshal(map[string]any{"taskId": taskID, "robotIds": enqueueRobots})
			if marshalErr != nil {
				*state = *before
				return nil, marshalErr
			}
			outboxID = fmt.Sprintf("%s/intent/%d/ready", taskID, index+1)
			topic := "fleet/unbound"
			if next.RobotID != "" {
				topic = "robot/" + next.RobotID
			}
			outbox = []eventlog.OutboxEntry{{ID: outboxID, Topic: topic, Key: taskID, Payload: payload, CreatedAt: c.now().UTC()}}
		}
	} else if sharedHandoff && intents[index].Destination.Category == manipulation.CategoryTargetZone && c.resources != nil {
		released, releaseErr := c.resources.Transfer(ctx, node.ResourceID, robotID, "environment", node.FencingToken, c.resourceTTL)
		if releaseErr != nil {
			*state = *before
			return nil, releaseErr
		}
		transitionedGrant = &released
		releaseAfterPublish = true
		node.PredicateState = "BLOCK_DELIVERED"
		eventType = "BLOCK_DELIVERED"
	}
	payload := map[string]any{"intentIndex": index, "robotId": robotID, "stepId": node.StepID,
		"commandId": node.CommandID, "fencingToken": node.FencingToken, "worldRevision": node.WorldRevision}
	if sharedHandoff {
		payload["harness"] = map[string]any{
			"status": harnessVerdict.Status, "reason": harnessVerdict.Reason,
			"evidenceIds":   append([]string(nil), harnessVerdict.EvidenceIDs...),
			"worldRevision": verifiedWorld.Revision,
			"observations": []map[string]any{
				auditableEvidence(verifiedWorld.Entities["red-block"].Evidence),
				auditableEvidence(verifiedWorld.Robots[robotID].Evidence),
			},
		}
		if transitionedGrant != nil {
			payload["resourceTransition"] = map[string]any{
				"resourceId":       node.ResourceID,
				"fromOwner":        robotID,
				"fromFencingToken": node.FencingToken,
				"toOwner":          transitionedGrant.Owner,
				"toFencingToken":   transitionedGrant.Token,
			}
		}
	}
	if err := c.persistLocked(ctx, state, eventType, fmt.Sprintf("%s/intent/%d/succeeded", taskID, index), payload, outbox); err != nil {
		if transitionedGrant != nil {
			_ = c.resources.Release(ctx, transitionedGrant.ResourceID, transitionedGrant.Owner, transitionedGrant.Token)
		}
		*state = *before
		return nil, err
	}
	if transitionedGrant != nil {
		if worldErr := c.publishResource(ctx, *transitionedGrant); worldErr != nil {
			return nil, worldErr
		}
		if releaseAfterPublish {
			_ = c.resources.Release(ctx, transitionedGrant.ResourceID, transitionedGrant.Owner, transitionedGrant.Token)
		}
	}
	if outboxID != "" && c.enqueue != nil {
		if err := c.enqueue(context.Background(), taskID, enqueueRobots); err == nil {
			_ = c.store.AckOutbox(context.Background(), outboxID)
		}
	}
	activated, activationErr := c.activateWaitingRevisionLocked(ctx, state)
	if activationErr != nil {
		return nil, activationErr
	}
	if !activated && index+1 == len(state.intents) {
		c.advanceTaskState(ctx, taskID, taskgraph.StateSucceeded)
	}
	return c.snapshotLocked(ctx, state)
}

func auditableEvidence(evidence worldmodel.EvidenceRef) map[string]any {
	return map[string]any{
		"observationId": evidence.ObservationID,
		"sourceId":      evidence.SourceID,
		// Domain event payloads round-trip through map[string]any. Decimal text
		// preserves the full uint64 where a JSON float would erase low bits.
		"sourceSequence":    fmt.Sprintf("%d", evidence.SourceSequence),
		"observedAt":        evidence.ObservedAt.UTC().Format(time.RFC3339Nano),
		"frameId":           evidence.FrameID,
		"transformRevision": evidence.TransformRevision,
	}
}

func postClaimEvidenceReason(snapshot worldmodel.Snapshot, node IntentNode, entityID, robotID string) string {
	if snapshot.Revision <= node.WorldRevision {
		return "WORLD_REVISION_NOT_ADVANCED"
	}
	entity, ok := snapshot.Entities[entityID]
	if !ok {
		return "ENTITY_UNKNOWN"
	}
	// A semantic entity may legitimately alternate between multiple scene
	// sources. ObservationCount is projector-wide for the entity, so two
	// accepted post-claim samples remain comparable across those sources.
	if entity.ObservationCount < node.EntityCount+2 {
		return "ENTITY_EVIDENCE_NOT_POST_COMMAND_STABLE"
	}
	if !entity.Evidence.ObservedAt.After(node.Started) {
		return "ENTITY_EVIDENCE_PREDATES_CLAIM"
	}
	robot, ok := snapshot.Robots[robotID]
	if !ok {
		return "ROBOT_UNKNOWN"
	}
	if node.RobotSourceID != "" && robot.Evidence.SourceID != node.RobotSourceID {
		return "ROBOT_SOURCE_CHANGED"
	}
	if robot.Evidence.SourceSequence <= node.RobotSequence {
		return "ROBOT_EVIDENCE_PREDATES_COMMAND"
	}
	if !robot.Evidence.ObservedAt.After(node.Started) {
		return "ROBOT_EVIDENCE_PREDATES_CLAIM"
	}
	return ""
}

func (c *Coordinator) publishResource(ctx context.Context, grant lease.Grant) error {
	if c.worldWriter == nil || c.world == nil {
		return nil
	}
	snapshot, err := c.world.Snapshot(ctx)
	if err != nil {
		return err
	}
	now := c.now().UTC()
	_, err = c.worldWriter.Ingest(ctx, observation.Envelope{
		SchemaVersion:  "world.observation.v1",
		ObservationID:  fmt.Sprintf("coordinator/resource/%s/%020d", grant.ResourceID, grant.Token),
		WorldID:        snapshot.WorldID,
		SourceID:       "coordinator/resources/" + grant.ResourceID,
		SourceType:     observation.SourceToolResultEvidence,
		SourceSequence: grant.Token,
		ObservedAt:     now, ReceivedAt: now, FrameID: "world",
		TransformRevision: "coordinator-world-v1", Kind: observation.ResourceUpsert,
		Payload: observation.ResourcePayload{
			ResourceID: grant.ResourceID, Owner: grant.Owner,
			FencingToken: grant.Token, ExpiresAt: grant.ExpiresAt,
		},
		Confidence: 1,
		Provenance: observation.Provenance{Adapter: "fleet-coordinator", Version: "v1"},
	})
	return err
}

const sharedBlockResourceID = "block:red-block"

// changesTheScene reports whether an intent can alter scene state, which is what
// a world check is for.
//
// Navigation and observation are excluded deliberately: they move the robot and
// read the world, and neither changes what an entity-inside predicate would see,
// so demanding a scene predicate for them would refuse intents that are not
// lying about anything.
func changesTheScene(intent manipulation.Intent) bool {
	switch intent.Action {
	case manipulation.ActionPickAndPlace, manipulation.ActionFetch, manipulation.ActionHomeManipulation:
		return true
	default:
		return false
	}
}

func isSharedBlockHandoff(intent manipulation.Intent) bool {
	return intent.Object.Category == "block" && intent.Object.Attributes["color"] == "red" &&
		(intent.Source.Category == manipulation.CategoryHandoffZone ||
			intent.Destination.Category == manipulation.CategoryHandoffZone ||
			intent.Destination.Category == manipulation.CategoryTargetZone)
}

func (c *Coordinator) evaluateHandoffPredicate(
	snapshot worldmodel.Snapshot,
	intent manipulation.Intent,
	robotID string,
) worldmodel.PredicateResult {
	zoneID := "handoff-zone"
	if intent.Destination.Category == manipulation.CategoryTargetZone {
		zoneID = "right-target-zone"
	}
	predicates := []worldmodel.Predicate{
		worldmodel.EntityInside("red-block", zoneID, c.worldMaxAge),
		worldmodel.EntityStable("red-block", 2, c.worldMaxAge),
		worldmodel.RobotHeld(robotID, "", c.worldMaxAge),
	}
	if entity, ok := snapshot.Entities["red-block"]; ok && entity.Evidence.SourceID != "" {
		predicates = append(predicates, worldmodel.SourceFresh(entity.Evidence.SourceID, c.worldMaxAge))
	}
	if robot, ok := snapshot.Robots[robotID]; ok && robot.Evidence.SourceID != "" {
		predicates = append(predicates, worldmodel.SourceFresh(robot.Evidence.SourceID, c.worldMaxAge))
	}
	return worldmodel.All(predicates...).Evaluate(snapshot)
}

// FailIntent marks intent index failed (fail-closed: later intents never
// become ready) and moves the task to FAILED.
func (c *Coordinator) FailIntent(ctx context.Context, taskID string, index int, robotID, reason string) (*Snapshot, error) {
	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	state, err = c.reloadIfNeeded(ctx, state)
	if err != nil {
		c.mu.Unlock()
		return nil, err
	}
	if index < 0 || index >= len(state.intents) {
		c.mu.Unlock()
		return nil, fmt.Errorf("%w: %d", ErrIntentNotFound, index)
	}
	node := state.intents[index]
	c.mu.Unlock()
	if node.TaskRevision > 1 {
		return nil, fmt.Errorf("%w: revision-aware failure is required for revision %d", ErrIntentIdentityConflict, node.TaskRevision)
	}
	return c.FailIntentRevision(ctx, taskID, node.TaskRevision, node.AggregateVersion, index, node.StepID,
		robotID, node.CommandID, node.FencingToken, reason)
}

func (c *Coordinator) FailIntentRevision(
	ctx context.Context,
	taskID string,
	taskRevision uint64,
	aggregateVersion uint64,
	index int,
	stepID string,
	robotID string,
	commandID string,
	fencingToken uint64,
	reason string,
) (*Snapshot, error) {
	if err := c.validateLeadership(ctx); err != nil {
		return nil, err
	}
	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	state, err = c.reloadIfNeeded(ctx, state)
	if err != nil {
		return nil, err
	}
	if index < 0 || index >= len(state.intents) {
		return nil, fmt.Errorf("%w: %d", ErrIntentNotFound, index)
	}
	node := &state.intents[index]
	if taskRevision != state.taskRevision || taskRevision != node.TaskRevision {
		return nil, fmt.Errorf("%w: got %d, active %d", ErrStaleTaskRevision, taskRevision, state.taskRevision)
	}
	if aggregateVersion != node.AggregateVersion || stepID != node.StepID || commandID != node.CommandID || fencingToken != node.FencingToken {
		return nil, fmt.Errorf("%w: failure identity does not match active command", ErrIntentIdentityConflict)
	}
	if node.Status == StatusFailed && node.Claimed == robotID {
		return c.snapshotLocked(ctx, state)
	}
	before := cloneTaskState(state)
	c.reclaimStaleLocked(state)
	node = &state.intents[index]
	if node.Claimed != robotID || node.Status != StatusRunning {
		*state = *before
		return nil, fmt.Errorf("intent %d is not running on %s", index, robotID)
	}
	node.Status = StatusFailed
	node.Finished = c.now().UTC()
	node.Error = reason
	if err := c.persistLocked(ctx, state, "INTENT_FAILED", fmt.Sprintf("%s/intent/%d/failed", taskID, index), map[string]any{
		"intentIndex": index, "robotId": robotID, "reason": reason, "stepId": node.StepID,
		"commandId": node.CommandID, "fencingToken": node.FencingToken, "worldRevision": node.WorldRevision,
	}, nil); err != nil {
		*state = *before
		return nil, err
	}
	if c.resources != nil && node.ResourceID != "" && node.FencingToken != 0 {
		if releaseErr := c.resources.Release(ctx, node.ResourceID, robotID, node.FencingToken); releaseErr != nil &&
			!errors.Is(releaseErr, lease.ErrLeaseNotFound) && !errors.Is(releaseErr, lease.ErrLeaseExpired) &&
			!errors.Is(releaseErr, lease.ErrStaleFencingToken) {
			return nil, releaseErr
		}
	}
	c.advanceTaskState(ctx, taskID, taskgraph.StateFailed)
	return c.snapshotLocked(ctx, state)
}

// ReconcileIntent resolves an intent whose outcome was never established.
//
// It is the only way out of StatusUnknownOutcome, and it exists so that "the
// worker went quiet" cannot quietly become "the step may be run again". A person
// either established that nothing was dispatched (NEVER_ACTED, the only decision
// that returns the intent to the claimable pool) or that the step cannot be
// continued (ABANDON, which fails it).
//
// Both a person and a reason are required. The reason is not ceremony: the
// decision that re-arms a physical action is exactly the decision the next
// reader has to be able to disagree with, and "who decided this was safe to
// retry, and on what grounds" has no other answer anywhere in the system.
func (c *Coordinator) ReconcileIntent(
	ctx context.Context, taskID string, index int,
	decision ReconcileDecision, actor, note string,
) (*Snapshot, error) {
	if err := c.validateLeadership(ctx); err != nil {
		return nil, err
	}
	// Validate before taking the lock: a refused reconciliation must not mutate
	// anything, and the two checks below have no dependency on the loaded state.
	if strings.TrimSpace(actor) == "" {
		return nil, errors.New("reconciliation requires the person making the decision")
	}
	if strings.TrimSpace(note) == "" {
		return nil, errors.New("reconciliation requires a reason")
	}
	switch decision {
	case ReconcileNeverActed, ReconcileAbandon:
	default:
		return nil, fmt.Errorf("unknown reconciliation decision %q", decision)
	}

	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	state, err = c.reloadIfNeeded(ctx, state)
	if err != nil {
		return nil, err
	}
	if index < 0 || index >= len(state.intents) {
		return nil, fmt.Errorf("%w: %d", ErrIntentNotFound, index)
	}
	before := cloneTaskState(state)
	// Evaluate the claim lease first, so the answer does not depend on whether
	// some earlier call happened to run the reclaim pass. Without this, the same
	// intent reconciled two seconds apart would be "RUNNING, not awaiting
	// reconciliation" or "UNKNOWN_OUTCOME" depending on unrelated traffic.
	if c.reclaimStaleLocked(state) {
		if err := c.persistLocked(ctx, state, "INTENT_CLAIM_EXPIRED",
			fmt.Sprintf("%s/reclaim/%d", taskID, state.version+1),
			map[string]any{"intentIndex": index, "stepId": state.intents[index].StepID,
				"reason": "claim lease expired with the outcome unknown"}, nil); err != nil {
			*state = *before
			return nil, err
		}
		// The lapse is a recorded fact now. A later refusal in this call is about
		// the reconciliation, not about the lapse, so it must not roll it back.
		before = cloneTaskState(state)
	}
	node := &state.intents[index]
	if node.Status != StatusUnknownOutcome {
		return nil, fmt.Errorf("%w: intent %d is %s, not awaiting reconciliation", ErrReconcileRequired, index, node.Status)
	}

	now := c.now().UTC()
	// Captured before the switch: ReconcileNeverActed clears Claimed, and the
	// resource release below still has to name the holder whose fence it drops.
	claimedBy := node.Claimed
	releasedResource, releasedToken := node.ResourceID, node.FencingToken
	switch decision {
	case ReconcileNeverActed:
		node.Status = StatusReady
		node.Claimed = ""
		node.Started = time.Time{}
		node.Finished = time.Time{}
		node.Error = ""
	case ReconcileAbandon:
		node.Status = StatusFailed
		node.Finished = now
		node.Error = "abandoned after reconciliation: " + strings.TrimSpace(note)
	}
	node.ReconciledBy = strings.TrimSpace(actor)
	node.ReconciledAt = now
	node.ReconcileDecision = string(decision)
	node.ReconcileNote = strings.TrimSpace(note)

	// The fence that guarded the lapsed claim is spent. Dropping it makes the
	// next claim acquire a fresh, strictly higher token instead of re-validating
	// one whose lease has already expired.
	node.FencingToken = 0

	if err := c.persistLocked(ctx, state, "INTENT_RECONCILED", fmt.Sprintf("%s/intent/%d/reconciled", taskID, index), map[string]any{
		"intentIndex": index, "decision": string(decision), "actor": node.ReconciledBy,
		"note": node.ReconcileNote, "stepId": node.StepID, "claimedBy": claimedBy,
	}, nil); err != nil {
		*state = *before
		return nil, err
	}

	if c.resources != nil && releasedResource != "" && releasedToken != 0 {
		if releaseErr := c.resources.Release(ctx, releasedResource, claimedBy, releasedToken); releaseErr != nil &&
			!errors.Is(releaseErr, lease.ErrLeaseNotFound) && !errors.Is(releaseErr, lease.ErrLeaseExpired) &&
			!errors.Is(releaseErr, lease.ErrStaleFencingToken) {
			return nil, releaseErr
		}
	}
	if decision == ReconcileAbandon {
		c.advanceTaskState(ctx, taskID, taskgraph.StateFailed)
	}
	return c.snapshotLocked(ctx, state)
}

// Snapshot returns the coordinator view of a task.
func (c *Coordinator) Snapshot(ctx context.Context, taskID string) (*Snapshot, error) {
	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	state, err = c.reloadIfNeeded(ctx, state)
	if err != nil {
		return nil, err
	}
	before := cloneTaskState(state)
	if c.reclaimStaleLocked(state) {
		if err := c.persistLocked(ctx, state, "INTENT_CLAIM_EXPIRED", fmt.Sprintf("%s/reclaim/%d", taskID, state.version+1), nil, nil); err != nil {
			*state = *before
			return nil, err
		}
	}
	return c.snapshotLocked(ctx, state)
}

// DomainEvents returns the immutable coordinator audit stream for a task.
// Harness agents use this cursor-based surface to distinguish a verified
// physical handoff from a merely completed command sequence.
func (c *Coordinator) DomainEvents(ctx context.Context, taskID string, afterVersion uint64, limit int) ([]eventlog.DomainEvent, error) {
	if limit <= 0 || limit > 1000 {
		limit = 100
	}
	return c.store.ListEvents(ctx, "fleet_task", taskID, afterVersion, limit)
}

// DispatchOutbox publishes durable ready-intent notifications. Claims are
// released on failure and expire automatically in the store, so a process
// crash at any point is recoverable by this or another coordinator instance.
func (c *Coordinator) DispatchOutbox(ctx context.Context, limit int) (int, error) {
	if c.enqueue == nil {
		return 0, errors.New("outbox publisher is not configured")
	}
	// CompleteIntent holds the same lock from durable state commit through
	// resource projection. Serializing dispatch here prevents a ready-intent
	// notification from overtaking that projection in this coordinator.
	c.mu.Lock()
	defer c.mu.Unlock()
	entries, err := c.store.ClaimOutbox(ctx, limit)
	if err != nil {
		return 0, err
	}
	delivered := 0
	var firstErr error
	for _, entry := range entries {
		var payload struct {
			TaskID   string   `json:"taskId"`
			RobotIDs []string `json:"robotIds"`
		}
		if err := json.Unmarshal(entry.Payload, &payload); err != nil || payload.TaskID == "" {
			_ = c.store.ReleaseOutbox(ctx, entry.ID)
			if firstErr == nil {
				if err != nil {
					firstErr = err
				} else {
					firstErr = errors.New("outbox task id is required")
				}
			}
			continue
		}
		if err := c.enqueue(ctx, payload.TaskID, payload.RobotIDs); err != nil {
			_ = c.store.ReleaseOutbox(ctx, entry.ID)
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		if err := c.store.AckOutbox(ctx, entry.ID); err != nil {
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		delivered++
	}
	return delivered, firstErr
}

func (c *Coordinator) snapshotLocked(ctx context.Context, state *taskState) (*Snapshot, error) {
	task, err := c.service.Get(ctx, state.taskID)
	if err != nil {
		return nil, err
	}
	snapshot := &Snapshot{
		TaskID: state.taskID, TaskRevision: state.taskRevision, AggregateVersion: state.aggregateVersion,
		Request: task.Request, State: string(task.State),
		Updated: c.now().UTC(),
	}
	seen := map[string]struct{}{}
	for _, node := range state.intents {
		snapshot.Intents = append(snapshot.Intents, node)
		if node.RobotID != "" {
			seen[node.RobotID] = struct{}{}
		}
		if node.Claimed != "" {
			seen[node.Claimed] = struct{}{}
		}
	}
	for robotID := range seen {
		snapshot.Robots = append(snapshot.Robots, robotID)
	}
	return snapshot, nil
}

func (c *Coordinator) persistLocked(
	ctx context.Context,
	state *taskState,
	eventType string,
	idempotencyKey string,
	payload map[string]any,
	outbox []eventlog.OutboxEntry,
) error {
	nextVersion := state.version + 1
	wire, err := json.Marshal(persistedState{TaskID: state.taskID, TaskRevision: state.taskRevision,
		AggregateVersion: state.aggregateVersion, Intents: state.intents})
	if err != nil {
		return err
	}
	now := c.now().UTC()
	eventID := fmt.Sprintf("%s/%020d/%s", state.taskID, nextVersion, eventType)
	checkpoint := eventlog.Checkpoint{
		AggregateID: state.taskID, Version: nextVersion, EventCursor: eventID,
		Data: append(json.RawMessage(nil), wire...), CreatedAt: now,
	}
	if payload == nil {
		payload = map[string]any{}
	}
	payload["taskRevision"] = state.taskRevision
	payload["aggregateVersion"] = state.aggregateVersion
	request := eventlog.CommitRequest{
		ExpectedVersion: state.version,
		State: eventlog.AggregateState{
			AggregateID: state.taskID, Version: nextVersion, Data: wire, UpdatedAt: now,
		},
		Events: []eventlog.DomainEvent{{
			EventID: eventID, AggregateType: "fleet_task", AggregateID: state.taskID,
			AggregateVersion: nextVersion, EventType: eventType, Payload: payload,
			IdempotencyKey: idempotencyKey, CorrelationID: state.taskID, Actor: "coordinator", OccurredAt: now,
		}},
		Outbox: outbox, Checkpoint: &checkpoint,
	}
	if err := c.store.Commit(ctx, request); err != nil {
		return err
	}
	state.version = nextVersion
	return nil
}

func cloneTaskState(state *taskState) *taskState {
	clone := &taskState{taskID: state.taskID, version: state.version, taskRevision: state.taskRevision,
		aggregateVersion: state.aggregateVersion, intents: make([]IntentNode, len(state.intents))}
	copy(clone.intents, state.intents)
	for index := range clone.intents {
		clone.intents[index].HarnessEvidence = append([]string(nil), state.intents[index].HarnessEvidence...)
	}
	return clone
}

func (c *Coordinator) validateLeadership(ctx context.Context) error {
	c.mu.Lock()
	manager := c.lease
	grant := c.leader
	c.mu.Unlock()
	if manager == nil {
		return nil
	}
	if err := manager.Validate(ctx, grant.ResourceID, grant.Owner, grant.Token); err != nil {
		return fmt.Errorf("%w: %v", ErrLeadershipLost, err)
	}
	return nil
}

// reloadIfNeeded rebuilds the intent view when the persisted task changed
// (e.g. after a coordinator restart or task edit): preserved running/terminal
// statuses are kept, and new intents are appended. Callers must hold c.mu.
func (c *Coordinator) reloadIfNeeded(ctx context.Context, state *taskState) (*taskState, error) {
	task, err := c.service.Get(ctx, state.taskID)
	if err != nil {
		return nil, err
	}
	if task.CurrentRevision == state.taskRevision {
		state.aggregateVersion = task.AggregateVersion
		return state, nil
	}
	if hasRunningIntent(state) {
		return state, nil
	}
	if err := c.reconcileRevisionLocked(ctx, state, task); err != nil {
		return nil, err
	}
	return state, nil
}

// IntentAction converts an intent action into a display name used by the
// console.
func IntentAction(intent manipulation.Intent) string {
	if intent.Action != "" {
		return intent.Action
	}
	return "unspecified"
}
