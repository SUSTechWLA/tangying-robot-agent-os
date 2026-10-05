package main

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestReadRecoveryWaitsForNewMatchingAutomaticVerifiedPlan(t *testing.T) {
	request := tasks.TaskEvent{Type: "CAPABILITY_READ_RECOVERY_REQUESTED", StepID: "s1"}
	plan := tasks.TaskEvent{Type: "ops.recovery_plan", Payload: map[string]any{"planId": "p1", "trigger": "ANOMALY_ACTION_FAILED@navigation.status", "verdict": "PLAN", "trail": map[string]any{"steps": []any{map[string]any{"name": "recovery.start", "findings": map[string]any{"stepId": "s1"}}}}}}
	done := tasks.TaskEvent{Type: "ops.recovery_executed", Payload: map[string]any{"planId": "p1", "actionId": "execution.read-history", "executed": true, "verified": true}}
	for _, tc := range []struct {
		name   string
		events []tasks.TaskEvent
		want   bool
	}{
		{"success", []tasks.TaskEvent{request, plan, done}, true},
		{"old plan", []tasks.TaskEvent{plan, done, request}, false},
		{"missing plan", []tasks.TaskEvent{request, done}, false},
		{"pending", []tasks.TaskEvent{request, plan}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := readRecoveryCompleted(tc.events, "s1", "navigation.status")
			if err != nil || got != tc.want {
				t.Fatalf("ready=%t err=%v", got, err)
			}
		})
	}
	for _, key := range []string{"verified", "executed"} {
		bad := done
		bad.Payload = map[string]any{"planId": "p1", "actionId": "execution.read-history", "executed": true, "verified": true}
		bad.Payload[key] = false
		if ok, err := readRecoveryCompleted([]tasks.TaskEvent{request, plan, bad}, "s1", "navigation.status"); ok || err == nil {
			t.Fatal("unverified diagnostic allowed retry")
		}
	}
}
