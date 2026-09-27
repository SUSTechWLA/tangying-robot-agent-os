package coordinator

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var ErrCancellationRequested = errors.New("operator cancellation requested")

func resourceOwner(node *IntentNode, fallback string) string {
	if node.LeaseOwner != "" {
		return node.LeaseOwner
	}
	return fallback
}

// RenewIntent preserves the original dispatch time used for evidence freshness.
// A lapsed lease remains UNKNOWN_OUTCOME and can never be resurrected by a heartbeat.
func (c *Coordinator) RenewIntent(ctx context.Context, taskID string, claimed *IntentNode, robot string) error {
	if claimed == nil {
		return errors.New("claim identity required")
	}
	if err := c.validateLeadership(ctx); err != nil {
		return err
	}
	state, err := c.ensure(ctx, taskID)
	if err != nil {
		return err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	state, err = c.reloadIfNeeded(ctx, state)
	if err != nil {
		return err
	}
	if claimed.Index < 0 || claimed.Index >= len(state.intents) {
		return ErrIntentNotFound
	}
	before := cloneTaskState(state)
	if c.reclaimStaleLocked(state) {
		if err := c.persistLocked(ctx, state, "INTENT_CLAIM_EXPIRED", fmt.Sprintf("%s/expired/%d", taskID, state.version+1), nil, nil); err != nil {
			*state = *before
			return err
		}
	}
	node := &state.intents[claimed.Index]
	if node.Status != StatusRunning || node.Claimed != robot || node.TaskRevision != claimed.TaskRevision || node.AggregateVersion != claimed.AggregateVersion || node.StepID != claimed.StepID || node.CommandID != claimed.CommandID || node.FencingToken != claimed.FencingToken {
		return ErrClaimExpired
	}
	task, err := c.service.Get(ctx, taskID)
	if err != nil {
		return err
	}
	for _, e := range task.Events {
		if e.Type == "CANCEL_REQUESTED" {
			return ErrCancellationRequested
		}
	}
	if !task.Approved || task.CurrentRevision != node.TaskRevision || task.State == "CANCELLED" || task.State == "FAILED" {
		return ErrStaleTaskRevision
	}
	if node.ResourceID != "" {
		if c.resources == nil {
			return errors.New("resource lease unavailable")
		}
		if _, err := c.resources.Renew(ctx, node.ResourceID, resourceOwner(node, robot), node.FencingToken, c.resourceTTL); err != nil {
			return err
		}
	}
	node.LeaseRenewed = c.now().UTC()
	if err := c.persistLocked(ctx, state, "INTENT_LEASE_RENEWED", fmt.Sprintf("%s/renew/%d/%d", taskID, node.Index, state.version+1), nil, nil); err != nil {
		*state = *before
		return err
	}
	return nil
}

func (c *Coordinator) verifyCapabilityNode(ctx context.Context, task *tasks.Task, node *IntentNode) error {
	plan := task.Plan.Capabilities
	if node.Index >= len(plan.Calls) || plan.RobotID != node.Claimed || node.ResourceID != "robot:"+node.Claimed || node.FencingToken == 0 {
		return ErrIntentIdentityConflict
	}
	if c.resources == nil {
		return ErrIntentUnverifiable
	}
	if err := c.resources.Validate(ctx, node.ResourceID, node.LeaseOwner, node.FencingToken); err != nil {
		return err
	}
	call := plan.Calls[node.Index]
	for i := len(task.Events) - 1; i >= 0; i-- {
		e := task.Events[i]
		if e.Type != "CAPABILITY_VERIFIED" || e.StepID != node.StepID {
			continue
		}
		if e.Payload["tool"] != call.Tool || e.Payload["robotId"] != node.Claimed || e.Payload["leaseCommandId"] != node.CommandID || e.Payload["catalogRevision"] != plan.CatalogRevision || e.Payload["callBinding"] != capability.Fingerprint(call) {
			return ErrIntentIdentityConflict
		}
		evidence, ok := e.Payload["evidence"].(map[string]any)
		if !ok || len(evidence) == 0 {
			return ErrIntentUnverifiable
		}
		return nil
	}
	return fmt.Errorf("%w: provider verification event missing", ErrIntentUnverifiable)
}

func (c *Coordinator) validateCapabilityEvent(ctx context.Context, state *taskState, event tasks.TaskEvent) error {
	if !strings.HasPrefix(event.Type, "CAPABILITY_") {
		return nil
	}
	task, err := c.service.Get(ctx, state.taskID)
	if err != nil {
		return err
	}
	if task.Plan == nil || task.Plan.Capabilities == nil {
		return errors.New("capability evidence requires a capability goal")
	}
	for i := range state.intents {
		n := &state.intents[i]
		if n.StepID != event.StepID {
			continue
		}
		before := cloneTaskState(state)
		if c.reclaimStaleLocked(state) {
			if err := c.persistLocked(ctx, state, "INTENT_CLAIM_EXPIRED", fmt.Sprintf("%s/expired/%d", state.taskID, state.version+1), nil, nil); err != nil {
				*state = *before
				return err
			}
		}
		if n.Status != StatusRunning || event.Payload["robotId"] != n.Claimed || event.Payload["leaseCommandId"] != n.CommandID || event.Payload["catalogRevision"] != task.Plan.Capabilities.CatalogRevision || event.Payload["callBinding"] != capability.Fingerprint(task.Plan.Capabilities.Calls[n.Index]) {
			return ErrIntentIdentityConflict
		}
		return nil
	}
	return ErrIntentIdentityConflict
}
