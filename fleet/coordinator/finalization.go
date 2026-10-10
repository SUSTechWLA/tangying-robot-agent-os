package coordinator

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/contextcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
)

const finalizationTopic = "fleet/context-finalization"

func finalizationID(r *completionReceipt) string { return "completion-finalization/" + r.Basis.Digest }

// A spent fence is already released for this command. Never release a newer
// owner/token, reacquire a lease, or transfer a resource during replay.
func (c *Coordinator) releaseCompletedGrant(ctx context.Context, grant *lease.Grant) error {
	if grant == nil {
		return nil
	}
	if c.resources == nil {
		return errors.New("completion resource manager unavailable")
	}
	err := c.resources.Release(ctx, grant.ResourceID, grant.Owner, grant.Token)
	if errors.Is(err, lease.ErrLeaseNotFound) || errors.Is(err, lease.ErrLeaseExpired) || errors.Is(err, lease.ErrStaleFencingToken) {
		return nil
	}
	return err
}

func (c *Coordinator) projectCompletedGrant(ctx context.Context, r *completionReceipt) error {
	g := r.Effects.Transition
	if g == nil {
		return nil
	}
	if r.Basis.World.Mode == "edge_local" && c.world == nil {
		return nil
	}
	if c.world == nil || c.worldWriter == nil || c.resources == nil {
		return errors.New("completion world/resource authority unavailable")
	}
	check := func() (bool, error) {
		snapshot, err := c.world.Snapshot(ctx)
		if err != nil {
			return false, err
		}
		if snapshot.WorldID != r.Basis.World.WorldID {
			return false, ErrIntentIdentityConflict
		}
		resource, ok := snapshot.Resources[g.ResourceID]
		if ok && resource.FencingToken > g.Token {
			return true, nil
		}
		if ok && resource.FencingToken == g.Token {
			if resource.Owner != g.Owner {
				return false, ErrIntentIdentityConflict
			}
			return true, nil
		}
		return false, nil
	}
	if done, err := check(); done || err != nil {
		return err
	}
	// An unpublished expired transfer is not current world authority. Stop for
	// reconciliation rather than reviving the grant or inventing a fresh fence.
	if err := c.resources.Validate(ctx, g.ResourceID, g.Owner, g.Token); err != nil {
		return err
	}
	if err := c.publishResource(ctx, *g); err != nil {
		// Another writer may have published a newer fence between the snapshot
		// and this write. The projector rejects stale sequence numbers.
		if done, readErr := check(); done && readErr == nil {
			return nil
		}
		return err
	}
	return nil
}

func (c *Coordinator) finalizedReceipt(ctx context.Context, r *completionReceipt, digest string) (bool, error) {
	cursor := r.Basis.ClaimVersion
	for {
		events, err := c.store.ListEvents(ctx, "fleet_task", r.Basis.TaskID, cursor, 100)
		if err != nil {
			return false, err
		}
		for _, event := range events {
			if event.AggregateVersion <= cursor || event.AggregateID != r.Basis.TaskID {
				return false, ErrIntentIdentityConflict
			}
			cursor = event.AggregateVersion
			if event.IdempotencyKey == finalizationID(r) {
				if event.EventType != "COMPLETION_FINALIZED" || event.Payload["receiptDigest"] != digest {
					return false, ErrIntentIdentityConflict
				}
				return true, nil
			}
		}
		if len(events) < 100 {
			return false, nil
		}
	}
}

// This path performs coordinator bookkeeping only. Its source of authority is
// the exact immutable success receipt; no Runtime or physical API is called.
func (c *Coordinator) finalizeCompletionLocked(ctx context.Context, state *taskState, supplied *completionReceipt) error {
	r, err := c.completionRecord(ctx, &supplied.Basis)
	if err != nil {
		return err
	}
	if r == nil {
		return ErrIntentIdentityConflict
	}
	digest, err := contextcontract.Fingerprint(r)
	if err != nil {
		return err
	}
	want, err := contextcontract.Fingerprint(supplied)
	if err != nil || digest != want {
		return ErrIntentIdentityConflict
	}
	if r.SchemaVersion == "context.completion.v1" {
		return nil
	}
	if done, err := c.finalizedReceipt(ctx, r, digest); err != nil {
		return err
	} else if done {
		return c.store.AckOutbox(ctx, finalizationID(r))
	}
	if state.pendingCompletion == nil {
		return errors.New("successful context lacks pending finalization checkpoint")
	}
	pendingDigest, err := contextcontract.Fingerprint(state.pendingCompletion)
	if err != nil || pendingDigest != digest || state.taskID != r.Basis.TaskID {
		return ErrIntentIdentityConflict
	}
	if err := c.releaseCompletedGrant(ctx, r.Effects.RobotRelease); err != nil {
		return err
	}
	if err := c.projectCompletedGrant(ctx, r); err != nil {
		return err
	}
	if r.Effects.ReleaseTransition {
		if err := c.releaseCompletedGrant(ctx, r.Effects.Transition); err != nil {
			return err
		}
	}
	// A delayed receipt never applies its old terminal transition to a newer
	// revision. Activation itself is idempotent in the task revision store.
	if state.taskRevision == r.Basis.TaskRevision {
		activated, err := c.activateWaitingRevisionLocked(ctx, state)
		if err != nil {
			return err
		}
		if !activated && r.Effects.LastIntent {
			if !c.prefixSucceeded(state, len(state.intents)) {
				return ErrIntentIdentityConflict
			}
			task, err := c.service.Get(ctx, state.taskID)
			if err != nil {
				return err
			}
			switch task.State {
			case taskgraph.StateCancelled, taskgraph.StateFailed, taskgraph.StateSafetyStopped:
				// Respect a later operator terminal decision.
			default:
				c.advanceTaskState(ctx, state.taskID, taskgraph.StateSucceeded)
				task, err = c.service.Get(ctx, state.taskID)
				if err != nil {
					return err
				}
				if task.State != taskgraph.StateSucceeded {
					return errors.New("completion task transition is not durable")
				}
			}
		}
	}
	var ready []eventlog.OutboxEntry
	for _, node := range state.intents {
		if node.Status != StatusReady {
			continue
		}
		payload, err := json.Marshal(map[string]any{"taskId": state.taskID, "robotIds": []string{node.RobotID}, "schemaVersion": "context.ready.v1", "taskRevision": node.TaskRevision, "stepId": node.StepID})
		if err != nil {
			return err
		}
		topic := "robot/" + node.RobotID
		if node.RobotID == "" {
			topic = "fleet/unbound"
		}
		ready = append(ready, eventlog.OutboxEntry{ID: fmt.Sprintf("%s/revision/%d/step/%s/ready", state.taskID, node.TaskRevision, node.StepID), Topic: topic, Key: state.taskID, Payload: payload, CreatedAt: c.now().UTC()})
		break
	}
	before := cloneTaskState(state)
	state.pendingCompletion = nil
	if err := c.persistLocked(ctx, state, "COMPLETION_FINALIZED", finalizationID(r), map[string]any{"contextDigest": r.Basis.Digest, "receiptDigest": digest}, ready); err != nil {
		*state = *before
		return err
	}
	if err := c.store.AckOutbox(ctx, finalizationID(r)); err != nil {
		return err
	}
	for _, entry := range ready {
		if c.enqueue != nil {
			var payload struct {
				RobotIDs []string `json:"robotIds"`
			}
			_ = json.Unmarshal(entry.Payload, &payload)
			if c.enqueue(ctx, state.taskID, payload.RobotIDs) == nil {
				_ = c.store.AckOutbox(ctx, entry.ID)
			}
		}
	}
	return nil
}
