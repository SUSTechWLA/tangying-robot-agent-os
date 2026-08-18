// Package console exposes the loopback-only Local Agent API and embedded web
// application. Distributed control-plane mutation routes do not belong here.
package console

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	operatorweb "github.com/SUSTechWLA/tangying-robot-agent-os/web"
	"github.com/gorilla/websocket"
)

type Executor interface {
	Enqueue(taskID string) error
	Cancel(taskID string) error
}

type ConfigStatus struct {
	Provider        string `json:"provider"`
	BaseURL         string `json:"baseUrl"`
	Model           string `json:"model"`
	HasAPIKey       bool   `json:"hasApiKey"`
	RestartRequired bool   `json:"restartRequired"`
}

type LLMConfig struct {
	Provider string `json:"provider"`
	BaseURL  string `json:"baseUrl"`
	Model    string `json:"model"`
	APIKey   string `json:"apiKey"`
}

type Settings interface {
	Status() ConfigStatus
	UpdateLLM(LLMConfig) error
}

type RuntimeProvider interface {
	Info(context.Context) (runtime.Snapshot, error)
}

type Option func(*Server)

func WithSettings(settings Settings) Option {
	return func(server *Server) { server.settings = settings }
}

func WithRuntime(provider RuntimeProvider) Option {
	return func(server *Server) { server.runtime = provider }
}

type Server struct {
	service  *tasks.Service
	executor Executor
	settings Settings
	runtime  RuntimeProvider
	mux      *http.ServeMux
}

func NewServer(service *tasks.Service, executor Executor, options ...Option) *Server {
	server := &Server{service: service, executor: executor, mux: http.NewServeMux()}
	for _, option := range options {
		option(server)
	}
	server.routes()
	return server
}

func (s *Server) Handler() http.Handler { return withConsoleSecurityHeaders(s.mux) }

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "mode": "local"})
	})
	s.mux.HandleFunc("GET /v1/config/status", s.configStatus)
	s.mux.HandleFunc("PUT /v1/config/llm", s.updateLLM)
	s.mux.HandleFunc("GET /v1/runtime", s.runtimeStatus)
	s.mux.HandleFunc("POST /v1/tasks", s.createTask)
	s.mux.HandleFunc("GET /v1/tasks", s.listTasks)
	s.mux.HandleFunc("GET /v1/tasks/{id}", s.getTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/approve", s.approveTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/cancel", s.cancelTask)
	s.mux.HandleFunc("GET /v1/tasks/{id}/events/ws", s.taskEventsWebSocket)
	s.mux.HandleFunc("GET /v1/telemetry", s.getTelemetry)
	s.mux.HandleFunc("GET /v1/scene/frame", s.getSceneFrame)
	s.mux.HandleFunc("GET /v1/orchestration/metrics", s.orchestrationMetrics)
	s.mux.Handle("GET /", operatorweb.Handler())
}

func (s *Server) runtimeStatus(w http.ResponseWriter, r *http.Request) {
	if s.runtime == nil {
		writeError(w, http.StatusServiceUnavailable, "ROBOT_DISCONNECTED", "Robot Runtime is not configured")
		return
	}
	snapshot, err := s.runtime.Info(r.Context())
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, "ROBOT_DISCONNECTED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, snapshot)
}

func (s *Server) configStatus(w http.ResponseWriter, _ *http.Request) {
	if s.settings == nil {
		writeJSON(w, http.StatusOK, ConfigStatus{Provider: "deterministic"})
		return
	}
	writeJSON(w, http.StatusOK, s.settings.Status())
}

func (s *Server) updateLLM(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		writeError(w, http.StatusServiceUnavailable, "SETTINGS_UNAVAILABLE", "settings storage is unavailable")
		return
	}
	var input LLMConfig
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil || strings.TrimSpace(input.Provider) == "" {
		writeError(w, http.StatusBadRequest, "INVALID_LLM_CONFIG", "provider is required")
		return
	}
	if err := s.settings.UpdateLLM(input); err != nil {
		writeError(w, http.StatusBadRequest, "CONFIG_UPDATE_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, s.settings.Status())
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
	taskList, err := s.service.List(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "LIST_FAILED", err.Error())
		return
	}
	if taskList == nil {
		taskList = []*tasks.Task{}
	}
	writeJSON(w, http.StatusOK, taskList)
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
	if err := s.executor.Enqueue(task.ID); err != nil {
		writeError(w, http.StatusConflict, "ENQUEUE_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, task)
}

func (s *Server) cancelTask(w http.ResponseWriter, r *http.Request) {
	if err := s.executor.Cancel(r.PathValue("id")); err != nil {
		writeError(w, http.StatusBadRequest, "CANCEL_FAILED", err.Error())
		return
	}
	task, err := s.service.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "CANCEL_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, task)
}

func (s *Server) taskEventsWebSocket(w http.ResponseWriter, r *http.Request) {
	connection, err := (&websocket.Upgrader{}).Upgrade(w, r, nil)
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

func (s *Server) getTelemetry(w http.ResponseWriter, r *http.Request) {
	adapter := r.URL.Query().Get("adapter")
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	latest, hasLatest := s.service.TelemetryLatest(adapter)
	response := map[string]any{
		"adapter": adapter, "adapters": s.service.TelemetryAdapters(),
		"history": s.service.TelemetryHistory(adapter, limit), "hasLatest": hasLatest,
	}
	if hasLatest {
		response["latest"] = latest
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) getSceneFrame(w http.ResponseWriter, r *http.Request) {
	adapter := strings.TrimSpace(r.URL.Query().Get("adapter"))
	frame, ok := s.service.SceneFrame(adapter)
	if adapter == "" || !ok || len(frame.Data) == 0 || frame.MediaType == "" {
		if issue, issueOK := s.service.SceneFrameIssue(adapter); adapter != "" && issueOK {
			code := "SCENE_FRAME_INVALID"
			message := "Scene frame bytes do not match the declared media type"
			if issue == tasks.SceneFrameUnsupported {
				code = "SCENE_FRAME_UNSUPPORTED"
				message = "Scene frame media type is not supported"
			}
			writeError(w, http.StatusUnsupportedMediaType, code, message)
			return
		}
		writeError(w, http.StatusNotFound, "SCENE_FRAME_UNAVAILABLE", "No scene frame is available for the requested adapter")
		return
	}
	w.Header().Set("Content-Type", frame.MediaType)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(frame.Data)
}

func withConsoleSecurityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", strings.Join([]string{
			"default-src 'self'",
			"script-src 'self'",
			"style-src 'self' 'unsafe-inline'",
			"img-src 'self' blob:",
			"connect-src 'self' ws: wss:",
			"object-src 'none'",
			"base-uri 'none'",
			"frame-ancestors 'none'",
			"form-action 'self'",
		}, "; "))
		next.ServeHTTP(w, r)
	})
}

func (s *Server) orchestrationMetrics(w http.ResponseWriter, r *http.Request) {
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
