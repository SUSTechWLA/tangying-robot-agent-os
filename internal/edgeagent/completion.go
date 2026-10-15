package edgeagent

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/structpb"
)

// This is an objective-wide durable budget, including all completion checks,
// generations and host restarts. It cannot authorize a writer or replan.
const completionRetryLimit = 2

func (h *Host) completion(ctx context.Context, task *tasks.Task, c objective.Contract, provider capabilityagent.Provider, cp *checkpoint, save func(string) error) ([]map[string]any, bool, error) {
	if cp.CompletionBlocked {
		return nil, false, fmt.Errorf("%w: completion read recovery budget exhausted", capabilityagent.ErrNeedsInput)
	}
	// An interrupted read may have spent its reserved attempt. Stopping here
	// avoids granting an extra retry when its acknowledgement failed to commit.
	if cp.CompletionReadPending {
		return nil, false, fmt.Errorf("%w: interrupted completion read requires review", capabilityagent.ErrNeedsInput)
	}
	proofs := make([]map[string]any, 0, len(c.Completion))
	satisfied := true
	for index, check := range c.Completion {
		params, err := structpb.NewStruct(check.Arguments)
		if err != nil {
			return nil, false, err
		}
		for {
			cp.CompletionReadPending = true
			if err := save("EDGE_COMPLETION_READ_RESERVED"); err != nil {
				return nil, false, err
			}
			response, err := provider.CallService(ctx, &robotv1.ServiceRequest{RobotId: c.RobotID, Name: check.Tool, Parameters: params})
			if err == nil && (response == nil || !response.Ok || response.Result == nil) {
				err = fmt.Errorf("objective completion read failed")
			}
			if err == nil {
				cp.CompletionReadPending = false
				if err := save("EDGE_COMPLETION_READ_CONFIRMED"); err != nil {
					return nil, false, err
				}
				value := objective.Value(response.Result.AsMap(), check.Path)
				matched := objective.Equal(value, check.Equals)
				satisfied = satisfied && matched
				proofs = append(proofs, map[string]any{"check": check, "value": value, "satisfied": matched})
				break
			}
			if ctx.Err() != nil {
				return nil, false, ctx.Err()
			}
			cp.CompletionReadPending = false
			if errors.Is(err, capabilityagent.ErrNeedsInput) {
				if saveErr := save("EDGE_COMPLETION_READ_FAILED"); saveErr != nil {
					return nil, false, errors.Join(err, saveErr)
				}
				return nil, false, err
			}
			if code := status.Code(err); code != codes.Unavailable && code != codes.DeadlineExceeded {
				if saveErr := save("EDGE_COMPLETION_READ_FAILED"); saveErr != nil {
					return nil, false, errors.Join(err, saveErr)
				}
				return nil, false, err
			}
			if cp.CompletionRetries >= completionRetryLimit || cp.Reads >= c.Budget.MaxReads {
				cp.CompletionBlocked = true
				if saveErr := save("EDGE_COMPLETION_RECOVERY_EXHAUSTED"); saveErr != nil {
					return nil, false, saveErr
				}
				return nil, false, fmt.Errorf("%w: completion read recovery budget exhausted: %v", capabilityagent.ErrNeedsInput, err)
			}
			cp.CompletionRetries++
			if saveErr := save("EDGE_COMPLETION_RETRY_RESERVED"); saveErr != nil {
				return nil, false, saveErr
			}
			findingID := fmt.Sprintf("%s/ops/completion/%d", task.ID, cp.CompletionRetries)
			message := err.Error()
			if len(message) > 1024 {
				message = message[:1024]
			}
			if _, err := h.Tasks.AppendEvent(ctx, task.ID, tasks.TaskEvent{Type: "EDGE_OPS_FINDING", Payload: map[string]any{"role": "ops", "phase": "completion", "findingId": findingID, "checkIndex": index, "tool": check.Tool, "reason": message, "effectsKnownAbsent": true, "objectiveDigest": cp.ObjectiveDigest}}); err != nil {
				return nil, false, err
			}
			if _, err := h.Tasks.AppendEvent(ctx, task.ID, tasks.TaskEvent{Type: "EDGE_RECOVERY_DECISION", Payload: map[string]any{"role": "recovery", "phase": "completion", "diagnosedBy": "ops", "causationId": findingID, "decision": "RETRY_COMPLETION_READ", "checkIndex": index, "tool": check.Tool, "attempt": cp.CompletionRetries, "objectiveDigest": cp.ObjectiveDigest}}); err != nil {
				return nil, false, err
			}
			timer := time.NewTimer(100 * time.Millisecond)
			select {
			case <-ctx.Done():
				timer.Stop()
				return nil, false, ctx.Err()
			case <-timer.C:
			}
		}
	}
	return proofs, satisfied, nil
}
