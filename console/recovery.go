package console

import (
	"context"
	"errors"
	"net/http"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type recoveryExecutor interface {
	Pause(string) error
	Resume(string) error
	Recovery(context.Context, string) (tasks.LocalRecovery, error)
}

func (s *Server) localRecovery(w http.ResponseWriter, r *http.Request) {
	executor, ok := s.executor.(recoveryExecutor)
	if !ok {
		writeError(w, http.StatusNotImplemented, "RECOVERY_UNAVAILABLE", "当前执行器不支持安全恢复")
		return
	}
	view, err := executor.Recovery(r.Context(), r.PathValue("id"))
	if err != nil {
		writeRecoveryError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, view)
}

func (s *Server) pauseTask(w http.ResponseWriter, r *http.Request) {
	s.changeLocalRecovery(w, r, false)
}

func (s *Server) resumeTask(w http.ResponseWriter, r *http.Request) {
	s.changeLocalRecovery(w, r, true)
}

func (s *Server) changeLocalRecovery(w http.ResponseWriter, r *http.Request, resume bool) {
	executor, ok := s.executor.(recoveryExecutor)
	if !ok {
		writeError(w, http.StatusNotImplemented, "RECOVERY_UNAVAILABLE", "当前执行器不支持安全恢复")
		return
	}
	var err error
	if resume {
		err = executor.Resume(r.PathValue("id"))
	} else {
		err = executor.Pause(r.PathValue("id"))
	}
	if err != nil {
		writeRecoveryError(w, err)
		return
	}
	s.localRecovery(w, r)
}

func writeRecoveryError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, tasks.ErrTaskNotFound):
		writeError(w, http.StatusNotFound, "TASK_NOT_FOUND", "任务不存在")
	case errors.Is(err, agent.ErrPhysicalOutcomeUnknown):
		writeError(w, http.StatusConflict, "PHYSICAL_OUTCOME_UNKNOWN", "物理动作结果需要核对，不能重放或绕过")
	default:
		writeError(w, http.StatusConflict, "RECOVERY_REJECTED", err.Error())
	}
}
