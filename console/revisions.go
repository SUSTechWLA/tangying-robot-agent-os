package console

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func (s *Server) proposeTaskRevision(w http.ResponseWriter, r *http.Request) {
	var input struct {
		ExpectedRevision uint64 `json:"expectedRevision"`
		Request          string `json:"request"`
		IdempotencyKey   string `json:"idempotencyKey"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil || input.ExpectedRevision == 0 ||
		strings.TrimSpace(input.Request) == "" || !tasks.ValidIdempotencyKey(input.IdempotencyKey) {
		writeError(w, http.StatusBadRequest, "INVALID_REVISION_REQUEST", "expectedRevision, request, and UUID idempotencyKey are required")
		return
	}
	task, err := s.service.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeLocalRevisionError(w, err)
		return
	}
	command := tasks.ProposeRevisionCommand{
		TaskID: task.ID, ExpectedRevision: input.ExpectedRevision, Request: input.Request,
		IdempotencyKey: input.IdempotencyKey, Creator: "local-owner",
	}
	var record *tasks.RevisionRecord
	if executor, ok := s.executor.(interface {
		ProposeRevision(context.Context, tasks.ProposeRevisionCommand) (*tasks.RevisionRecord, error)
	}); ok {
		record, err = executor.ProposeRevision(r.Context(), command)
	} else {
		record, err = s.service.ProposeRevision(r.Context(), command, localRevisionBasis(task))
	}
	if err != nil {
		writeLocalRevisionError(w, err)
		return
	}
	current, _ := s.service.Get(r.Context(), task.ID)
	previewTask := *current
	previewTask.CurrentRevision = record.Revision.Revision
	writeJSON(w, http.StatusCreated, map[string]any{
		"schemaVersion": "task.revision-preview.v1", "taskId": current.ID,
		"currentRevision": current.CurrentRevision, "aggregateVersion": current.AggregateVersion,
		"proposal": record, "experience": tasks.ProjectExperience(tasks.ExperienceInput{Task: &previewTask, Revision: *record}),
	})
}

func (s *Server) confirmTaskRevision(w http.ResponseWriter, r *http.Request) {
	revision, err := strconv.ParseUint(r.PathValue("revision"), 10, 64)
	if err != nil || revision == 0 {
		writeError(w, http.StatusBadRequest, "INVALID_REVISION", "revision must be a positive integer")
		return
	}
	var input struct {
		ExpectedCurrentRevision uint64 `json:"expectedCurrentRevision"`
		IdempotencyKey          string `json:"idempotencyKey"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil || input.ExpectedCurrentRevision == 0 ||
		!tasks.ValidIdempotencyKey(input.IdempotencyKey) {
		writeError(w, http.StatusBadRequest, "INVALID_REVISION_CONFIRMATION", "expectedCurrentRevision and UUID idempotencyKey are required")
		return
	}
	task, err := s.service.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeLocalRevisionError(w, err)
		return
	}
	var record *tasks.RevisionRecord
	if executor, ok := s.executor.(interface {
		ConfirmRevision(context.Context, string, uint64, uint64, string) (*tasks.RevisionRecord, error)
	}); ok {
		record, err = executor.ConfirmRevision(r.Context(), task.ID, revision, input.ExpectedCurrentRevision, input.IdempotencyKey)
	} else {
		wait := localTaskExecuting(task.State)
		record, err = s.service.ConfirmRevision(r.Context(), tasks.ConfirmRevisionCommand{
			TaskID: task.ID, Revision: revision, ExpectedCurrentRevision: input.ExpectedCurrentRevision,
			IdempotencyKey: input.IdempotencyKey, Actor: "local-owner", WaitForSafePoint: wait,
		})
	}
	if err != nil {
		writeLocalRevisionError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"schemaVersion": "task.revision-confirmation.v1", "revision": record})
}

func (s *Server) listTaskRevisions(w http.ResponseWriter, r *http.Request) {
	task, err := s.service.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeLocalRevisionError(w, err)
		return
	}
	history, err := s.service.ListRevisions(r.Context(), task.ID)
	if err != nil {
		writeLocalRevisionError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"schemaVersion": "task.revisions.v1", "taskId": task.ID, "currentRevision": task.CurrentRevision,
		"aggregateVersion": task.AggregateVersion, "revisions": history,
	})
}

func (s *Server) taskExperience(w http.ResponseWriter, r *http.Request) {
	task, err := s.service.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeLocalRevisionError(w, err)
		return
	}
	history, err := s.service.ListRevisions(r.Context(), task.ID)
	if err != nil {
		writeLocalRevisionError(w, err)
		return
	}
	var record tasks.RevisionRecord
	for _, candidate := range history {
		if candidate.Revision.Revision == task.CurrentRevision {
			record = candidate
			break
		}
	}
	if record.Revision.Revision == 0 {
		writeLocalRevisionError(w, tasks.ErrRevisionNotFound)
		return
	}
	displays := s.localRuntimeDisplays(r)
	activities := tasks.ToolActivitiesFromEvents(task.Events, displays)
	tasks.OverlayActivityStatuses(&record, activities)
	if task.State == taskgraph.StateSucceeded {
		for index := range record.Revision.Steps {
			record.Revision.Steps[index].Status = tasks.StepSatisfied
		}
	}
	writeJSON(w, http.StatusOK, tasks.ProjectExperience(tasks.ExperienceInput{
		Task: task, Revision: record, Activities: activities,
	}))
}

func localRevisionBasis(task *tasks.Task) tasks.RevisionBasis {
	activities := tasks.ToolActivitiesFromEvents(task.Events, nil)
	basis := tasks.RevisionBasis{EvidenceValidity: map[string]bool{}}
	latest := map[string]tasks.ToolActivityInput{}
	for _, activity := range activities {
		latest[activity.StepID] = activity
	}
	for stepID, activity := range latest {
		switch activity.Status {
		case "SENDING", "RUNNING", "AWAITING_EVIDENCE":
			basis.RunningStepIDs = append(basis.RunningStepIDs, stepID)
		case "CONFIRMED":
			basis.EvidenceValidity[stepID] = len(activity.EvidenceIDs) > 0
		}
	}
	return basis
}

func localTaskExecuting(state taskgraph.TaskState) bool {
	switch state {
	case taskgraph.StateObserving, taskgraph.StatePlanning, taskgraph.StateExecuting, taskgraph.StateVerifying:
		return true
	default:
		return false
	}
}

func (s *Server) localRuntimeDisplays(r *http.Request) map[string]tasks.ToolDisplay {
	displays := map[string]tasks.ToolDisplay{}
	if s.runtime == nil {
		return displays
	}
	snapshot, err := s.runtime.Info(r.Context())
	if err != nil {
		return displays
	}
	for _, capability := range snapshot.Capabilities {
		display := tasks.ToolDisplay{DisplayName: capability.DisplayName, Purpose: capability.Purpose,
			SafeArguments: append([]string(nil), capability.SafeArgumentNames...)}
		displays[snapshot.RobotID+"\x00"+capability.Name] = display
		displays["\x00"+capability.Name] = display
	}
	return displays
}

func writeLocalRevisionError(w http.ResponseWriter, err error) {
	var conflict *tasks.RevisionConflictError
	switch {
	case errors.Is(err, tasks.ErrTaskNotFound), errors.Is(err, tasks.ErrRevisionNotFound):
		writeError(w, http.StatusNotFound, "TASK_NOT_FOUND", err.Error())
	case errors.As(err, &conflict):
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(map[string]any{"code": "REVISION_CONFLICT", "message": err.Error(), "current": conflict.Current})
	case errors.Is(err, tasks.ErrRevisionConflict):
		writeError(w, http.StatusConflict, "REVISION_CONFLICT", err.Error())
	case errors.Is(err, tasks.ErrIdempotencyConflict):
		writeError(w, http.StatusConflict, "IDEMPOTENCY_CONFLICT", err.Error())
	default:
		writeError(w, http.StatusBadRequest, "REVISION_FAILED", err.Error())
	}
}
