package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	operatorweb "github.com/SUSTechWLA/tangying-robot-agent-os/web"
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
	s.mux.HandleFunc("GET /v1/orchestration/metrics", s.orchestrationMetrics)
	s.mux.HandleFunc("POST /v1/telemetry", s.publishTelemetry)
	s.mux.HandleFunc("GET /v1/telemetry", s.getTelemetry)
	s.mux.HandleFunc("POST /v1/tasks/{id}/approve", s.approveTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/cancel", s.cancelTask)
	s.mux.HandleFunc("POST /v1/agents/{id}/claim", s.claimTask)
	s.mux.HandleFunc("POST /v1/leases/{id}/renew", s.renewLease)
	s.mux.HandleFunc("POST /v1/tasks/{id}/events", s.appendEvent)
	s.mux.HandleFunc("GET /v1/tasks/{id}/events/ws", s.taskEventsWebSocket)
	s.mux.HandleFunc("POST /v1/tasks/{id}/state", s.setTaskState)
	s.mux.Handle("GET /", operatorweb.Handler())
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

func (s *Server) orchestrationMetrics(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.service.OrchestrationMetrics(r.Context()))
}

func (s *Server) publishTelemetry(w http.ResponseWriter, r *http.Request) {
	var snapshot telemetry.Snapshot
	if err := json.NewDecoder(r.Body).Decode(&snapshot); err != nil || snapshot.Adapter == "" {
		writeError(w, http.StatusBadRequest, "INVALID_TELEMETRY", "adapter is required")
		return
	}
	s.service.PublishTelemetry(r.Context(), snapshot)
	writeJSON(w, http.StatusCreated, map[string]string{"stored": "true"})
}

func (s *Server) getTelemetry(w http.ResponseWriter, r *http.Request) {
	adapter := r.URL.Query().Get("adapter")
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	latest, hasLatest := s.service.TelemetryLatest(adapter)
	response := map[string]any{
		"adapter":   adapter,
		"adapters":  s.service.TelemetryAdapters(),
		"history":   s.service.TelemetryHistory(adapter, limit),
		"hasLatest": hasLatest,
	}
	if hasLatest {
		response["latest"] = latest
	}
	writeJSON(w, http.StatusOK, response)
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

func (s *Server) renewLease(w http.ResponseWriter, r *http.Request) {
	var input struct {
		AgentID string `json:"agentId"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil || input.AgentID == "" {
		writeError(w, http.StatusBadRequest, "INVALID_LEASE_RENEWAL", "agentId is required")
		return
	}
	expires, err := s.service.RenewLease(r.Context(), r.PathValue("id"), input.AgentID, time.Minute)
	if errors.Is(err, orchestrator.ErrLeaseNotFound) {
		writeError(w, http.StatusConflict, "LEASE_NOT_FOUND", err.Error())
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "LEASE_RENEWAL_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"leaseId":          r.PathValue("id"),
		"leaseExpiresAt":   expires,
		"leaseExpiresUnix": expires.UnixMilli(),
	})
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
