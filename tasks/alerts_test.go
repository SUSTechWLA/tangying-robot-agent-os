package tasks_test

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The console banner is driven by this projection, so these tests decide what an
// operator is interrupted by. Two failure modes matter and they pull in opposite
// directions: a finding that stays after the problem is fixed trains people to
// ignore the banner, and a finding that vanishes because a rule was too clever
// means nobody is told at all.

func alertTask(id string, state taskgraph.TaskState, events ...tasks.TaskEvent) *tasks.Task {
	return &tasks.Task{ID: id, State: state, Events: events}
}

func anomalyEvent(severity, code, message string) tasks.TaskEvent {
	return tasks.TaskEvent{
		Type: "ops.anomaly_detected", OccurredAt: time.Unix(1000, 0).UTC(),
		Payload: map[string]any{
			"agent": "ops", "severity": severity, "code": code, "message": message,
			"anomalyId":          code + "@execution",
			"recommendedActions": []any{"先观测确认", "再决定下一步"},
		},
	}
}

func TestAlertsSurfaceFindingsFromAnyTask(t *testing.T) {
	// The banner exists because the task rail only shows the task someone has
	// open. A finding must reach an operator without them navigating to it.
	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{
		alertTask("task-a", taskgraph.StateExecuting, anomalyEvent("critical", "ANOMALY_SAFETY_STOP", "急停")),
		alertTask("task-b", taskgraph.StateReady),
	}})
	if len(alerts) != 1 {
		t.Fatalf("alerts = %#v, want one", alerts)
	}
	if alerts[0].TaskID != "task-a" || !alerts[0].Active {
		t.Fatalf("alert = %#v", alerts[0])
	}
	if len(alerts[0].RecommendedActions) != 2 {
		t.Fatalf("advice was lost: %#v", alerts[0].RecommendedActions)
	}
}

// Execution events are not findings. Putting a tool activity in a warning banner
// is how a banner becomes something people close without reading.
func TestAlertsIgnoreExecutionAndLifecycleEvents(t *testing.T) {
	task := alertTask("task-a", taskgraph.StateExecuting,
		tasks.TaskEvent{Type: "TOOL_ACTIVITY", Payload: map[string]any{
			"agent": "task", "toolName": "manipulation.pick", "activityStatus": "SENDING",
		}},
		tasks.TaskEvent{Type: "agent.health_changed", Payload: map[string]any{
			"agent": "ops", "previous": "UNKNOWN", "current": "HEALTHY",
		}},
		tasks.TaskEvent{Type: "STATE_CHANGED", Payload: map[string]any{
			"previousState": "READY", "state": "EXECUTING",
		}},
	)
	if alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{task}}); len(alerts) != 0 {
		t.Fatalf("non-findings were surfaced as alerts: %#v", alerts)
	}
}

// A finding stops being an alert once its condition has cleared. This is what
// keeps the banner honest: there is no dismissal to forget and no banner that
// outlives the problem it describes.
func TestAnUnknownOutcomeAlertClearsWhenTheWorldIsReconciled(t *testing.T) {
	task := alertTask("task-a", taskgraph.StateRecoverableFailure,
		anomalyEvent("critical", "ANOMALY_UNVERIFIED_MUTATION", "有物理动作未确认"),
	)

	// Still unreconciled: the alert is live. The task state alone cannot say
	// this, which is why reconciliation is passed in.
	pending := tasks.ProjectAgentAlerts(tasks.AlertInput{
		Tasks: []*tasks.Task{task}, Reconciliation: map[string]bool{"task-a": true},
	})
	if len(pending) != 1 || !pending[0].Active {
		t.Fatalf("an unreconciled step did not raise an alert: %#v", pending)
	}

	// Reconciled: the condition the finding described no longer holds.
	cleared := tasks.ProjectAgentAlerts(tasks.AlertInput{
		Tasks: []*tasks.Task{task}, Reconciliation: map[string]bool{"task-a": false},
	})
	if len(cleared) != 1 {
		t.Fatalf("the alert disappeared instead of resolving: %#v", cleared)
	}
	if cleared[0].Active {
		t.Fatalf("a reconciled unknown outcome is still active: %#v", cleared[0])
	}
}

// Reconciliation state is the one thing that cannot be read off the task row.
// When it cannot be read, the alert stays active: claiming a physical unknown is
// resolved without evidence is the same mistake as claiming a task complete
// without evidence.
func TestAnUnknownOutcomeStaysActiveWhenReconciliationIsUnknown(t *testing.T) {
	task := alertTask("task-a", taskgraph.StateSucceeded,
		anomalyEvent("critical", "ANOMALY_UNVERIFIED_MUTATION", "有物理动作未确认"),
	)
	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{task}})
	if len(alerts) != 1 || !alerts[0].Active {
		t.Fatalf("an unresolvable unknown outcome was treated as resolved: %#v", alerts)
	}
}

// Other findings resolve when the task is recorded successful. That is
// conservative on purpose: a rule cannot tell that an operator-assist fault was
// physically fixed, so it keeps reporting until something does say so.
func TestOtherAlertsResolveOnlyOnARecordedSuccess(t *testing.T) {
	failed := alertTask("task-a", taskgraph.StateRecoverableFailure,
		anomalyEvent("warning", "ANOMALY_ACTION_FAILED", "失败"))
	if alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{failed}}); !alerts[0].Active {
		t.Fatalf("a failure on a non-successful task was reported resolved: %#v", alerts[0])
	}

	succeeded := alertTask("task-b", taskgraph.StateSucceeded,
		anomalyEvent("warning", "ANOMALY_ACTION_FAILED", "失败"))
	if alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{succeeded}}); alerts[0].Active {
		t.Fatalf("a finding on a successful task stayed active: %#v", alerts[0])
	}
}

// The banner leads with what still needs attention, then by severity.
func TestAlertsOrderActiveFirstThenBySeverity(t *testing.T) {
	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{
		alertTask("done", taskgraph.StateSucceeded,
			anomalyEvent("critical", "ANOMALY_SAFETY_STOP", "已解决")),
		alertTask("live-warning", taskgraph.StateExecuting,
			anomalyEvent("warning", "ANOMALY_STEP_LATENCY", "慢")),
		alertTask("live-critical", taskgraph.StateExecuting,
			anomalyEvent("critical", "ANOMALY_SAFETY_STOP", "急停")),
	}})
	if len(alerts) != 3 {
		t.Fatalf("alerts = %#v", alerts)
	}
	if !alerts[0].Active || alerts[0].Severity != "critical" {
		t.Fatalf("first alert = %#v, want the active critical one", alerts[0])
	}
	if alerts[1].Severity != "warning" {
		t.Fatalf("second alert = %#v, want the warning", alerts[1])
	}
	if alerts[2].Active {
		t.Fatalf("the resolved alert was not ordered last: %#v", alerts[2])
	}
}

// The identity is stable so a console can keep an acknowledgement across polls.
func TestAlertIdentityIsStableAcrossReads(t *testing.T) {
	task := alertTask("task-a", taskgraph.StateExecuting,
		anomalyEvent("critical", "ANOMALY_SAFETY_STOP", "急停"))
	first := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{task}})
	second := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{task}})
	if first[0].ID != second[0].ID || first[0].ID == "" {
		t.Fatalf("alert identity is not stable: %q vs %q", first[0].ID, second[0].ID)
	}
}

func TestAlertsCarryWhatAnOperatorActsOn(t *testing.T) {
	event := anomalyEvent("critical", "ANOMALY_UNVERIFIED_MUTATION", "有物理动作未确认")
	event.Payload["automaticRetryForbidden"] = true
	event.Payload["missingEvidence"] = []any{"步骤的执行记录没有证据编号"}
	event.Payload["evidence"] = []any{"task-a/pick"}
	event.StepID = "pick"
	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{
		Tasks: []*tasks.Task{alertTask("task-a", taskgraph.StateExecuting, event)},
	})
	if len(alerts) != 1 {
		t.Fatalf("alerts = %#v", alerts)
	}
	alert := alerts[0]
	if !alert.AutomaticRetryForbidden {
		t.Error("the retry prohibition was lost; it is the one thing that must never be softened")
	}
	if len(alert.MissingEvidence) == 0 {
		t.Error("the missing-evidence note was lost")
	}
	if len(alert.EvidenceIDs) == 0 {
		t.Error("the evidence references were lost, so the finding cannot be checked")
	}
	if alert.StepID != "pick" {
		t.Errorf("step id = %q; an operator needs to know which step", alert.StepID)
	}
}

func TestSupervisionOpaqueSaysNothingIsKnown(t *testing.T) {
	// A deployment that never started the agent runtime must not report itself as
	// healthy: "no alerts" and "nothing watching" look identical otherwise.
	status := tasks.SupervisionOpaque()
	if status.Enabled || status.Observing {
		t.Fatalf("an unstarted runtime reported supervision: %#v", status)
	}
	if status.Reason == "" {
		t.Fatal("an absent supervisor must explain itself")
	}
}
