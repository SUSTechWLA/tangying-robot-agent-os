package edgeagent

import (
	"context"
	"errors"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Finding is factual input to a recovery proposer. Neither component has a
// robot command port. Host retains the final authority and budget checks.
type Finding struct {
	ChildID   string `json:"childId"`
	StepID    string `json:"stepId"`
	Reason    string `json:"reason"`
	CanReplan bool   `json:"canReplan"`
	Unknown   bool   `json:"unknown"`
}
type RecoveryPolicy interface {
	Propose(context.Context, Finding) (string, error)
}
type BoundedRecovery struct{}

func (BoundedRecovery) Propose(_ context.Context, f Finding) (string, error) {
	if f.Unknown {
		return "RECONCILE", nil
	}
	if f.CanReplan {
		return "REPLAN_REMAINING", nil
	}
	return "STOP", nil
}

type Ops struct{}

func (Ops) Diagnose(task *tasks.Task, step string, cause error) Finding {
	f := Finding{StepID: step, Reason: cause.Error(), Unknown: failureUnknown(task, step)}
	if task != nil {
		f.ChildID = task.ID
	}
	f.CanReplan = safeToReplan(task, step, cause)
	if errors.Is(cause, capabilityagent.ErrOutcomeUnknown) {
		f.Unknown = true
		f.CanReplan = false
	}
	return f
}
