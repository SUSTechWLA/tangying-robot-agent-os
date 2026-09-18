package console

import (
	"net/http"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// The operator alert channel.
//
// Findings already reach the durable ledger, and the task rail already renders
// them — but only for the task someone has open. An operator running a robot
// needs to be told about a fault whether or not they happen to be looking at the
// right task, so this endpoint projects every task's current findings into one
// list.
//
// It is a read-only projection over existing data: no new store, no new event
// type, nothing to keep in sync. The alternative — accumulating alerts as they
// arrive — would need a dismissal protocol and would drift from the ledger; see
// tasks.ProjectAgentAlerts for why the alerts are derived from current state
// instead.

// runnerAlertReporter is what the console needs to show findings that belong to
// no task. They are the ones an operator most needs: an emergency stop is not a
// task problem, and it matters whether or not a task is running.
type runnerAlertReporter interface {
	RunnerAlerts() []agentruntime.RunnerAlert
}

// supervisionReporter is what the console needs to say whether anything is
// watching. It is optional: a deployment that never started the agent runtime
// simply has no supervision to report, and the console must say so rather than
// imply everything is fine.
type supervisionReporter interface {
	SupervisionStatus() tasks.SupervisionStatus
}

// agentAlerts answers "what is wrong right now, and is anything watching".
func (s *Server) agentAlerts(w http.ResponseWriter, r *http.Request) {
	all, err := s.service.List(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "ALERTS_UNAVAILABLE", err.Error())
		return
	}

	// Reconciliation state is the one condition that cannot be read off a task
	// row: it lives in the execution records. Where the executor can answer, ask
	// it; where it cannot, the unknown-outcome alerts stay active rather than
	// being guessed as resolved. Erring toward "still a problem" is the safe
	// direction for a warning banner.
	reconciling := map[string]bool{}
	if executor, ok := s.executor.(recoveryExecutor); ok {
		for _, task := range all {
			if task == nil {
				continue
			}
			if view, recoveryErr := executor.Recovery(r.Context(), task.ID); recoveryErr == nil {
				reconciling[task.ID] = view.RequiresReconciliation
			}
		}
	}

	alerts := tasks.ProjectAgentAlerts(tasks.AlertInput{Tasks: all, Reconciliation: reconciling})

	// Robot-level findings are merged in as alerts with no task id. They are kept
	// in the agent runtime rather than the ledger, because a condition that has
	// cleared should disappear and the ledger is a record, not a live view.
	runnerAlerts := []runtimeAlertView{}
	if reporter, ok := s.executor.(runnerAlertReporter); ok {
		// Each robot-level finding is shown with the plan made for it. A finding
		// without its proposal makes the reader look in two places for one thing.
		plans, _ := s.executor.(runnerAlertPlanReporter)
		for _, alert := range reporter.RunnerAlerts() {
			view := runtimeAlertView{RunnerAlert: alert}
			if plans != nil {
				// Looked up by identity rather than by code: the identity is what
				// the plan was filed under and what this alert is, so the two
				// cannot drift apart again.
				if plan, ok := plans.RunnerAlertPlan(alert.ID); ok {
					view.Recovery = recoveryViewFromStored(plan)
					view.Investigation = investigationViewFromTrail(plan.Investigation)
				}
			}
			runnerAlerts = append(runnerAlerts, view)
		}
	}

	status := tasks.SupervisionStatus{}
	if reporter, ok := s.executor.(supervisionReporter); ok {
		status = reporter.SupervisionStatus()
	}
	active := 0
	for _, alert := range alerts {
		if alert.Active {
			active++
		}
	}
	for _, alert := range runnerAlerts {
		if alert.Active {
			active++
		}
	}
	// The grouped view travels with the flat one. The flat list is what a drill-down
	// reads; the groups are what a person reviews. Computing both here rather than
	// in the browser keeps one definition of "the same problem" — a client-side
	// grouping would be a second one, and the two would disagree about identity the
	// first time an id format changed.
	combined := make([]tasks.AgentAlert, 0, len(alerts)+len(runnerAlerts))
	combined = append(combined, alerts...)
	for _, view := range runnerAlerts {
		combined = append(combined, agentAlertFromRunnerAlert(view))
	}
	groups := tasks.GroupAgentAlerts(combined)
	activeGroups := 0
	for _, group := range groups {
		if group.Active {
			activeGroups++
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"alerts":       alerts,
		"runnerAlerts": runnerAlerts,
		"groups":       groups,
		// ActiveCount is sent alongside the list so the console can render a
		// badge without walking the array, and so a test can assert on the
		// number that a person would see.
		"activeCount": active,
		// ActiveGroupCount is the number the badge should show once grouping is on:
		// problems, not reports. Both are sent because the difference between them
		// is the finding, and hiding it would make the console look like it had
		// simply lost data.
		"activeGroupCount": activeGroups,
		"groupCount":       len(groups),
		"supervision":      status,
	})
}

// agentAlertFromRunnerAlert renders a robot-level finding in the shape grouping
// reads.
//
// The two alert types exist for a real reason — one is per task and carries an
// investigation, the other is robot-level and has no task to belong to — but a
// review surface that showed them in two lists would make an operator count twice.
// The conversion is here, in the console, rather than in tasks: it is a rendering
// decision, and putting it in the projection would make the ledger's own type
// depend on how a page groups things.
func agentAlertFromRunnerAlert(view runtimeAlertView) tasks.AgentAlert {
	converted := tasks.AgentAlert{
		ID: view.ID, Code: view.Code, Severity: view.Severity, Message: view.Message,
		DetectedAt: view.DetectedAt, Active: view.Active,
		RecommendedActions:      view.RecommendedActions,
		AutomaticRetryForbidden: view.AutomaticRetryForbidden,
		MissingEvidence:         view.MissingEvidence,
		EvidenceIDs:             view.EvidenceIDs,
		Recovery:                view.Recovery,
		Investigation:           view.Investigation,
		Stage:                   tasks.StageDetected,
		RobotWide:               true,
	}
	if converted.ID == "" {
		converted.ID = view.Code + "@" + view.Component
	}
	return converted
}

// runnerAlertPlanReporter is what the console needs to show a robot-level finding
// with the recovery plan made for it.
type runnerAlertPlanReporter interface {
	RunnerAlertPlan(trigger string) (agentruntime.RunnerAlertPlan, bool)
}

// runtimeAlertView is a robot-level finding plus the plan proposed for it.
//
// The stored plan and the projected task plan are different shapes because they
// come from different stores, so they are converted here into the one shape the
// console renders. A console that had to handle two shapes would eventually
// render one of them wrong.
type runtimeAlertView struct {
	// Alert is embedded so every existing field keeps its JSON shape: adding the
	// recovery section must not move what a console already reads.
	agentruntime.RunnerAlert
	Recovery      *tasks.RecoveryView      `json:"recovery,omitempty"`
	Investigation *tasks.InvestigationView `json:"investigation,omitempty"`
}

func recoveryViewFromStored(plan agentruntime.RunnerAlertPlan) *tasks.RecoveryView {
	view := &tasks.RecoveryView{
		PlanID: plan.PlanID, Verdict: plan.Verdict, Diagnosis: plan.Diagnosis,
		Confidence: plan.Confidence, Source: plan.Source, EscalateReason: plan.EscalateReason,
	}
	for _, step := range plan.Steps {
		view.Steps = append(view.Steps, tasks.RecoveryStepView{
			Order: step.Order, Action: step.Action, Summary: step.Summary,
			Risk: step.Risk, RequiresApproval: step.RequiresApproval, Why: step.Why,
		})
	}
	for _, refused := range plan.Refused {
		view.Refused = append(view.Refused, tasks.RefusalView{
			Action: refused.Action, Summary: refused.Summary, Reason: refused.Reason,
		})
	}
	return view
}

func investigationViewFromTrail(trail *agentruntime.Trail) *tasks.InvestigationView {
	if trail == nil {
		return nil
	}
	view := &tasks.InvestigationView{
		TrailID: trail.ID, Trigger: trail.Trigger, StepCount: len(trail.Steps),
	}
	for _, step := range trail.Steps {
		entry := tasks.InvestigationStepView{
			Sequence: step.Sequence, Kind: string(step.Kind), Name: step.Name,
			Summary: step.Summary, Source: step.Source.String(),
			Rows: step.Rows, Error: step.Error, At: step.At,
		}
		if step.Source != (agentruntime.DataSource{}) {
			entry.SourceDetail = map[string]any{
				"kind": step.Source.Kind, "name": step.Source.Name,
				"table": step.Source.Table, "query": step.Source.Query,
			}
		}
		if len(step.Findings) > 0 {
			entry.Findings = step.Findings
		}
		view.Steps = append(view.Steps, entry)
	}
	return view
}
