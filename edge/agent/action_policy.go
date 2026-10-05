package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/recovery"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var errPolicyAuditUnavailable = errors.New("policy preparation audit is unavailable")

func (r *Runner) prepareActionPolicy(ctx context.Context, task *tasks.Task, command runtime.Command, snapshot runtime.Snapshot) (_ runtime.Command, resultErr error) {
	if r.ActionPolicy == nil {
		return command, nil
	}
	defer func() {
		if resultErr != nil {
			resultErr = r.recordPolicyPreparationFailure(ctx, task, command, resultErr)
		}
	}()
	// The hook receives its own arguments: inference cannot mutate the frozen
	// semantic command through a shared nested map.
	wire, err := json.Marshal(command.Parameters)
	if err != nil {
		return runtime.Command{}, fmt.Errorf("%w: %v", policy.ErrInferenceInvalid, err)
	}
	input := command
	input.Parameters = nil
	if err := json.Unmarshal(wire, &input.Parameters); err != nil {
		return runtime.Command{}, fmt.Errorf("%w: %v", policy.ErrInferenceInvalid, err)
	}
	prepareCtx, cancel := context.WithDeadline(ctx, command.Deadline)
	defer cancel()
	parameters, err := r.ActionPolicy(prepareCtx, input, snapshot)
	if err == nil {
		err = prepareCtx.Err()
	}
	if err != nil {
		return runtime.Command{}, err
	}
	if len(parameters) == 0 {
		return command, nil
	}
	if command.Capability != runtime.CapabilityPick && command.Capability != runtime.CapabilityPlace {
		return runtime.Command{}, fmt.Errorf("%w: action policy cannot augment %s", policy.ErrActionUnsafe, command.Capability)
	}
	for key := range parameters {
		if key != "action_chunk" && key != "policy_execution" {
			return runtime.Command{}, fmt.Errorf("%w: action policy cannot change semantic argument %s", policy.ErrActionUnsafe, key)
		}
	}
	prepared := command
	prepared.Parameters = make(map[string]any, len(command.Parameters)+len(parameters))
	for key, value := range command.Parameters {
		prepared.Parameters[key] = value
	}
	for key, value := range parameters {
		prepared.Parameters[key] = value
	}
	if r.TaskEvents == nil {
		return runtime.Command{}, errPolicyAuditUnavailable
	}
	_, hasChunk := parameters["action_chunk"]
	if !hasChunk || parameters["policy_execution"] == nil {
		return runtime.Command{}, fmt.Errorf("%w: policy action chunk and provenance are required together", policy.ErrInferenceInvalid)
	}
	if err := r.TaskEvents(ctx, task.ID, tasks.TaskEvent{Type: "POLICY_PREPARED", StepID: command.StepID,
		Payload: map[string]any{"commandId": command.CommandID, "tool": string(command.Capability), "policy": parameters["policy_execution"], "physicalDispatched": false}}); err != nil {
		return runtime.Command{}, errors.Join(errPolicyAuditUnavailable, err)
	}
	return prepared, nil
}

// Only call before durable STARTED or any invocation. This diagnostic concerns
// read/inference preparation; it must never describe a physical dispatch.
func (r *Runner) recordPolicyPreparationFailure(ctx context.Context, task *tasks.Task, command runtime.Command, cause error) error {
	if cause == nil {
		return nil
	}
	if r.TaskEvents == nil {
		return errors.Join(cause, errPolicyAuditUnavailable)
	}
	code := recovery.Classify(cause).TechnicalCode
	switch {
	case errors.Is(cause, context.Canceled):
		code = "CANCELLED"
	case errors.Is(cause, context.DeadlineExceeded):
		code = "COMMAND_EXPIRED"
	case errors.Is(cause, errPolicyAuditUnavailable):
		code = "POLICY_AUDIT_UNAVAILABLE"
	case errors.Is(cause, policy.ErrInferenceInvalid), errors.Is(cause, policy.ErrManifestInvalid), errors.Is(cause, policy.ErrResponseTooLarge):
		code = "POLICY_COMPATIBILITY_OR_ACTION_REJECTED"
	case errors.Is(cause, policy.ErrObservationInvalid):
		code = "POLICY_OBSERVATION_NOT_READY"
	case code == "UNCLASSIFIED_EXECUTION_FAILURE" || code == "EXECUTION_OUTCOME_UNKNOWN":
		code = "POLICY_PREPARATION_FAILED"
	}
	persistCtx, done := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer done()
	err := r.TaskEvents(persistCtx, task.ID, tasks.TaskEvent{Type: "POLICY_PREPARATION_FAILED", StepID: command.StepID,
		Message: "Action policy preparation was rejected before physical dispatch", Payload: map[string]any{
			"commandId": command.CommandID, "stepId": command.StepID, "taskRevision": command.TaskRevision,
			"tool": string(command.Capability), "toolName": "policy.prepare", "code": code, "error": code,
			"activityStatus": "FAILED", "phase": "pre_dispatch", "physicalDispatched": false,
			"mutatesWorld": false, "outcomeUnknown": false, "rejected": true,
		}})
	if err != nil {
		return errors.Join(cause, fmt.Errorf("%w: %w", errPolicyAuditUnavailable, err))
	}
	return cause
}

// Check again after every read/inference/audit write and before STARTED. A
// prepared trajectory does not extend either the sensor or command deadline.
func (r *Runner) validatePreparedPolicy(command runtime.Command) error {
	if !command.Deadline.IsZero() && !r.now().Before(command.Deadline) {
		return context.DeadlineExceeded
	}
	if _, ok := command.Parameters["action_chunk"]; !ok {
		return nil
	}
	metadata, ok := command.Parameters["policy_execution"].(map[string]any)
	if !ok {
		return fmt.Errorf("%w: prepared action lacks policy provenance", policy.ErrInferenceInvalid)
	}
	value, _ := metadata["validUntil"].(string)
	expires, err := time.Parse(time.RFC3339Nano, value)
	if err != nil || !r.now().Before(expires) {
		return fmt.Errorf("%w: prepared policy observation expired or has no validity deadline", policy.ErrObservationStale)
	}
	return nil
}
