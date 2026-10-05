package main

import (
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func recoveryGuardPlan(step, tool, planID, verdict string) tasks.TaskEvent {
	return tasks.TaskEvent{Type: agentcontract.TopicOpsRecoveryPlan, Payload: map[string]any{
		"planId": planID, "trigger": "ANOMALY_ACTION_FAILED@" + tool, "verdict": verdict,
		"escalateReason": "independent reconciliation unavailable",
		"trail": map[string]any{"steps": []any{
			map[string]any{"name": "recovery.start", "findings": map[string]any{"stepId": step}},
		}},
	}}
}

func recoveryGuardExecution(planID string) tasks.TaskEvent {
	return tasks.TaskEvent{Type: agentcontract.TopicOpsRecoveryExecuted, Payload: map[string]any{
		"planId": planID, "actionId": "execution.read-history", "operatorApproved": false,
		"executed": true, "verified": true,
	}}
}

func TestReadRecoveryRequiresItsOwnStepToolPlanAndAutomaticExecution(t *testing.T) {
	for _, tc := range []struct {
		name    string
		mutate  func(*tasks.TaskEvent, *tasks.TaskEvent)
		wantErr bool
	}{
		{"other step same tool", func(plan, _ *tasks.TaskEvent) {
			*plan = recoveryGuardPlan("earlier-step", "navigation.status", "plan-current", "PLAN")
		}, false},
		{"other tool same step", func(plan, _ *tasks.TaskEvent) {
			*plan = recoveryGuardPlan("current-step", "mapping.status", "plan-current", "PLAN")
		}, false},
		{"other plan execution", func(_, execution *tasks.TaskEvent) {
			execution.Payload["planId"] = "plan-foreign"
		}, false},
		{"manual execution", func(_, execution *tasks.TaskEvent) {
			execution.Payload["operatorApproved"] = true
		}, false},
		{"failed execution despite verified flags", func(_, execution *tasks.TaskEvent) {
			execution.Payload["failed"] = "verification persistence failed"
		}, true},
		{"different diagnostic action", func(_, execution *tasks.TaskEvent) {
			execution.Payload["actionId"] = "observe.re-read"
		}, false},
		{"matching escalation", func(plan, _ *tasks.TaskEvent) {
			plan.Payload["verdict"] = "ESCALATE"
		}, true},
		{"foreign step escalation", func(plan, _ *tasks.TaskEvent) {
			*plan = recoveryGuardPlan("earlier-step", "navigation.status", "plan-current", "ESCALATE")
		}, false},
		{"foreign tool escalation", func(plan, _ *tasks.TaskEvent) {
			*plan = recoveryGuardPlan("current-step", "mapping.status", "plan-current", "ESCALATE")
		}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			plan := recoveryGuardPlan("current-step", "navigation.status", "plan-current", "PLAN")
			execution := recoveryGuardExecution("plan-current")
			tc.mutate(&plan, &execution)
			events := []tasks.TaskEvent{
				{Type: "CAPABILITY_READ_RECOVERY_REQUESTED", StepID: "current-step"},
				plan, execution,
			}
			ready, err := readRecoveryCompleted(events, "current-step", "navigation.status")
			if ready || (err != nil) != tc.wantErr {
				t.Fatalf("invalid diagnostic released waiting task: ready=%t err=%v", ready, err)
			}
			if tc.name == "matching escalation" && !strings.Contains(err.Error(), "independent reconciliation unavailable") {
				t.Fatalf("escalation lost its actionable reason: %v", err)
			}
		})
	}
}

func TestForeignFailedDiagnosisCannotVetoLaterMatchingAutomaticDiagnosis(t *testing.T) {
	foreignFailure := recoveryGuardExecution("plan-foreign")
	foreignFailure.Payload["failed"] = "foreign diagnostic failed"
	events := []tasks.TaskEvent{
		{Type: "CAPABILITY_READ_RECOVERY_REQUESTED", StepID: "current-step"},
		recoveryGuardPlan("earlier-step", "navigation.status", "plan-foreign", "PLAN"),
		foreignFailure,
		recoveryGuardPlan("current-step", "navigation.status", "plan-current", "PLAN"),
		recoveryGuardExecution("plan-current"),
	}
	ready, err := readRecoveryCompleted(events, "current-step", "navigation.status")
	if err != nil || !ready {
		t.Fatalf("foreign-step failure blocked the current verified diagnosis: ready=%t err=%v", ready, err)
	}
}
