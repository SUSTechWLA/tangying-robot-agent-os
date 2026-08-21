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
)

// IntentNode is one subtask node of the distributed task graph.
type IntentNode struct {
	Index           int          `json:"index"`
	Action          string       `json:"action"`
	RobotID         string       `json:"robotId,omitempty"` // bound robot, "" = any worker
	Claimed         string       `json:"claimed,omitempty"` // worker that claimed it
	Status          IntentStatus `json:"status"`
	ResourceID      string       `json:"resourceId,omitempty"`
	FencingToken    uint64       `json:"fencingToken,omitempty"`
	CatalogRevision string       `json:"catalogRevision,omitempty"`
	WorldRevision   uint64       `json:"worldRevision,omitempty"`
	EntitySourceID  string       `json:"entitySourceId,omitempty"`
	EntitySequence  uint64       `json:"entitySequenceBasis,omitempty"`
	EntityCount     uint64       `json:"entityObservationCountBasis,omitempty"`
	RobotSourceID   string       `json:"robotSourceId,omitempty"`
	RobotSequence   uint64       `json:"robotSequenceBasis,omitempty"`
	PredicateState  string       `json:"predicateState,omitempty"`
	HarnessStatus   string       `json:"harnessStatus,omitempty"`
	HarnessReason   string       `json:"harnessReason,omitempty"`
	HarnessEvidence []string     `json:"harnessEvidenceIds,omitempty"`
	Started         time.Time    `json:"startedAt,omitempty"`
	Finished        time.Time    `json:"finishedAt,omitempty"`
	Error           string       `json:"error,omitempty"`
}

// Snapshot is the coordinator view of one distributed task.
type Snapshot struct {
	TaskID  string       `json:"taskId"`
	Request string       `json:"request"`
	State   string       `json:"state"` // task-level state from the task store
	Intents []IntentNode `json:"intents"`
	Updated time.Time    `json:"updatedAt"`
	Robots  []string     `json:"robots"`
}

// ErrIntentNotFound is returned for unknown intent indexes.
var ErrIntentNotFound = errors.New("intent not found")

var ErrLeadershipLost = errors.New("coordinator leadership lease lost")

var ErrWorldNotReady = errors.New("world evidence is not ready")

// IntentClaimLease is how long a worker may hold an intent claim without
// the coordinator reclaiming it. A crashed or disconnected worker therefore
// never blocks the task forever: after the lease expires the intent returns
// to READY and another worker (or the same one after reconnect) can claim
// it again.
const IntentClaimLease = 2 * time.Minute

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
	taskID  string
	version uint64
	intents []IntentNode
}

type persistedState struct {
	TaskID  string       `json:"taskId"`
	Intents []IntentNode `json:"intents"`
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

// reclaimStaleLocked returns stale RUNNING intents to READY when their
// claim lease expired. Callers must hold c.mu.
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
		node.Status = StatusReady
		node.Claimed = ""
		node.Started = time.Time{}
		node.Error = "claim lease expired"
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
		state := &taskState{taskID: persisted.TaskID, version: stored.Version, intents: persisted.Intents}
		c.graphs[taskID] = state
		return state, nil
	}
	task, err := c.service.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	intents := task.Intent.Tasks()
	state := &taskState{taskID: taskID}
	for index, intent := range intents {
		status := StatusPending
		if index == 0 {
			status = StatusReady
		}
		state.intents = append(state.intents, IntentNode{
			Index: index, Action: intent.Action, RobotID: intent.RobotID, Status: status,
		})
	}
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
		state = &taskState{taskID: persisted.TaskID, version: stored.Version, intents: persisted.Intents}
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
		if err := c.persistLocked(ctx, state, "INTENT_CLAIMED", fmt.Sprintf("%s/intent/%d/claim/%d", taskID, index, state.version+1), map[string]any{
			"intentIndex": index, "robotId": robotID,
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

// CompleteIntent marks intent index succeeded for the claiming worker and
// refreshes the next intent to READY (event-driven refresh). When the last
// intent succeeds the whole task transitions to SUCCEEDED.
func (c *Coordinator) CompleteIntent(ctx context.Context, taskID string, index int, robotID string) (*Snapshot, error) {
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
	if index < 0 || index >= len(state.intents) {
		return nil, fmt.Errorf("%w: %d", ErrIntentNotFound, index)
	}
	node := &state.intents[index]
	if node.Claimed != robotID || node.Status != StatusRunning {
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
	}
	node.Status = StatusSucceeded
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
	payload := map[string]any{"intentIndex": index, "robotId": robotID}
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
	if index+1 == len(state.intents) {
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
	if index < 0 || index >= len(state.intents) {
		return nil, fmt.Errorf("%w: %d", ErrIntentNotFound, index)
	}
	node := &state.intents[index]
	if node.Claimed != robotID || node.Status != StatusRunning {
		return nil, fmt.Errorf("intent %d is not running on %s", index, robotID)
	}
	node.Status = StatusFailed
	node.Finished = c.now().UTC()
	node.Error = reason
	if err := c.persistLocked(ctx, state, "INTENT_FAILED", fmt.Sprintf("%s/intent/%d/failed", taskID, index), map[string]any{
		"intentIndex": index, "robotId": robotID, "reason": reason,
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
		TaskID: state.taskID, Request: task.Request, State: string(task.State),
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
	wire, err := json.Marshal(persistedState{TaskID: state.taskID, Intents: state.intents})
	if err != nil {
		return err
	}
	now := c.now().UTC()
	eventID := fmt.Sprintf("%s/%020d/%s", state.taskID, nextVersion, eventType)
	checkpoint := eventlog.Checkpoint{
		AggregateID: state.taskID, Version: nextVersion, EventCursor: eventID,
		Data: append(json.RawMessage(nil), wire...), CreatedAt: now,
	}
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
	clone := &taskState{taskID: state.taskID, version: state.version, intents: make([]IntentNode, len(state.intents))}
	copy(clone.intents, state.intents)
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
	intents := task.Intent.Tasks()
	if len(intents) == len(state.intents) {
		return state, nil
	}
	current := make([]IntentNode, len(state.intents))
	copy(current, state.intents)
	state.intents = nil
	for index, intent := range intents {
		node := IntentNode{Index: index, Action: intent.Action, RobotID: intent.RobotID}
		if index < len(current) {
			node = current[index]
			if intent.RobotID != "" {
				node.RobotID = intent.RobotID
			}
		} else if index == 0 {
			node.Status = StatusReady
		}
		state.intents = append(state.intents, node)
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
