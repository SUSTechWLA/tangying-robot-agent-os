package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/gorilla/websocket"
)

type Server struct {
	service *orchestrator.Service
	mux     *http.ServeMux
}

func NewServer(service *orchestrator.Service) *Server {
	server := &Server{service: service, mux: http.NewServeMux()}
	server.routes()
	return server
}

func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	s.mux.HandleFunc("POST /v1/tasks", s.createTask)
	s.mux.HandleFunc("GET /v1/tasks/{id}", s.getTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/approve", s.approveTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/cancel", s.cancelTask)
	s.mux.HandleFunc("POST /v1/agents/{id}/claim", s.claimTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/events", s.appendEvent)
	s.mux.HandleFunc("GET /v1/tasks/{id}/events/ws", s.taskEventsWebSocket)
}

func (s *Server) taskEventsWebSocket(w http.ResponseWriter, r *http.Request) {
	upgrader := websocket.Upgrader{}
	connection, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer connection.Close()

	nextSequence := uint64(1)
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		task, err := s.service.Get(r.Context(), r.PathValue("id"))
		if err != nil {
			_ = connection.WriteJSON(map[string]string{"code": "TASK_NOT_FOUND"})
			return
		}
		for _, event := range task.Events {
			if event.Sequence < nextSequence {
				continue
			}
			if err := connection.WriteJSON(event); err != nil {
				return
			}
			nextSequence = event.Sequence + 1
		}
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
		}
	}
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

func (s *Server) getTask(w http.ResponseWriter, r *http.Request) {
	task, err := s.service.Get(r.Context(), r.PathValue("id"))
	if errors.Is(err, orchestrator.ErrTaskNotFound) {
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
	writeJSON(w, http.StatusOK, task)
}

func (s *Server) cancelTask(w http.ResponseWriter, r *http.Request) {
	if err := s.service.Transition(r.Context(), r.PathValue("id"), taskgraph.StateCancelled, "operator cancelled"); err != nil {
		writeError(w, http.StatusBadRequest, "CANCEL_FAILED", err.Error())
		return
	}
	task, _ := s.service.Get(r.Context(), r.PathValue("id"))
	writeJSON(w, http.StatusOK, task)
}

func (s *Server) claimTask(w http.ResponseWriter, r *http.Request) {
	claim, err := s.service.Claim(r.Context(), r.PathValue("id"), time.Minute)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "CLAIM_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, claim)
}

func (s *Server) appendEvent(w http.ResponseWriter, r *http.Request) {
	var event orchestrator.TaskEvent
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

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]string{"code": code, "message": message})
}
