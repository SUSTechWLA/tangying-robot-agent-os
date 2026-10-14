package console

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func (s *Server) createObjective(w http.ResponseWriter, r *http.Request) {
	if !s.allowOperatorWrite(w, r) {
		return
	}
	var input struct {
		Objective      objective.Contract `json:"objective"`
		Adapter        string             `json:"adapter"`
		IdempotencyKey string             `json:"idempotencyKey"`
	}
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 256<<10))
	d.DisallowUnknownFields()
	if err := d.Decode(&input); err != nil {
		writeError(w, 400, "INVALID_OBJECTIVE", err.Error())
		return
	}
	if err := d.Decode(new(any)); err != io.EOF {
		writeError(w, 400, "INVALID_OBJECTIVE", "one JSON document required")
		return
	}
	if s.robotServices == nil {
		writeError(w, 503, "RUNTIME_UNAVAILABLE", "objective requires Runtime service catalog")
		return
	}
	robot, entries, err := capabilityagent.Catalogue(r.Context(), s.robotServices)
	if err == nil {
		err = input.Objective.ValidateCatalog(robot, entries)
	}
	if err != nil {
		writeError(w, 422, "OBJECTIVE_CONTRACT_REJECTED", err.Error())
		return
	}
	task, err := s.service.CreateObjectiveOnce(r.Context(), input.Objective, input.Adapter, input.IdempotencyKey, "local:"+robot)
	if err != nil {
		if errors.Is(err, tasks.ErrIdempotencyConflict) {
			writeError(w, 409, "OBJECTIVE_CONFLICT", err.Error())
			return
		}
		writeError(w, 422, "OBJECTIVE_CREATE_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, task)
}
