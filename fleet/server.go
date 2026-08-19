// Package fleet exposes the distributed cloud control-plane API. It is
// transport- and storage-agnostic: task persistence is provided by
// tasks.Repository, the ready-task queue by middleware.Queue[string].
package fleet

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type Server struct {
	service *tasks.Service
	queue   middleware.Queue[string]
	mux     *http.ServeMux
}

func NewServer(service *tasks.Service, queue middleware.Queue[string]) *Server {
	server := &Server{service: service, queue: queue, mux: http.NewServeMux()}
	server.routes()
	return server
}

func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("POST /v1/tasks", s.createTask)
	s.mux.HandleFunc("GET /v1/tasks", s.listTasks)
	s.mux.HandleFunc("GET /v1/tasks/{id}", s.getTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/approve", s.approveTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/cancel", s.cancelTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/state", s.setTaskState)
	s.mux.HandleFunc("POST /v1/tasks/{id}/events", s.appendEvent)
	s.mux.HandleFunc("GET /v1/orchestration/metrics", s.metrics)
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "mode": "fleet"})
}

func (s *Server) createTask(w http.ResponseWriter, r *http.Request) {
	var input struct {
		Request string `json:"request"`
		Adapter string `json:"adapter"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil || strings.TrimSpace(input.Request) == "" {
		writeError(w, http.StatusBadRequest, "INVALID_REQUEST", "request is required")
		return
	}
	task, err := s.service.Create(r.Context(), input.Request, input.Adapter)
	if errors.Is(err, intent.ErrUnsupportedIntent) {
		writeError(w, http.StatusUnprocessableEntity, "UNSUPPORTED_INTENT", err.Error())
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "CREATE_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, task)
}

func (s *Server) listTasks(w http.ResponseWriter, r *http.Request) {
	list, err := s.service.List(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "LIST_FAILED", err.Error())
		return
	}
	if list == nil {
		list = []*tasks.Task{}
	}
	writeJSON(w, http.StatusOK, list)
}

func (s *Server) getTask(w http.ResponseWriter, r *http.Request) {
	task, err := s.service.Get(r.Context(), r.PathValue("id"))
	if errors.Is(err, tasks.ErrTaskNotFound) {
		writeError(w, http.StatusNotFound, "TASK_NOT_FOUND", err.Error())
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "READ_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, task)
}

func (s *Server) approveTask(w http.ResponseWriter, r *http.Request) {
	task, err := s.service.Approve(r.Context(), r.PathValue("id"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "APPROVAL_FAILED", err.Error())
		return
	}
	if s.queue != nil {
		if err := s.queue.Enqueue(context.Background(), task.ID); err != nil {
			writeError(w, http.StatusConflict, "ENQUEUE_FAILED", err.Error())
			return
		}
	}
	writeJSON(w, http.StatusOK, task)
}

func (s *Server) cancelTask(w http.ResponseWriter, r *http.Request) {
	if err := s.service.Transition(r.Context(), r.PathValue("id"), taskgraph.StateCancelled, "cloud operator cancelled"); err != nil {
		writeError(w, http.StatusBadRequest, "CANCEL_FAILED", err.Error())
		return
	}
	task, _ := s.service.Get(r.Context(), r.PathValue("id"))
	writeJSON(w, http.StatusOK, task)
}

func (s *Server) setTaskState(w http.ResponseWriter, r *http.Request) {
	var input struct {
		State  taskgraph.TaskState `json:"state"`
		Reason string              `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil || input.State == "" {
		writeError(w, http.StatusBadRequest, "INVALID_STATE", "state is required")
		return
	}
	if err := s.service.Transition(r.Context(), r.PathValue("id"), input.State, input.Reason); err != nil {
		writeError(w, http.StatusBadRequest, "TRANSITION_REJECTED", err.Error())
		return
	}
	task, _ := s.service.Get(r.Context(), r.PathValue("id"))
	writeJSON(w, http.StatusOK, task)
}

func (s *Server) appendEvent(w http.ResponseWriter, r *http.Request) {
	var event tasks.TaskEvent
	if err := json.NewDecoder(r.Body).Decode(&event); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_EVENT", err.Error())
		return
	}
	task, err := s.service.AppendEvent(r.Context(), r.PathValue("id"), event)
	if err != nil {
		writeError(w, http.StatusBadRequest, "EVENT_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, task)
}

func (s *Server) metrics(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.service.OrchestrationMetrics(r.Context()))
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]string{"code": code, "message": message})
}
