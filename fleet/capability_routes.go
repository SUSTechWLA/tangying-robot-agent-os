package fleet

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
)

func (s *Server) renewIntent(w http.ResponseWriter, r *http.Request) {
	if s.coordinator == nil {
		writeError(w, 503, "COORDINATOR_UNAVAILABLE", "coordinator unavailable")
		return
	}
	index, err := strconv.Atoi(r.PathValue("index"))
	if err != nil {
		writeError(w, 400, "INVALID_INDEX", err.Error())
		return
	}
	var input struct {
		RobotID          string `json:"robotId"`
		TaskRevision     uint64 `json:"taskRevision"`
		AggregateVersion uint64 `json:"aggregateVersion"`
		StepID           string `json:"stepId"`
		CommandID        string `json:"commandId"`
		FencingToken     uint64 `json:"fencingToken"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 8192))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		writeError(w, 400, "INVALID_RENEW", err.Error())
		return
	}
	robot, ok := deviceRobotIdentity(w, r, input.RobotID)
	if !ok {
		return
	}
	node := &coordinator.IntentNode{Index: index, TaskRevision: input.TaskRevision, AggregateVersion: input.AggregateVersion, StepID: input.StepID, CommandID: input.CommandID, FencingToken: input.FencingToken}
	if err := s.coordinator.RenewIntent(r.Context(), r.PathValue("id"), node, robot); err != nil {
		writeError(w, 409, "CLAIM_RENEW_FAILED", err.Error())
		return
	}
	writeJSON(w, 200, map[string]any{"renewed": true})
}
