package main

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestReadRecoveryWaitsForNewMatchingAutomaticVerifiedPlan(t *testing.T) {
	prefix := recoveryGuardPrefix("current-step", "navigation.status")
	plan := recoveryGuardPlan("current-step", "navigation.status", "p1", "PLAN")
	done := recoveryGuardExecution("p1")
	for _, tc := range []struct {
		name   string
		events []tasks.TaskEvent
		want   bool
	}{
		{"success", append(append([]tasks.TaskEvent{}, prefix...), plan, done), true},
		{"old plan", append([]tasks.TaskEvent{plan, done}, prefix...), false},
		{"missing plan", append(append([]tasks.TaskEvent{}, prefix...), done), false},
		{"pending", append(append([]tasks.TaskEvent{}, prefix...), plan), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := readRecoveryCompleted(tc.events, "task-recovery", "current-step", "navigation.status")
			if err != nil || got != tc.want {
				t.Fatalf("ready=%t err=%v", got, err)
			}
		})
	}
	for _, key := range []string{"verified", "executed"} {
		bad := done
		bad = recoveryGuardExecution("p1")
		bad.Payload[key] = false
		if ok, err := readRecoveryCompleted(append(append([]tasks.TaskEvent{}, prefix...), plan, bad), "task-recovery", "current-step", "navigation.status"); ok || err == nil {
			t.Fatal("unverified diagnostic allowed retry")
		}
	}
}
