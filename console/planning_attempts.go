package console

import (
	"context"
	"errors"
	"net/http"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
)

type PlanningAttemptReader interface {
	PlanningAttempt(context.Context, string) (capability.PlanningAttempt, error)
}

func WithPlanningAttempts(reader PlanningAttemptReader) Option {
	return func(s *Server) { s.planningAttempts = reader }
}

func (s *Server) getPlanningAttempt(w http.ResponseWriter, r *http.Request) {
	if !sameOriginRequest(r) {
		writeError(w, http.StatusForbidden, "CONSOLE_CROSS_SITE_READ", "planning evidence requires this console's session")
		return
	}
	if !s.session.matches(r) {
		writeError(w, http.StatusUnauthorized, RefusalNoSession, "planning evidence requires this console's session")
		return
	}
	if s.planningAttempts == nil {
		writeError(w, http.StatusServiceUnavailable, "PLANNING_AUDIT_UNAVAILABLE", "planning attempt storage is unavailable")
		return
	}
	attempt, err := s.planningAttempts.PlanningAttempt(r.Context(), r.PathValue("id"))
	if errors.Is(err, capability.ErrPlanningAttemptNotFound) {
		writeError(w, http.StatusNotFound, "PLANNING_ATTEMPT_NOT_FOUND", "planning attempt not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "PLANNING_AUDIT_READ_FAILED", "planning evidence could not be verified")
		return
	}
	w.Header().Set("Cache-Control", "private, no-store")
	writeJSON(w, http.StatusOK, attempt)
}

func writePlanningError(w http.ResponseWriter, status int, code string, err error) {
	body := map[string]any{"code": code, "message": err.Error()}
	var failure *capability.FailedPlanningError
	if errors.As(err, &failure) {
		if failure.PlanningAttemptID != "" && !failure.AuditUnavailable {
			body["planningAttemptId"] = failure.PlanningAttemptID
		} else {
			body["auditUnavailable"] = true
		}
	}
	writeJSON(w, status, body)
}
