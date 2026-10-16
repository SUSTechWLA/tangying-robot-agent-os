package fleet

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/objective"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/capabilityagent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"google.golang.org/protobuf/encoding/protojson"
)

func (s *Server) goalCatalog(w http.ResponseWriter, r *http.Request) {
	if s.registry == nil {
		writeError(w, 503, "REGISTRY_UNAVAILABLE", "registry unavailable")
		return
	}
	d, found, err := s.registry.Status(r.Context(), r.PathValue("id"))
	if err != nil || !found || !d.Online {
		writeError(w, 409, "ROBOT_UNAVAILABLE", "registered online robot required")
		return
	}
	var catalog robotv1.ServiceCatalog
	if err = protojson.Unmarshal(d.ServiceCatalog, &catalog); err != nil {
		writeError(w, 409, "INVALID_CATALOG", err.Error())
		return
	}
	robot, entries, err := capabilityagent.Catalogue(r.Context(), advertisedProvider{&catalog})
	if err != nil {
		writeError(w, 409, "INVALID_CATALOG", err.Error())
		return
	}
	writeJSON(w, 200, map[string]any{"robotId": robot, "catalogRevision": capability.Fingerprint(entries), "capabilities": entries, "objectiveSchemaVersion": objective.Version})
}

func (s *Server) createObjective(w http.ResponseWriter, r *http.Request) {
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
	if s.registry == nil {
		writeError(w, 503, "REGISTRY_UNAVAILABLE", "objective requires registered robot")
		return
	}
	device, found, err := s.registry.Status(r.Context(), input.Objective.RobotID)
	if err != nil || !found || !device.Online {
		writeError(w, 409, "ROBOT_UNAVAILABLE", "registered online robot required")
		return
	}
	if input.Adapter != "" && input.Adapter != "auto" && device.Adapter != "" && input.Adapter != device.Adapter {
		writeError(w, 409, "ADAPTER_MISMATCH", "objective adapter differs from registered robot")
		return
	}
	var catalog robotv1.ServiceCatalog
	if err = protojson.Unmarshal(device.ServiceCatalog, &catalog); err != nil {
		writeError(w, 409, "INVALID_CATALOG", err.Error())
		return
	}
	robot, entries, err := capabilityagent.Catalogue(r.Context(), advertisedProvider{&catalog})
	if err == nil {
		err = input.Objective.ValidateCatalog(robot, entries)
	}
	if err != nil {
		writeError(w, 422, "OBJECTIVE_CONTRACT_REJECTED", err.Error())
		return
	}
	task, err := s.service.CreateObjectiveOnce(r.Context(), input.Objective, input.Adapter, input.IdempotencyKey, "fleet:"+operatorSubject(r))
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
