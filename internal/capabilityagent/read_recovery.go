package capabilityagent

import (
	"context"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// One recovery per durable step, including across process restarts. Only a
// transport failure of a catalogue-declared read qualifies. This never retries
// a write, a rejected argument, a verification failure, or an unknown outcome.
func (e *Executor) callRecoverableRead(ctx context.Context, task, step, robot, tool, identity string, args map[string]any, mutates bool) (map[string]any, error) {
	if mutates {
		return e.call(ctx, robot, tool, identity, args)
	}
	// Check before dispatch, including when observers are disabled on restart.
	// An interrupted recovery must not silently gain another initial attempt.
	snapshot, readErr := e.Tasks.Get(ctx, task)
	if readErr != nil {
		return nil, readErr
	}
	for _, event := range snapshot.Events {
		if event.StepID == step && event.Type == "CAPABILITY_READ_RECOVERY_REQUESTED" {
			return nil, fmt.Errorf("read recovery budget already consumed; interrupted diagnostics require operator review")
		}
	}
	result, err := e.call(ctx, robot, tool, identity, args)
	if err == nil || e.RecoverRead == nil || ctx.Err() != nil {
		return result, err
	}
	if code := status.Code(err); code != codes.Unavailable && code != codes.DeadlineExceeded {
		return nil, err
	}
	record := middleware.StepRecord{TaskID: task, StepID: step, IdempotencyKey: identity, Capability: tool, SafetyLevel: "read_only"}
	if markErr := e.Store.MarkStepFailed(ctx, record); markErr != nil {
		return nil, markErr
	}
	if recordErr := e.record(ctx, task, step, "CAPABILITY_READ_RECOVERY_REQUESTED", map[string]any{"tool": tool, "commandId": identity, "maxRetries": 1}); recordErr != nil {
		return nil, recordErr
	}
	if recordErr := e.recordFailure(ctx, task, step, tool, identity, "dispatch", false, err); recordErr != nil {
		return nil, recordErr
	}
	recoveryCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	if recoverErr := e.RecoverRead(recoveryCtx, task, step, tool, err); recoverErr != nil {
		return nil, fmt.Errorf("read recovery stopped: %v (original: %w)", recoverErr, err)
	}
	// Re-check the robot and entire approved catalogue after the investigation.
	currentRobot, entries, checkErr := Catalogue(recoveryCtx, e.Provider)
	if checkErr != nil {
		return nil, checkErr
	}
	if currentRobot != robot || snapshot.Plan == nil || snapshot.Plan.Capabilities == nil || capability.Fingerprint(entries) != snapshot.Plan.Capabilities.CatalogRevision || entries[tool].MutatesWorld {
		return nil, fmt.Errorf("read recovery refused: approved capability binding changed")
	}
	if markErr := e.Store.MarkStepStarted(ctx, record); markErr != nil {
		return nil, markErr
	}
	if recordErr := e.record(ctx, task, step, "CAPABILITY_READ_RETRY", map[string]any{"tool": tool, "commandId": identity, "attempt": 2, "basis": "OPS_DIAGNOSIS_AND_VERIFIED_READ_ONLY_RECOVERY"}); recordErr != nil {
		return nil, recordErr
	}
	return e.call(ctx, robot, tool, identity, args)
}
