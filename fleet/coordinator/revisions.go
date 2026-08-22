package coordinator

import (
	"context"
	"errors"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var ErrStaleTaskRevision = errors.New("stale task revision")

var ErrIntentIdentityConflict = errors.New("intent command identity conflict")

func commandIdentity(taskID string, revision uint64, stepID string) string {
	return fmt.Sprintf("%s/revision/%d/step/%s", taskID, revision, stepID)
}

func hasRunningIntent(state *taskState) bool {
	for index := range state.intents {
		if state.intents[index].Status == StatusRunning {
			return true
		}
	}
	return false
}

func (c *Coordinator) activeRevision(ctx context.Context, taskID string, revision uint64) (tasks.RevisionRecord, error) {
	history, err := c.service.ListRevisions(ctx, taskID)
	if err != nil {
		return tasks.RevisionRecord{}, err
	}
	for _, record := range history {
		if record.Revision.Revision == revision {
			return record, nil
		}
	}
	return tasks.RevisionRecord{}, tasks.ErrRevisionNotFound
}

// applyRevisionIdentity initializes a graph from the task's immutable active
// revision. Legacy tasks still receive deterministic step identities.
func (c *Coordinator) applyRevisionIdentity(ctx context.Context, state *taskState, task *tasks.Task) {
	record, err := c.activeRevision(ctx, task.ID, task.CurrentRevision)
	if err == nil {
		state.intents = nodesForRevision(record.Revision, task.AggregateVersion)
		return
	}
	state.intents = nil
	for index, intent := range task.Intent.Tasks() {
		step := tasks.RevisionStep{IntentIndex: index, Action: intent.Action, RobotID: intent.RobotID}
		step.SemanticFingerprint = tasks.SemanticFingerprint(step)
		step.StepID = tasks.StableStepID(index, step.SemanticFingerprint)
		status := StatusPending
		if index == 0 {
			status = StatusReady
		}
		state.intents = append(state.intents, IntentNode{Index: index, StepID: step.StepID,
			TaskRevision: task.CurrentRevision, AggregateVersion: task.AggregateVersion,
			SemanticFingerprint: step.SemanticFingerprint, Action: intent.Action, RobotID: intent.RobotID, Status: status})
	}
}

func nodesForRevision(revision tasks.TaskRevision, aggregateVersion uint64) []IntentNode {
	nodes := make([]IntentNode, 0, len(revision.Steps))
	for index, step := range revision.Steps {
		status := StatusPending
		if index == 0 {
			status = StatusReady
		}
		nodes = append(nodes, IntentNode{
			Index: index, StepID: step.StepID, TaskRevision: revision.Revision, AggregateVersion: aggregateVersion,
			SemanticFingerprint: step.SemanticFingerprint, Action: step.Action, RobotID: step.RobotID,
			Status: status,
		})
	}
	return nodes
}

// RevisionBasis is the coordinator-owned execution/evidence view used when a
// new immutable revision is proposed. The task service never guesses whether
// physical evidence remains valid.
func (c *Coordinator) RevisionBasis(ctx context.Context, taskID string) (tasks.RevisionBasis, error) {
	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return tasks.RevisionBasis{}, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	state, err = c.reloadIfNeeded(ctx, state)
	if err != nil {
		return tasks.RevisionBasis{}, err
	}
	basis := tasks.RevisionBasis{EvidenceValidity: map[string]bool{}}
	var world worldmodel.Snapshot
	if c.world != nil {
		world, err = c.world.Snapshot(ctx)
		if err != nil {
			return tasks.RevisionBasis{}, err
		}
	}
	for _, node := range state.intents {
		if node.Status == StatusRunning {
			basis.RunningStepIDs = append(basis.RunningStepIDs, node.StepID)
		}
		if node.Status == StatusSucceeded {
			basis.EvidenceValidity[node.StepID] = evidenceStillValid(node, world, c.world != nil)
		}
	}
	return basis, nil
}

func evidenceStillValid(node IntentNode, world worldmodel.Snapshot, hasWorld bool) bool {
	if node.HarnessStatus != "SATISFIED" || len(node.HarnessEvidence) == 0 || !hasWorld {
		return false
	}
	available := map[string]struct{}{}
	for _, entity := range world.Entities {
		available[entity.Evidence.ObservationID] = struct{}{}
	}
	for _, robot := range world.Robots {
		available[robot.Evidence.ObservationID] = struct{}{}
	}
	for _, evidenceID := range node.HarnessEvidence {
		if _, ok := available[evidenceID]; !ok {
			return false
		}
	}
	return true
}

// ConfirmRevision confirms an owner-approved proposal. A running physical
// command is allowed to finish; otherwise the revision becomes active now.
func (c *Coordinator) ConfirmRevision(
	ctx context.Context,
	taskID string,
	revision uint64,
	expectedCurrentRevision uint64,
	idempotencyKey string,
) (*tasks.RevisionRecord, error) {
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
	wait := hasRunningIntent(state)
	record, err := c.service.ConfirmRevision(ctx, tasks.ConfirmRevisionCommand{
		TaskID: taskID, Revision: revision, ExpectedCurrentRevision: expectedCurrentRevision,
		IdempotencyKey: idempotencyKey, Actor: "owner", WaitForSafePoint: wait,
	})
	if err != nil {
		return nil, err
	}
	updated, err := c.service.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	state.aggregateVersion = updated.AggregateVersion
	if record.Status == tasks.RevisionActive {
		if err := c.reconcileRevisionLocked(ctx, state, updated); err != nil {
			return nil, err
		}
		return record, nil
	}
	if err := c.persistLocked(ctx, state, "REVISION_WAITING_SAFE_POINT",
		fmt.Sprintf("%s/revision/%d/waiting", taskID, revision), map[string]any{"revision": revision}, nil); err != nil {
		return nil, err
	}
	return record, nil
}

func (c *Coordinator) activateWaitingRevisionLocked(ctx context.Context, state *taskState) (bool, error) {
	if hasRunningIntent(state) {
		return false, nil
	}
	history, err := c.service.ListRevisions(ctx, state.taskID)
	if err != nil {
		return false, err
	}
	var waiting *tasks.RevisionRecord
	for index := range history {
		if history[index].Status == tasks.RevisionWaitingSafePoint {
			waiting = &history[index]
		}
	}
	if waiting == nil {
		return false, nil
	}
	if _, err := c.service.ActivateWaitingRevision(ctx, state.taskID, waiting.Revision.Revision,
		fmt.Sprintf("%s/revision/%d/safe-point", state.taskID, waiting.Revision.Revision)); err != nil {
		return false, err
	}
	task, err := c.service.Get(ctx, state.taskID)
	if err != nil {
		return false, err
	}
	if err := c.reconcileRevisionLocked(ctx, state, task); err != nil {
		return false, err
	}
	return true, nil
}

// ReconcileRevision refreshes the coordinator graph after an externally
// activated revision. It is also used by failover/reload paths.
func (c *Coordinator) ReconcileRevision(ctx context.Context, taskID string) (*Snapshot, error) {
	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return nil, err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if hasRunningIntent(state) {
		return nil, errors.New("revision cannot reconcile before the running command reaches a safe point")
	}
	task, err := c.service.Get(ctx, taskID)
	if err != nil {
		return nil, err
	}
	if err := c.reconcileRevisionLocked(ctx, state, task); err != nil {
		return nil, err
	}
	return c.snapshotLocked(ctx, state)
}

func (c *Coordinator) reconcileRevisionLocked(ctx context.Context, state *taskState, task *tasks.Task) error {
	if state.taskRevision == task.CurrentRevision {
		state.aggregateVersion = task.AggregateVersion
		return nil
	}
	record, err := c.activeRevision(ctx, task.ID, task.CurrentRevision)
	if err != nil {
		return err
	}
	old := make(map[string]IntentNode, len(state.intents))
	for _, node := range state.intents {
		old[node.StepID] = node
	}
	world := worldmodel.Snapshot{}
	hasWorld := false
	if c.world != nil {
		world, err = c.world.Snapshot(ctx)
		if err != nil {
			return err
		}
		hasWorld = true
	}
	next := nodesForRevision(record.Revision, task.AggregateVersion)
	for index := range next {
		prior, ok := old[next[index].StepID]
		if !ok || prior.SemanticFingerprint != next[index].SemanticFingerprint {
			continue
		}
		if prior.Status == StatusSucceeded && evidenceStillValid(prior, world, hasWorld) {
			prior.Index = index
			prior.TaskRevision = task.CurrentRevision
			prior.AggregateVersion = task.AggregateVersion
			prior.SafeCheckpoint = true
			next[index] = prior
		}
	}
	for index := range next {
		if next[index].Status == StatusSucceeded {
			continue
		}
		if index == 0 || allPriorSucceeded(next, index) {
			next[index].Status = StatusReady
		} else {
			next[index].Status = StatusPending
		}
	}
	state.intents = next
	state.taskRevision = task.CurrentRevision
	state.aggregateVersion = task.AggregateVersion
	return c.persistLocked(ctx, state, "REVISION_RECONCILED",
		fmt.Sprintf("%s/revision/%d/reconciled", task.ID, task.CurrentRevision), map[string]any{"revision": task.CurrentRevision}, nil)
}

func allPriorSucceeded(nodes []IntentNode, index int) bool {
	for prior := 0; prior < index; prior++ {
		if nodes[prior].Status != StatusSucceeded {
			return false
		}
	}
	return true
}
