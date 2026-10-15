package edgeagent

import (
	"context"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/eventstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// confirmedPending recognizes only the crash window between the executor's
// durable completion and the goal checkpoint's acknowledgement. A receipt or a
// started step alone is never sufficient: those still require reconciliation.
// This function reads local evidence and never calls a robot or changes state.
func confirmedPending(ctx context.Context, store eventstore.Store, task *tasks.Task, cp checkpoint) (completedCall, bool, error) {
	if !cp.Pending || cp.Complete || cp.ChildID == "" || cp.Index < 0 {
		return completedCall{}, false, nil
	}
	execution, ok := store.(middleware.ExecutionStore)
	if !ok {
		return completedCall{}, false, nil
	}
	child, err := (childEvents{store: store}).Get(ctx, cp.ChildID)
	if err != nil {
		return completedCall{}, false, err
	}
	if child.ID != cp.ChildID || child.CurrentRevision != task.CurrentRevision || child.Plan == nil || child.Plan.Capabilities == nil || child.Plan.Capabilities.RobotID != task.Plan.Capabilities.RobotID || child.Plan.Capabilities.CatalogRevision != task.Plan.Capabilities.CatalogRevision || objective.PlanDigest(child.Plan.Capabilities) != cp.PlanDigest || cp.Index >= len(child.Plan.Capabilities.Calls) {
		return completedCall{}, false, nil
	}
	step := fmt.Sprintf("rev-%d-cap-%02d", child.CurrentRevision, cp.Index+1)
	status, err := execution.StepStatus(ctx, child.ID, step)
	if err != nil || status != middleware.StepCompleted {
		return completedCall{}, false, err
	}
	call := child.Plan.Capabilities.Calls[cp.Index]
	for i := len(child.Events) - 1; i >= 0; i-- {
		event := child.Events[i]
		if event.StepID != step {
			continue
		}
		if event.Type == "CAPABILITY_FAILED" || event.Type == "CAPABILITY_STOP_CONFIRMED" {
			return completedCall{}, false, nil
		}
		if event.Type != "CAPABILITY_VERIFIED" {
			continue
		}
		p := event.Payload
		mutates, classified := p["mutatesWorld"].(bool)
		if !classified || p["tool"] != call.Tool || p["commandId"] != child.ID+"/"+step || p["robotId"] != child.Plan.Capabilities.RobotID || !objective.Equal(p["taskRevision"], child.CurrentRevision) || p["evidence"] == nil {
			return completedCall{}, false, nil
		}
		for _, done := range cp.Completed {
			if done.ChildID == child.ID && done.StepID == step {
				return completedCall{}, false, nil
			}
		}
		return completedCall{ChildID: child.ID, StepID: step, Binding: capability.Fingerprint(call), EffectBinding: objective.EffectKey(call), Tool: call.Tool, Mutates: mutates}, true, nil
	}
	return completedCall{}, false, nil
}

// A recorded receipt cannot downgrade a writer into a read. The execution
// classification comes from the current, objective-bound provider catalog.
func validateRecoveredMutation(done completedCall, catalog map[string]capability.Manifest) error {
	manifest, ok := catalog[done.Tool]
	if !ok || manifest.MutatesWorld != done.Mutates {
		return fmt.Errorf("%w: recovered capability mutation classification differs from frozen catalog", capabilityagent.ErrOutcomeUnknown)
	}
	return nil
}
