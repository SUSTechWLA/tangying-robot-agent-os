package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/policy"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestEveryPolicyPreparationFailureHasTypedNoDispatchDiagnostic(t *testing.T) {
	for _, tc := range []struct {
		name, code                      string
		cause                           error
		parameters                      map[string]any
		invalidInput, failAudit, cancel bool
	}{
		{name: "provider timeout", cause: policy.ErrProviderTimeout, code: "POLICY_PROVIDER_TIMEOUT"},
		{name: "manifest drift", cause: policy.ErrManifestDrift, code: "POLICY_COMPATIBILITY_OR_ACTION_REJECTED"},
		{name: "stale observation", cause: policy.ErrObservationStale, code: "POLICY_OBSERVATION_NOT_READY"},
		{name: "invalid semantic change", parameters: map[string]any{"target_ref": "unapproved"}, code: "POLICY_COMPATIBILITY_OR_ACTION_REJECTED"},
		{name: "missing provenance", parameters: map[string]any{"action_chunk": []any{1}}, code: "POLICY_COMPATIBILITY_OR_ACTION_REJECTED"},
		{name: "unserializable arguments", invalidInput: true, code: "POLICY_COMPATIBILITY_OR_ACTION_REJECTED"},
		{name: "audit write failed", failAudit: true, parameters: map[string]any{"action_chunk": []any{1}, "policy_execution": map[string]any{"inferenceId": "i-1"}}, code: "POLICY_AUDIT_UNAVAILABLE"},
		{name: "cancelled", cancel: true, cause: context.Canceled, code: "CANCELLED"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if tc.cancel {
				cancel()
			}
			var diagnostics []tasks.TaskEvent
			r := &Runner{ActionPolicy: func(context.Context, runtime.Command, runtime.Snapshot) (map[string]any, error) {
				return tc.parameters, tc.cause
			}}
			r.TaskEvents = func(ctx context.Context, taskID string, event tasks.TaskEvent) error {
				if event.Type == "POLICY_PREPARED" && tc.failAudit {
					return errors.New("disk write refused")
				}
				if ctx.Err() != nil {
					t.Fatal("failure persistence inherited cancellation")
				}
				if taskID != "task-policy" {
					t.Fatalf("task scope = %q", taskID)
				}
				diagnostics = append(diagnostics, event)
				return nil
			}
			command := runtime.Command{CommandID: "command-7", StepID: "rev-2-pick", TaskRevision: 2,
				Capability: runtime.CapabilityPick, Deadline: time.Now().Add(time.Minute)}
			if tc.invalidInput {
				command.Parameters = map[string]any{"invalid": make(chan int)}
			}
			_, err := r.prepareActionPolicy(ctx, &tasks.Task{ID: "task-policy"}, command, runtime.Snapshot{})
			if err == nil {
				t.Fatal("preparation unexpectedly succeeded")
			}
			if tc.cause != nil && !errors.Is(err, tc.cause) {
				t.Fatalf("lost cause: %v", err)
			}
			if len(diagnostics) != 1 {
				t.Fatalf("diagnostics = %#v", diagnostics)
			}
			event := diagnostics[0]
			if event.Type != "POLICY_PREPARATION_FAILED" || event.StepID != command.StepID || event.Payload["code"] != tc.code {
				t.Fatalf("diagnostic = %#v", event)
			}
			for key, expected := range map[string]any{"phase": "pre_dispatch", "physicalDispatched": false,
				"mutatesWorld": false, "outcomeUnknown": false, "rejected": true,
				"toolName": "policy.prepare", "tool": "manipulation.pick", "commandId": command.CommandID} {
				if event.Payload[key] != expected {
					t.Fatalf("%s=%v, expected %v", key, event.Payload[key], expected)
				}
			}
		})
	}
}

func TestPolicyDiagnosticPersistenceFailurePreservesBothCauses(t *testing.T) {
	diskFailure := errors.New("diagnostic storage unavailable")
	r := &Runner{TaskEvents: func(context.Context, string, tasks.TaskEvent) error { return diskFailure }}
	err := r.recordPolicyPreparationFailure(context.Background(), &tasks.Task{ID: "task-1"}, runtime.Command{}, policy.ErrProviderTimeout)
	if !errors.Is(err, policy.ErrProviderTimeout) || !errors.Is(err, diskFailure) || !errors.Is(err, errPolicyAuditUnavailable) {
		t.Fatalf("diagnostic failure hid a cause: %v", err)
	}
}

func TestPolicyDiagnosticOmitsRawProviderDetailsButPreservesReturnedCause(t *testing.T) {
	const privateDetail = "provider response contains private telemetry and endpoint credentials"
	cause := fmt.Errorf("%w: %s", policy.ErrProviderUnavailable, privateDetail)
	var recorded tasks.TaskEvent
	r := &Runner{TaskEvents: func(_ context.Context, _ string, event tasks.TaskEvent) error {
		recorded = event
		return nil
	}}
	err := r.recordPolicyPreparationFailure(context.Background(), &tasks.Task{ID: "task-1"}, runtime.Command{}, cause)
	if !errors.Is(err, cause) || !strings.Contains(err.Error(), privateDetail) {
		t.Fatalf("returned error lost the original debugging chain: %v", err)
	}
	wire, marshalErr := json.Marshal(recorded)
	if marshalErr != nil {
		t.Fatal(marshalErr)
	}
	if strings.Contains(string(wire), privateDetail) || recorded.Payload["code"] != "POLICY_PROVIDER_UNAVAILABLE" ||
		recorded.Payload["error"] != recorded.Payload["code"] || recorded.Message != "Action policy preparation was rejected before physical dispatch" {
		t.Fatalf("diagnostic leaked provider details or lost its stable classification: %s", wire)
	}
}
