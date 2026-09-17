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

// A recovery plan must reach the operator attached to the problem it answers.
//
// A plan presented in a separate list makes the reader do the joining, and the
// investigation behind it is the part that makes the plan reviewable — so both
// are asserted here, field by field, rather than "something was attached".
func TestAnAlertCarriesItsRecoveryPlanAndInvestigation(t *testing.T) {
	planEvent := tasks.TaskEvent{
		Type: "ops.recovery_plan", OccurredAt: time.Unix(1100, 0).UTC(),
		Payload: map[string]any{
			"planId": "plan-trail-task-a-ANOMALY_COMPONENT_FAULT@execution", "verdict": "PLAN",
			"diagnosis": "地图未启用", "confidence": 0.9, "source": "deterministic",
			// The trigger is the finding's identity, not its code. A plan filed
			// under the bare code matches every component that failed the same
			// way, which is how one component's plan came to be displayed against
			// another component's finding.
			"trigger": "ANOMALY_COMPONENT_FAULT@execution", "stepCount": 2.0,
			"steps": []any{
				map[string]any{"order": 1.0, "action": "nav.read-map", "summary": "读取导航地图",
					"risk": "read_only", "requiresApproval": false, "why": "先确认现状"},
				map[string]any{"order": 2.0, "action": "map.activate", "summary": "加载已保存地图",
					"risk": "bounded_write", "requiresApproval": true, "why": "地图未启用"},
			},
			"trail": map[string]any{
				"trailId":   "trail-task-a-ANOMALY_COMPONENT_FAULT@execution",
				"trigger":   "ANOMALY_COMPONENT_FAULT@execution",
				"stepCount": 3.0,
				"steps": []any{
					map[string]any{"sequence": 1.0, "kind": "decision", "name": "catalog.read",
						"summary": "读取恢复动作目录", "rows": 16.0},
					map[string]any{"sequence": 2.0, "kind": "query", "name": "step_runs.read",
						"summary": "读取该任务的执行记录",
						"source":  "sqlite:step_runs (task_id = task-a)",
						"sourceDetail": map[string]any{
							"kind": "sqlite", "table": "step_runs", "query": "task_id = task-a"},
						"rows": 3.0},
					map[string]any{"sequence": 3.0, "kind": "query", "name": "tasks.read",
						"summary": "读取任务状态", "error": "database is locked"},
				},
			},
			"catalog": map[string]any{
				"actionCount": 16.0,
				"actions": []any{
					map[string]any{"id": "nav.read-map", "risk": "read_only"},
					map[string]any{"id": "estop.release", "risk": "never_automatic",
						"summary": "复位急停", "refusal": "复位急停要人确认现场安全"},
				},
			},
		},
	}

	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{
		alertTask("task-a", taskgraph.StateExecuting,
			anomalyEvent("critical", "ANOMALY_COMPONENT_FAULT", "地图未启用"), planEvent),
	}})

	var withPlan *tasks.AgentAlert
	for index := range alerts {
		if alerts[index].Recovery != nil {
			withPlan = &alerts[index]
		}
	}
	if withPlan == nil {
		t.Fatalf("no alert carried its recovery plan: %#v", alerts)
	}

	plan := withPlan.Recovery
	if plan.Verdict != "PLAN" || plan.Source != "deterministic" {
		t.Fatalf("plan = %#v", plan)
	}
	if len(plan.Steps) != 2 {
		t.Fatalf("steps = %#v, want 2", plan.Steps)
	}
	// The approval requirement travels with the step, so a reader knows what they
	// are being asked to approve without looking it up.
	if !plan.Steps[1].RequiresApproval {
		t.Fatalf("the write step is not marked as needing approval: %#v", plan.Steps[1])
	}
	if plan.Steps[0].RequiresApproval {
		t.Fatalf("the read-only step is marked as needing approval: %#v", plan.Steps[0])
	}

	// The investigation, including which table was read and what failed.
	investigation := withPlan.Investigation
	if investigation == nil {
		t.Fatal("the alert carries no investigation")
	}
	if investigation.StepCount != 3 || len(investigation.Steps) != 3 {
		t.Fatalf("investigation = %#v, want 3 steps", investigation)
	}
	var query *tasks.InvestigationStepView
	for index := range investigation.Steps {
		if investigation.Steps[index].Name == "step_runs.read" {
			query = &investigation.Steps[index]
		}
	}
	if query == nil {
		t.Fatalf("the store read is missing from the investigation: %#v", investigation.Steps)
	}
	// Both renderings: the readable string a console prints, and the parts an
	// operator asked "which database, which table" needs.
	if query.Source == "" {
		t.Fatal("the query step has no readable source")
	}
	if query.SourceDetail["kind"] != "sqlite" || query.SourceDetail["table"] != "step_runs" {
		t.Fatalf("sourceDetail = %#v, want sqlite/step_runs", query.SourceDetail)
	}
	if query.Rows != 3 {
		t.Fatalf("rows = %d, want 3", query.Rows)
	}

	// A failed step is shown, not hidden: an investigation that hid its failures
	// would look more certain than it was.
	failed := false
	for _, step := range investigation.Steps {
		if step.Error != "" {
			failed = true
		}
	}
	if !failed {
		t.Fatal("a failed investigation step was dropped")
	}

	// The boundary must be visible: the operator sees which actions are refused
	// and why, rather than inferring the limit from a missing step.
	if investigation.NeverAutomatic != 1 || len(plan.Refused) != 1 {
		t.Fatalf("refusals = %#v / neverAutomatic = %d, want the estop refusal shown",
			plan.Refused, investigation.NeverAutomatic)
	}
	if plan.Refused[0].Action != "estop.release" || plan.Refused[0].Reason == "" {
		t.Fatalf("refusal = %#v, want the action and its reason", plan.Refused[0])
	}
}

// An alert with no plan is unchanged: the addition must not invent a recovery
// section where none exists.
func TestAnAlertWithoutAPlanCarriesNoRecovery(t *testing.T) {
	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{
		alertTask("task-a", taskgraph.StateExecuting,
			anomalyEvent("critical", "ANOMALY_SAFETY_STOP", "急停")),
	}})
	if len(alerts) != 1 {
		t.Fatalf("alerts = %#v", alerts)
	}
	if alerts[0].Recovery != nil || alerts[0].Investigation != nil {
		t.Fatalf("a recovery section appeared without a plan: %#v", alerts[0])
	}
}

// A plan made about another task is not this alert's answer.
//
// The console showed plans whose ids named a task the alert was not about,
// because plans were gathered from every task's replay and matched by finding
// alone. A finding identity repeats across tasks — the same component fails the
// same way in all of them — so the match has to be scoped to the task as well.
func TestAPlanFromAnotherTaskIsNotAttached(t *testing.T) {
	planEvent := tasks.TaskEvent{
		Type: "ops.recovery_plan", OccurredAt: time.Unix(1100, 0).UTC(),
		Payload: map[string]any{
			"planId": "plan-trail-task-b-ANOMALY_COMPONENT_FAULT@execution", "verdict": "PLAN",
			"diagnosis": "另一个任务的诊断", "source": "deterministic",
			"trigger": "ANOMALY_COMPONENT_FAULT@execution",
		},
	}
	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{
		// The plan lives in task-b's replay; the alert belongs to task-a.
		alertTask("task-b", taskgraph.StateExecuting, planEvent),
		alertTask("task-a", taskgraph.StateExecuting,
			anomalyEvent("critical", "ANOMALY_COMPONENT_FAULT", "地图未启用")),
	}})

	for index := range alerts {
		if alerts[index].TaskID == "task-a" && alerts[index].Recovery != nil {
			t.Fatalf("an alert was given a plan from another task: %#v", alerts[index].Recovery)
		}
	}
}

// anomalyEventFor is anomalyEvent for a finding on a named component. The
// component is part of the finding's identity, so a test about two components
// failing the same way cannot use one that hardcodes it.
func anomalyEventFor(severity, code, message, component string, at time.Time) tasks.TaskEvent {
	return tasks.TaskEvent{
		Type: "ops.anomaly_detected", OccurredAt: at,
		Payload: map[string]any{
			"agent": "ops", "severity": severity, "code": code, "message": message,
			"component": component, "anomalyId": code + "@" + component,
		},
	}
}

// A standing condition is one row, however many times it has been reported.
//
// Once the observer's findings were actually persisted, the same component
// failure was reported on every evaluation and the banner listed it once per
// report — three rows for one problem in one task, and a badge that counted
// reports rather than problems. A warning list that repeats itself is a warning
// list that gets skimmed.
func TestAStandingConditionIsOneAlertPerTask(t *testing.T) {
	first := anomalyEventFor("critical", "ANOMALY_COMPONENT_FAULT", "地图未启用", "execution", time.Unix(1000, 0).UTC())
	second := anomalyEventFor("critical", "ANOMALY_COMPONENT_FAULT", "地图未启用", "execution", time.Unix(2000, 0).UTC())
	third := anomalyEventFor("critical", "ANOMALY_COMPONENT_FAULT", "地图未启用", "execution", time.Unix(3000, 0).UTC())

	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{
		alertTask("task-a", taskgraph.StateExecuting, first, second, third),
	}})

	if len(alerts) != 1 {
		t.Fatalf("alerts = %d, want one row for one standing condition: %#v", len(alerts), alerts)
	}
	// The newest report is the one kept: it is the evidence that describes the
	// world as last observed.
	if !alerts[0].DetectedAt.Equal(third.OccurredAt) {
		t.Fatalf("kept report at %s, want the newest %s", alerts[0].DetectedAt, third.OccurredAt)
	}
}

// The same failure in two tasks is two findings, and both are shown.
//
// Scoping the deduplication by task is the whole point of it: a robot with one
// broken component fails every task, and collapsing those into one row would hide
// how much work is affected.
func TestTheSameFailureInTwoTasksIsTwoAlerts(t *testing.T) {
	at := time.Unix(1000, 0).UTC()
	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{
		alertTask("task-a", taskgraph.StateExecuting,
			anomalyEventFor("critical", "ANOMALY_COMPONENT_FAULT", "地图未启用", "execution", at)),
		alertTask("task-b", taskgraph.StateExecuting,
			anomalyEventFor("critical", "ANOMALY_COMPONENT_FAULT", "地图未启用", "execution", at)),
	}})

	if len(alerts) != 2 {
		t.Fatalf("alerts = %d, want one per task: %#v", len(alerts), alerts)
	}
}

// Two different components failing in one task are two findings.
//
// This is the boundary of the rule above. Deduplicating by code rather than by
// identity would have hidden the second component, which is the same class of
// mistake as showing one component's plan against another component's alert.
func TestTwoComponentsFailingInOneTaskAreTwoAlerts(t *testing.T) {
	at := time.Unix(1000, 0).UTC()
	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: []*tasks.Task{
		alertTask("task-a", taskgraph.StateExecuting,
			anomalyEventFor("critical", "ANOMALY_ACTION_FAILED", "navigation.navigate 失败", "navigation.navigate", at),
			anomalyEventFor("warning", "ANOMALY_ACTION_FAILED", "observe_scene 失败", "observe_scene", at)),
	}})

	if len(alerts) != 2 {
		t.Fatalf("alerts = %d, want one per component: %#v", len(alerts), alerts)
	}
	ids := map[string]bool{}
	for _, alert := range alerts {
		ids[alert.ID] = true
	}
	for _, want := range []string{"ANOMALY_ACTION_FAILED@navigation.navigate", "ANOMALY_ACTION_FAILED@observe_scene"} {
		if !ids[want] {
			t.Fatalf("alerts = %#v, missing %s", ids, want)
		}
	}
}
