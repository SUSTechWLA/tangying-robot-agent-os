package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var ErrHandoffUncertain = errors.New("handoff execution outcome unknown; reconciliation required")
var ErrCompletionPending = errors.New("executed handoff awaits cloud completion")

const contextAccepted = "ACCEPTED"
const contextExecuted = "EXECUTED"
const contextCompleted = "COMPLETED"
const completionTopic = "edge/context-completion"

type contextCheckpoint struct {
	SchemaVersion string                 `json:"schemaVersion"`
	Node          coordinator.IntentNode `json:"node"`
	Phase         string                 `json:"phase"`
}

func (w *Worker) contextStore() (eventlog.Store, error) {
	store, ok := w.config.ExecutionStore.(eventlog.Store)
	if !ok || store == nil {
		return nil, errors.New("durable atomic context store required")
	}
	return store, nil
}

func contextKey(taskID, robotID string) string {
	digest, _ := contextcontract.Fingerprint([]string{taskID, robotID})
	return "edge-context/" + digest
}

func contextOutboxID(node *coordinator.IntentNode) string {
	return "context-completion/" + node.ContextBasis.Digest
}

func (w *Worker) loadContext(ctx context.Context, taskID string) (eventlog.AggregateState, contextCheckpoint, bool, error) {
	store, err := w.contextStore()
	if err != nil {
		return eventlog.AggregateState{}, contextCheckpoint{}, false, err
	}
	state, found, err := store.LoadState(ctx, contextKey(taskID, w.config.RobotID))
	if err != nil || !found {
		return state, contextCheckpoint{}, found, err
	}
	var saved contextCheckpoint
	if err := json.Unmarshal(state.Data, &saved); err != nil {
		return state, saved, true, err
	}
	if saved.SchemaVersion != "edge.context.checkpoint.v1" || saved.Node.ContextBasis == nil ||
		saved.Node.ContextBasis.TaskID != taskID || saved.Node.ContextBasis.RobotID != w.config.RobotID ||
		(saved.Phase != contextAccepted && saved.Phase != contextExecuted && saved.Phase != contextCompleted) {
		return state, saved, true, fmt.Errorf("%w: invalid durable checkpoint", contextcontract.ErrInvalid)
	}
	if err := saved.Node.ContextBasis.Validate(); err != nil {
		return state, saved, true, err
	}
	return state, saved, true, nil
}

func (w *Worker) persistContext(ctx context.Context, node *coordinator.IntentNode, phase string, version uint64) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	store, err := w.contextStore()
	if err != nil {
		return err
	}
	b := node.ContextBasis
	basisJSON, err := json.Marshal(b)
	if err != nil {
		return err
	}
	wire, err := json.Marshal(contextCheckpoint{SchemaVersion: "edge.context.checkpoint.v1", Node: *node, Phase: phase})
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	key := contextKey(b.TaskID, b.RobotID)
	eventID := fmt.Sprintf("%s/%020d", key, version+1)
	req := eventlog.CommitRequest{ExpectedVersion: version,
		State: eventlog.AggregateState{AggregateID: key, Version: version + 1, Data: wire, UpdatedAt: now},
		Events: []eventlog.DomainEvent{{EventID: eventID, AggregateID: key, AggregateType: "edge_context", AggregateVersion: version + 1,
			EventType: "CONTEXT_" + phase, IdempotencyKey: eventID, CorrelationID: b.TaskID, CausationID: b.CommandID,
			Actor: w.config.RobotID, OccurredAt: now, Payload: map[string]any{"contextBasis": b, "contextBasisJSON": string(basisJSON), "phase": phase}}},
		Checkpoint: &eventlog.Checkpoint{AggregateID: key, Version: version + 1, EventCursor: eventID, Data: wire, CreatedAt: now}}
	if phase == contextExecuted {
		req.Outbox = []eventlog.OutboxEntry{{ID: contextOutboxID(node), Topic: completionTopic, Key: b.TaskID, Payload: wire, CreatedAt: now}}
	}
	return store.Commit(ctx, req)
}

func (w *Worker) validateContext(task *tasks.Task, node *coordinator.IntentNode) error {
	if err := coordinator.ValidateContextBasis(task, node); err != nil {
		return err
	}
	if !task.Approved || node.ContextBasis.RobotID != w.config.RobotID ||
		(node.ContextBasis.World.Mode == "coordinator_world" && node.ContextBasis.World.WorldID != w.config.WorldID) {
		return fmt.Errorf("%w: approval/robot/world scope mismatch", contextcontract.ErrInvalid)
	}
	return nil
}

// acceptContext is the write-before-action boundary. Concurrent workers race on
// the existing event-store CAS; the loser cannot dispatch even the first step.
func (w *Worker) acceptContext(ctx context.Context, task *tasks.Task, node *coordinator.IntentNode) (string, error) {
	if err := w.validateContext(task, node); err != nil {
		return "", err
	}
	state, saved, found, err := w.loadContext(ctx, task.ID)
	if err != nil {
		return "", err
	}
	if found {
		prior, next := saved.Node.ContextBasis, node.ContextBasis
		if next.TaskRevision < prior.TaskRevision || next.ClaimVersion < prior.ClaimVersion {
			return "", fmt.Errorf("%w: stale handoff", contextcontract.ErrInvalid)
		}
		if next.ClaimVersion == prior.ClaimVersion {
			if next.Digest != prior.Digest {
				return "", fmt.Errorf("%w: conflicting handoff", contextcontract.ErrInvalid)
			}
			if saved.Phase == contextAccepted {
				return "", ErrHandoffUncertain
			}
			return saved.Phase, nil
		}
		if saved.Phase != contextCompleted {
			return "", ErrHandoffUncertain
		}
	}
	if err := w.beforeContextStep(ctx, task.ID, node); err != nil {
		return "", err
	}
	if err := w.persistContext(ctx, node, contextAccepted, state.Version); err != nil {
		return "", err
	}
	return contextAccepted, nil
}

func (w *Worker) beforeContextStep(ctx context.Context, taskID string, node *coordinator.IntentNode) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	task, err := w.config.Cloud.GetTask(ctx, taskID)
	if err != nil {
		return err
	}
	if err := w.validateContext(task, node); err != nil {
		return err
	}
	if isTerminalTaskState(string(task.State)) || task.State == taskgraph.StateWaitingUser || task.State == taskgraph.StateRecoverableFailure {
		return fmt.Errorf("%w: task no longer dispatchable", contextcontract.ErrInvalid)
	}
	renewer, ok := w.config.Cloud.(claimRenewer)
	if !ok {
		return errors.New("live cloud claim validation required")
	}
	return renewer.RenewIntentRevision(ctx, taskID, node, w.config.RobotID)
}

func (w *Worker) beforeRuntimeStep(ctx context.Context, taskID string, node *coordinator.IntentNode, physical bool) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	snapshot, err := w.config.Runtime.Info(ctx)
	if err != nil {
		return err
	}
	b := node.ContextBasis
	if snapshot.RobotID != b.RobotID || (b.Adapter != "auto" && snapshot.Adapter != b.Adapter) ||
		(b.CatalogRevision != "" && snapshot.CatalogRevision != b.CatalogRevision) {
		return fmt.Errorf("%w: connected Runtime identity/catalog changed", contextcontract.ErrInvalid)
	}
	if physical && (b.ResourceID == "" || b.FencingToken == 0 || b.CatalogRevision == "" || !snapshot.PhysicalReady()) {
		return fmt.Errorf("%w: physical resource/catalog/readiness missing", contextcontract.ErrInvalid)
	}
	// A slow Info RPC must not leave a cancellation/revision check behind it.
	// Re-read authority last, immediately before the caller dispatches.
	return w.beforeOwnedContextStep(ctx, taskID, node)
}

func (w *Worker) beforeOwnedContextStep(ctx context.Context, taskID string, node *coordinator.IntentNode) error {
	_, saved, found, err := w.loadContext(ctx, taskID)
	if err != nil {
		return err
	}
	if !found || node.ContextBasis == nil || saved.Phase != contextAccepted || saved.Node.ContextBasis.Digest != node.ContextBasis.Digest {
		return ErrHandoffUncertain
	}
	return w.beforeContextStep(ctx, taskID, node)
}

func (w *Worker) markContextExecuted(ctx context.Context, taskID string, node *coordinator.IntentNode) error {
	state, saved, found, err := w.loadContext(ctx, taskID)
	if err != nil {
		return fmt.Errorf("%w: checkpoint read: %v", ErrHandoffUncertain, err)
	}
	if !found || saved.Phase != contextAccepted || saved.Node.ContextBasis.Digest != node.ContextBasis.Digest {
		return ErrHandoffUncertain
	}
	if err := w.persistContext(ctx, node, contextExecuted, state.Version); err != nil {
		return fmt.Errorf("%w: executed checkpoint: %v", ErrHandoffUncertain, err)
	}
	return nil
}

// completeContext is transport-only replay. The durable EXECUTED phase/outbox
// was written after successful execution; no UNKNOWN phase can enter here.
func (w *Worker) completeContext(ctx context.Context, taskID string, node *coordinator.IntentNode) error {
	state, saved, found, err := w.loadContext(ctx, taskID)
	if err != nil {
		return err
	}
	if !found || saved.Node.ContextBasis.Digest != node.ContextBasis.Digest {
		return ErrHandoffUncertain
	}
	if saved.Phase == contextCompleted {
		return nil
	}
	if saved.Phase != contextExecuted {
		return ErrHandoffUncertain
	}
	task, err := w.config.Cloud.GetTask(ctx, taskID)
	if err != nil {
		return err
	}
	if err := w.validateContext(task, node); err != nil {
		return err
	}
	if err := retryWorldCompletion(ctx, 40, 250*time.Millisecond, func(callCtx context.Context) error {
		return w.config.Cloud.CompleteIntentRevision(callCtx, taskID, node, w.config.RobotID)
	}); err != nil {
		return fmt.Errorf("%w: %v", ErrCompletionPending, err)
	}
	if err := w.persistContext(ctx, node, contextCompleted, state.Version); err != nil {
		return fmt.Errorf("%w: %v", ErrCompletionPending, err)
	}
	store, _ := w.contextStore()
	return store.AckOutbox(ctx, contextOutboxID(node))
}

// Replay only persisted successful completions, even when a ready notification
// was lost before a restart. Queue deliveries themselves carry no authority.
func (w *Worker) dispatchContextOutbox(ctx context.Context) error {
	store, err := w.contextStore()
	if err != nil {
		return err
	}
	entries, err := store.ClaimOutbox(ctx, 10)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if entry.Topic != completionTopic {
			_ = store.ReleaseOutbox(ctx, entry.ID)
			continue
		}
		var saved contextCheckpoint
		err := json.Unmarshal(entry.Payload, &saved)
		if err == nil && (saved.Node.ContextBasis == nil || saved.Phase != contextExecuted || entry.ID != contextOutboxID(&saved.Node)) {
			err = contextcontract.ErrInvalid
		}
		if err == nil {
			err = w.completeContext(ctx, entry.Key, &saved.Node)
		}
		if err == nil {
			err = store.AckOutbox(ctx, entry.ID)
		}
		if err != nil {
			_ = store.ReleaseOutbox(ctx, entry.ID)
			return err
		}
	}
	return nil
}

func isPhysicalStep(step taskgraph.SkillStep) bool {
	return step.SafetyLevel == string(skills.SafetyPhysical)
}
