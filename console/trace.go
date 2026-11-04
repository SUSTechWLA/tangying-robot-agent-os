package console

import (
	"errors"
	"net/http"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func (s *Server) taskTrace(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "private, no-store")
	trace, err := s.service.Trace(r.Context(), r.PathValue("id"), s.evidence)
	if err != nil {
		switch {
		case errors.Is(err, tasks.ErrTaskNotFound):
			writeError(w, http.StatusNotFound, "TASK_NOT_FOUND", "task not found")
		case errors.Is(err, tasks.ErrTraceBusy):
			w.Header().Set("Retry-After", "1")
			writeError(w, http.StatusServiceUnavailable, "TRACE_SNAPSHOT_BUSY", "task changed during diagnostic capture; retry")
		default:
			writeError(w, http.StatusInternalServerError, "TRACE_READ_FAILED", "durable diagnostic history could not be read")
		}
		return
	}
	writeJSON(w, http.StatusOK, trace)
}
