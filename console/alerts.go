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
	runnerAlerts := []agentruntime.RunnerAlert{}
	if reporter, ok := s.executor.(runnerAlertReporter); ok {
		runnerAlerts = reporter.RunnerAlerts()
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
	writeJSON(w, http.StatusOK, map[string]any{
		"alerts":       alerts,
		"runnerAlerts": runnerAlerts,
		// ActiveCount is sent alongside the list so the console can render a
		// badge without walking the array, and so a test can assert on the
		// number that a person would see.
		"activeCount": active,
		"supervision": status,
	})
}
