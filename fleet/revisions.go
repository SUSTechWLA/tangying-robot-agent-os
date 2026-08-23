package fleet

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func (s *Server) proposeTaskRevision(w http.ResponseWriter, r *http.Request) {
	if s.coordinator == nil {
		writeError(w, http.StatusServiceUnavailable, "COORDINATOR_UNAVAILABLE", "task revision coordination is unavailable")
		return
	}
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
	if _, err := s.service.Get(r.Context(), r.PathValue("id")); err != nil {
		writeTaskRevisionError(w, err)
		return
	}
	record, err := s.coordinator.ProposeRevision(r.Context(), tasks.ProposeRevisionCommand{
		TaskID: r.PathValue("id"), ExpectedRevision: input.ExpectedRevision, Request: input.Request,
		IdempotencyKey: input.IdempotencyKey, Creator: operatorSubject(r),
	})
	if err != nil {
		writeTaskRevisionError(w, err)
		return
	}
	current, _ := s.service.Get(r.Context(), r.PathValue("id"))
	previewTask := *current
	previewTask.CurrentRevision = record.Revision.Revision
	writeJSON(w, http.StatusCreated, map[string]any{
		"schemaVersion": "task.revision-preview.v1", "taskId": current.ID,
		"currentRevision": current.CurrentRevision, "aggregateVersion": current.AggregateVersion,
		"proposal": record, "experience": tasks.ProjectExperience(tasks.ExperienceInput{Task: &previewTask, Revision: *record}),
	})
}

func (s *Server) confirmTaskRevision(w http.ResponseWriter, r *http.Request) {
	if s.coordinator == nil {
		writeError(w, http.StatusServiceUnavailable, "COORDINATOR_UNAVAILABLE", "task revision coordination is unavailable")
		return
	}
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
	record, err := s.coordinator.ConfirmRevision(r.Context(), r.PathValue("id"), revision,
		input.ExpectedCurrentRevision, input.IdempotencyKey)
	if err != nil {
		writeTaskRevisionError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"schemaVersion": "task.revision-confirmation.v1", "revision": record})
}

func (s *Server) listTaskRevisions(w http.ResponseWriter, r *http.Request) {
	task, err := s.service.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeTaskRevisionError(w, err)
		return
	}
	history, err := s.service.ListRevisions(r.Context(), task.ID)
	if err != nil {
		writeTaskRevisionError(w, err)
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
		writeTaskRevisionError(w, err)
		return
	}
	record, visibleRevision, err := revisionRecord(s.service, r, task)
	if err != nil {
		writeTaskRevisionError(w, err)
		return
	}
	var graph *coordinator.Snapshot
	if s.coordinator != nil {
		if snapshot, snapshotErr := s.coordinator.Snapshot(r.Context(), task.ID); snapshotErr == nil {
			graph = snapshot
			overlayCoordinatorSteps(&record, snapshot)
		}
	}
	var world *worldmodel.Snapshot
	if s.world != nil {
		if snapshot, snapshotErr := s.world.Snapshot(r.Context()); snapshotErr == nil {
			world = &snapshot
		}
	}
	activities := tasks.ToolActivitiesFromEvents(task.Events, s.registryDisplays(r))
	tasks.OverlayActivityStatuses(&record, activities)
	visibleTask := *task
	visibleTask.CurrentRevision = visibleRevision
	writeJSON(w, http.StatusOK, tasks.ProjectExperience(tasks.ExperienceInput{
		Task: &visibleTask, Revision: record, Activities: activities,
		Recovery: taskRecoveryGuidance(task, graph, world, activities),
	}))
}

func taskRecoveryGuidance(task *tasks.Task, graph *coordinator.Snapshot, world *worldmodel.Snapshot, activities []tasks.ToolActivityInput) *tasks.RecoveryGuidance {
	if task == nil {
		return nil
	}
	if guidance := tasks.RecoveryGuidanceFromEvents(task.Events); guidance != nil {
		return guidance
	}
	if graph != nil {
		for _, node := range graph.Intents {
			if node.Status == coordinator.StatusFailed {
				return &tasks.RecoveryGuidance{
					KnownState:       "系统保留了最后一次通过环境确认的任务进度",
					RobotSafetyState: "机器人已经停止推进出错的步骤，不会把失败当成完成",
					AutomaticAction:  "系统已拦截旧版本、重复命令和失效的控制凭证",
					UserActions:      []string{"查看机器人是否在线并清除现场障碍", "确认环境观测恢复后重新批准或更新任务"},
				}
			}
		}
	}
	if world != nil && (len(world.Health.DegradedSources) > 0 || len(world.Health.Conflicts) > 0) {
		return &tasks.RecoveryGuidance{
			KnownState:       "环境状态暂时不够新，已确认完成的步骤和证据仍然保留",
			RobotSafetyState: "机器人不会只凭动作返回就继续推进任务",
			AutomaticAction:  "系统正在等待新的环境观测，并保持相关步骤暂停",
			UserActions:      []string{"检查机器人网络、相机和状态观测", "环境恢复后等待系统自动重新确认"},
		}
	}
	return tasks.BasicRecoveryGuidance(task.RevisionState, activities)
}

func revisionRecord(service *tasks.Service, r *http.Request, task *tasks.Task) (tasks.RevisionRecord, uint64, error) {
	history, err := service.ListRevisions(r.Context(), task.ID)
	if err != nil {
		return tasks.RevisionRecord{}, 0, err
	}
	return tasks.SelectExperienceRevision(task, history)
}

func operatorSubject(r *http.Request) string {
	if principal, ok := auth.PrincipalFromContext(r.Context()); ok && principal.Role == "operator" {
		return principal.Subject
	}
	return "operator"
}

func writeTaskRevisionError(w http.ResponseWriter, err error) {
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

func overlayCoordinatorSteps(record *tasks.RevisionRecord, snapshot *coordinator.Snapshot) {
	byID := make(map[string]coordinator.IntentNode, len(snapshot.Intents))
	for _, node := range snapshot.Intents {
		byID[node.StepID] = node
	}
	for index := range record.Revision.Steps {
		node, ok := byID[record.Revision.Steps[index].StepID]
		if !ok {
			continue
		}
		switch node.Status {
		case coordinator.StatusReady:
			record.Revision.Steps[index].Status = tasks.StepReady
		case coordinator.StatusRunning:
			record.Revision.Steps[index].Status = tasks.StepRunning
		case coordinator.StatusSucceeded:
			record.Revision.Steps[index].Status = tasks.StepSatisfied
			record.Revision.Steps[index].HarnessEvidenceIDs = append([]string(nil), node.HarnessEvidence...)
		case coordinator.StatusFailed:
			record.Revision.Steps[index].Status = tasks.StepFailed
		case coordinator.StatusCancelled:
			record.Revision.Steps[index].Status = tasks.StepCancelledByRevision
		default:
			record.Revision.Steps[index].Status = tasks.StepPending
		}
	}
}

func (s *Server) registryDisplays(r *http.Request) map[string]tasks.ToolDisplay {
	displays := map[string]tasks.ToolDisplay{}
	if s.registry == nil {
		return displays
	}
	devices, err := s.registry.List(r.Context())
	if err != nil {
		return displays
	}
	for _, device := range devices {
		for _, tool := range device.ToolCatalog {
			displays[device.RobotID+"\x00"+tool.Name] = tasks.ToolDisplay{
				DisplayName: tool.DisplayName, Purpose: tool.Purpose,
				SafeArguments: append([]string(nil), tool.SafeArgumentNames...),
			}
		}
	}
	return displays
}
