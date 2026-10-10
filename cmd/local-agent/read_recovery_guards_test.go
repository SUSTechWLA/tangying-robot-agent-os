package main

import (
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func recoveryGuardBinding(step, planID string) agentcontract.RecoveryBinding {
	return agentcontract.RecoveryBinding{TaskID: "task-recovery", TaskRevision: 2, RobotID: "robot-a", StepID: step, CommandID: "cmd-" + step, SourceEventID: "failure-" + step, AnomalyEventID: "anomaly-" + step, PlanEventID: planID + "-event"}
}

func recoveryGuardPrefix(step, tool string) []tasks.TaskEvent {
	b := recoveryGuardBinding(step, "")
	request := tasks.TaskEvent{Type: "CAPABILITY_READ_RECOVERY_REQUESTED", StepID: step, Payload: b.Encode()}
	request.Payload["tool"] = tool
	failed := tasks.TaskEvent{Type: "TOOL_ACTIVITY", StepID: step, Payload: b.Encode()}
	failed.Payload["eventId"], failed.Payload["activityStatus"], failed.Payload["toolName"], failed.Payload["mutatesWorld"] = b.SourceEventID, "FAILED", tool, false
	anomaly := tasks.TaskEvent{Type: "ops.anomaly_detected", StepID: step, Payload: map[string]any{
		"protocolVersion": agentcontract.EventProtocolV1, "eventId": b.AnomalyEventID, "agent": "ops", "causationId": b.SourceEventID, "binding": b.Encode(),
	}}
	return []tasks.TaskEvent{request, failed, anomaly}
}

func recoveryGuardPlan(step, tool, planID, verdict string) tasks.TaskEvent {
	b := recoveryGuardBinding(step, planID)
	return tasks.TaskEvent{Type: agentcontract.TopicOpsRecoveryPlan, Payload: map[string]any{
		"protocolVersion": agentcontract.EventProtocolV1, "eventId": b.PlanEventID, "agent": "recovery", "causationId": b.AnomalyEventID, "binding": b.Encode(),
		"planId": planID, "trigger": "ANOMALY_ACTION_FAILED@" + tool, "verdict": verdict,
		"escalateReason": "independent reconciliation unavailable",
		"trail": map[string]any{"steps": []any{
			map[string]any{"name": "recovery.start", "findings": map[string]any{"stepId": step}},
		}},
	}}
}

func recoveryGuardExecution(planID string) tasks.TaskEvent {
	b := recoveryGuardBinding("current-step", planID)
	return tasks.TaskEvent{Type: agentcontract.TopicOpsRecoveryExecuted, Payload: map[string]any{
		"protocolVersion": agentcontract.EventProtocolV1, "eventId": planID + "-done", "agent": "recovery-executor", "causationId": b.PlanEventID, "binding": b.Encode(),
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
			events := append(recoveryGuardPrefix("current-step", "navigation.status"), plan, execution)
			ready, err := readRecoveryCompleted(events, "task-recovery", "current-step", "navigation.status")
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
	events := append(recoveryGuardPrefix("current-step", "navigation.status"),
		recoveryGuardPlan("earlier-step", "navigation.status", "plan-foreign", "PLAN"),
		foreignFailure,
		recoveryGuardPlan("current-step", "navigation.status", "plan-current", "PLAN"),
		recoveryGuardExecution("plan-current"),
	)
	ready, err := readRecoveryCompleted(events, "task-recovery", "current-step", "navigation.status")
	if err != nil || !ready {
		t.Fatalf("foreign-step failure blocked the current verified diagnosis: ready=%t err=%v", ready, err)
	}
}
