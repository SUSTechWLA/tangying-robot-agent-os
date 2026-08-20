// Package fleet exposes the distributed cloud control-plane API. It is
// transport- and storage-agnostic: task persistence is provided by
// tasks.Repository, the ready-task queue by fleet/queue.Router (Redis
// Streams or in-memory), device presence by fleet/registry, telemetry by
// fleet/telemetry, and the multi-robot task graph by fleet/coordinator.
//
// The same process serves the operator web console (embedded static app),
// so the whole cloud surface is one binary behind the reverse proxy.
package fleet

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/eventlog"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/fusion"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/gateway"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/queue"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/registry"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	operatorweb "github.com/SUSTechWLA/tangying-robot-agent-os/web"
	"github.com/gorilla/websocket"
)

type Server struct {
	service     *tasks.Service
	queues      *queue.Router
	mux         *http.ServeMux
	auth        *auth.Authenticator
	registry    *registry.Registry
	telemetry   fleettelemetry.Store
	coordinator *coordinator.Coordinator
	gateway     *gateway.Server
	world       worldmodel.Reader
	startedAt   time.Time
}

type Option func(*Server)

// WithAuthenticator installs the operator/device authentication boundary.
func WithAuthenticator(authenticator *auth.Authenticator) Option {
	return func(server *Server) { server.auth = authenticator }
}

// WithRegistry installs the device registry.
func WithRegistry(deviceRegistry *registry.Registry) Option {
	return func(server *Server) { server.registry = deviceRegistry }
}

// WithTelemetry installs the fleet telemetry sink.
func WithTelemetry(store fleettelemetry.Store) Option {
	return func(server *Server) { server.telemetry = store }
}

// WithCoordinator installs the multi-robot task coordinator.
func WithCoordinator(taskCoordinator *coordinator.Coordinator) Option {
	return func(server *Server) { server.coordinator = taskCoordinator }
}

// WithGateway installs the mTLS gRPC gateway (used for device commands).
func WithGateway(deviceGateway *gateway.Server) Option {
	return func(server *Server) { server.gateway = deviceGateway }
}

// WithWorld installs the authoritative snapshot-plus-cursor world shared by
// the gateway, coordinator, Console and future Harness Agents.
func WithWorld(world worldmodel.Reader) Option {
	return func(server *Server) { server.world = world }
}

func NewServer(service *tasks.Service, queues *queue.Router, options ...Option) *Server {
	server := &Server{service: service, queues: queues, mux: http.NewServeMux(), startedAt: time.Now().UTC()}
	for _, option := range options {
		option(server)
	}
	server.routes()
	return server
}

// Handler returns the authenticated HTTP handler.
func (s *Server) Handler() http.Handler {
	handler := http.Handler(s.mux)
	if s.auth != nil {
		handler = s.auth.RequireAuth(handler)
	}
	return withFleetSecurityHeaders(handler)
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", s.health)
	s.mux.HandleFunc("POST /v1/auth/login", s.login)
	s.mux.HandleFunc("POST /v1/auth/ws-ticket", s.issueWorldSocketTicket)

	// Operator console surface.
	s.mux.HandleFunc("GET /v1/devices", s.listDevices)
	s.mux.HandleFunc("GET /v1/devices/{id}", s.getDevice)
	s.mux.HandleFunc("POST /v1/devices/{id}/estop", s.deviceEmergencyStop)
	s.mux.HandleFunc("POST /v1/devices/{id}/cancel", s.deviceCancel)
	s.mux.HandleFunc("POST /v1/tasks", s.createTask)
	s.mux.HandleFunc("GET /v1/tasks", s.listTasks)
	s.mux.HandleFunc("GET /v1/tasks/{id}", s.getTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/approve", s.approveTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/cancel", s.cancelTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/state", s.setTaskState)
	s.mux.HandleFunc("POST /v1/tasks/{id}/events", s.appendEvent)
	s.mux.HandleFunc("GET /v1/tasks/{id}/intents", s.taskIntents)
	s.mux.HandleFunc("GET /v1/tasks/{id}/domain-events", s.taskDomainEvents)
	s.mux.HandleFunc("GET /v1/telemetry", s.getTelemetry)
	s.mux.HandleFunc("GET /v1/maps/global", s.globalMap)
	s.mux.HandleFunc("GET /v1/scene/frames", s.listSceneFrames)
	s.mux.HandleFunc("GET /v1/scene/frames/{robot}", s.getSceneFrame)
	s.mux.HandleFunc("GET /v1/world", s.worldState)
	s.mux.HandleFunc("GET /v1/world/events/ws", s.worldEventsWebSocket)
	s.mux.HandleFunc("GET /v1/orchestration/metrics", s.metrics)

	// Device data plane (edge workers).
	s.mux.HandleFunc("GET /v1/queue/next", s.nextTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/intents/next", s.nextIntent)
	s.mux.HandleFunc("POST /v1/tasks/{id}/intents/{index}/complete", s.completeIntent)
	s.mux.HandleFunc("POST /v1/tasks/{id}/intents/{index}/fail", s.failIntent)
	s.mux.HandleFunc("POST /v1/telemetry", s.ingestTelemetry)

	s.mux.Handle("GET /", operatorweb.Handler())
}

func (s *Server) issueWorldSocketTicket(w http.ResponseWriter, r *http.Request) {
	if s.auth == nil {
		writeError(w, http.StatusServiceUnavailable, "AUTH_DISABLED", "authentication is not configured")
		return
	}
	value := r.Header.Get("Authorization")
	if !strings.HasPrefix(value, "Bearer ") {
		writeError(w, http.StatusUnauthorized, "OPERATOR_TOKEN_REQUIRED", "operator token is required")
		return
	}
	subject, err := s.auth.VerifyToken(r.Context(), strings.TrimPrefix(value, "Bearer "))
	if err != nil {
		writeError(w, http.StatusUnauthorized, "OPERATOR_TOKEN_INVALID", err.Error())
		return
	}
	ticket, expiry, err := s.auth.IssueWSTicket(r.Context(), subject, "fleet-world")
	if err != nil {
		writeError(w, http.StatusInternalServerError, "WS_TICKET_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"ticket": ticket, "expiresAt": expiry.UnixMilli()})
}

func (s *Server) worldEventsWebSocket(w http.ResponseWriter, r *http.Request) {
	if s.auth == nil || s.world == nil {
		writeError(w, http.StatusServiceUnavailable, "WORLD_STREAM_UNAVAILABLE", "world stream is not configured")
		return
	}
	if _, err := s.auth.ConsumeWSTicket(r.Context(), r.URL.Query().Get("ticket"), "fleet-world"); err != nil {
		writeError(w, http.StatusUnauthorized, "WS_TICKET_INVALID", err.Error())
		return
	}
	afterRevision, err := strconv.ParseUint(r.URL.Query().Get("after_revision"), 10, 64)
	if r.URL.Query().Get("after_revision") == "" {
		afterRevision, err = 0, nil
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_WORLD_CURSOR", "after_revision must be an integer")
		return
	}
	upgrader := websocket.Upgrader{CheckOrigin: func(request *http.Request) bool {
		origin := request.Header.Get("Origin")
		if origin == "" {
			return true
		}
		parsed, parseErr := url.Parse(origin)
		return parseErr == nil && parsed.Host == request.Host
	}}
	connection, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer connection.Close()
	subscription, err := s.world.Subscribe(r.Context(), afterRevision)
	if errors.Is(err, worldmodel.ErrResyncRequired) {
		_ = connection.WriteJSON(map[string]any{"type": "RESYNC_REQUIRED", "afterRevision": afterRevision})
		return
	}
	if err != nil {
		_ = connection.WriteJSON(map[string]any{"type": "WORLD_STREAM_ERROR", "message": err.Error()})
		return
	}
	for delta := range subscription {
		if err := connection.WriteJSON(delta); err != nil {
			return
		}
	}
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "mode": "fleet", "startedAt": s.startedAt.Format(time.RFC3339)})
}

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	if s.auth == nil {
		writeError(w, http.StatusServiceUnavailable, "AUTH_DISABLED", "authentication is not configured")
		return
	}
	var input struct {
		User     string `json:"user"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_LOGIN", "user and password are required")
		return
	}
	token, expiry, err := s.auth.Login(r.Context(), input.User, input.Password)
	if err != nil {
		writeError(w, http.StatusUnauthorized, "INVALID_CREDENTIALS", "invalid operator credentials")
		return
	}
	writeJSON(w, http.StatusOK, auth.TokenInfo{Token: token, ExpiresAt: expiry.UnixMilli(), Operator: input.User})
}

func (s *Server) nextTask(w http.ResponseWriter, r *http.Request) {
	if s.queues == nil {
		writeError(w, http.StatusServiceUnavailable, "QUEUE_UNAVAILABLE", "task queue is not configured")
		return
	}
	robotID, ok := deviceRobotIdentity(w, r, r.URL.Query().Get("robot_id"))
	if !ok {
		return
	}
	taskID, err := s.queues.Dequeue(r.Context(), robotID)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			writeJSON(w, http.StatusOK, map[string]any{"taskId": ""})
			return
		}
		writeError(w, http.StatusInternalServerError, "QUEUE_FAILED", err.Error())
		return
	}
	if taskID == "" {
		writeJSON(w, http.StatusOK, map[string]any{"taskId": ""})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"taskId": taskID})
}

func (s *Server) nextIntent(w http.ResponseWriter, r *http.Request) {
	if s.coordinator == nil {
		writeError(w, http.StatusServiceUnavailable, "COORDINATOR_UNAVAILABLE", "coordinator is not configured")
		return
	}
	var input struct {
		RobotID string `json:"robotId"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_CLAIM", "robotId is required")
		return
	}
	robotID, ok := deviceRobotIdentity(w, r, input.RobotID)
	if !ok {
		return
	}
	node, err := s.coordinator.NextIntent(r.Context(), r.PathValue("id"), robotID)
	if err != nil {
		writeError(w, http.StatusBadRequest, "CLAIM_FAILED", err.Error())
		return
	}
	if node == nil {
		writeJSON(w, http.StatusOK, map[string]any{"intent": nil})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"intent": node})
}

func (s *Server) completeIntent(w http.ResponseWriter, r *http.Request) {
	if s.coordinator == nil {
		writeError(w, http.StatusServiceUnavailable, "COORDINATOR_UNAVAILABLE", "coordinator is not configured")
		return
	}
	index, err := strconv.Atoi(r.PathValue("index"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_INDEX", "intent index must be an integer")
		return
	}
	var input struct {
		RobotID string `json:"robotId"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_COMPLETE", "robotId is required")
		return
	}
	robotID, ok := deviceRobotIdentity(w, r, input.RobotID)
	if !ok {
		return
	}
	snapshot, err := s.coordinator.CompleteIntent(r.Context(), r.PathValue("id"), index, robotID)
	if err != nil {
		if errors.Is(err, coordinator.ErrWorldNotReady) {
			writeError(w, http.StatusConflict, "WORLD_NOT_READY", err.Error())
			return
		}
		writeError(w, http.StatusBadRequest, "COMPLETE_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, snapshot)
}

func (s *Server) failIntent(w http.ResponseWriter, r *http.Request) {
	if s.coordinator == nil {
		writeError(w, http.StatusServiceUnavailable, "COORDINATOR_UNAVAILABLE", "coordinator is not configured")
		return
	}
	index, err := strconv.Atoi(r.PathValue("index"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_INDEX", "intent index must be an integer")
		return
	}
	var input struct {
		RobotID string `json:"robotId"`
		Reason  string `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_FAIL", "robotId and reason are required")
		return
	}
	robotID, ok := deviceRobotIdentity(w, r, input.RobotID)
	if !ok {
		return
	}
	snapshot, err := s.coordinator.FailIntent(r.Context(), r.PathValue("id"), index, robotID, input.Reason)
	if err != nil {
		writeError(w, http.StatusBadRequest, "FAIL_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, snapshot)
}

func (s *Server) taskIntents(w http.ResponseWriter, r *http.Request) {
	if s.coordinator == nil {
		writeError(w, http.StatusServiceUnavailable, "COORDINATOR_UNAVAILABLE", "coordinator is not configured")
		return
	}
	snapshot, err := s.coordinator.Snapshot(r.Context(), r.PathValue("id"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "INTENTS_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, snapshot)
}

func (s *Server) taskDomainEvents(w http.ResponseWriter, r *http.Request) {
	if s.coordinator == nil {
		writeError(w, http.StatusServiceUnavailable, "COORDINATOR_UNAVAILABLE", "coordinator is not configured")
		return
	}
	afterVersion, err := strconv.ParseUint(r.URL.Query().Get("after_version"), 10, 64)
	if r.URL.Query().Get("after_version") == "" {
		afterVersion, err = 0, nil
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_EVENT_CURSOR", "after_version must be an integer")
		return
	}
	limit, err := strconv.Atoi(r.URL.Query().Get("limit"))
	if r.URL.Query().Get("limit") == "" {
		limit, err = 100, nil
	}
	if err != nil || limit < 1 || limit > 1000 {
		writeError(w, http.StatusBadRequest, "INVALID_EVENT_LIMIT", "limit must be between 1 and 1000")
		return
	}
	events, err := s.coordinator.DomainEvents(r.Context(), r.PathValue("id"), afterVersion, limit)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "DOMAIN_EVENTS_FAILED", err.Error())
		return
	}
	if events == nil {
		events = []eventlog.DomainEvent{}
	}
	writeJSON(w, http.StatusOK, events)
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
	if s.queues != nil {
		robotIDs := intentsRobots(task)
		if err := s.queues.Enqueue(context.Background(), task.ID, robotIDs); err != nil {
			writeError(w, http.StatusConflict, "ENQUEUE_FAILED", err.Error())
			return
		}
	}
	writeJSON(w, http.StatusOK, task)
}

// intentsRobots returns the distinct robots referenced by a task's intents
// ("" is included when any intent is unbound).
func intentsRobots(task *tasks.Task) []string {
	seen := map[string]struct{}{}
	for _, intent := range task.Intent.Tasks() {
		seen[intent.RobotID] = struct{}{}
	}
	robotIDs := make([]string, 0, len(seen))
	for robotID := range seen {
		robotIDs = append(robotIDs, robotID)
	}
	return robotIDs
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

func (s *Server) listDevices(w http.ResponseWriter, r *http.Request) {
	if s.registry == nil {
		writeError(w, http.StatusServiceUnavailable, "REGISTRY_UNAVAILABLE", "device registry is not configured")
		return
	}
	devices, err := s.registry.List(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "LIST_FAILED", err.Error())
		return
	}
	if devices == nil {
		devices = []registry.Device{}
	}
	writeJSON(w, http.StatusOK, devices)
}

func (s *Server) getDevice(w http.ResponseWriter, r *http.Request) {
	if s.registry == nil {
		writeError(w, http.StatusServiceUnavailable, "REGISTRY_UNAVAILABLE", "device registry is not configured")
		return
	}
	device, ok, err := s.registry.Status(r.Context(), r.PathValue("id"))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "READ_FAILED", err.Error())
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "DEVICE_NOT_FOUND", "no such robot device")
		return
	}
	writeJSON(w, http.StatusOK, device)
}

func (s *Server) deviceEmergencyStop(w http.ResponseWriter, r *http.Request) {
	if s.gateway == nil {
		writeError(w, http.StatusServiceUnavailable, "GATEWAY_UNAVAILABLE", "device gateway is not configured")
		return
	}
	var input struct {
		Reason string `json:"reason"`
	}
	_ = json.NewDecoder(r.Body).Decode(&input)
	if input.Reason == "" {
		input.Reason = "operator emergency stop"
	}
	if err := s.gateway.PushCommand(r.PathValue("id"), "emergency_stop", map[string]string{"reason": input.Reason}); err != nil {
		writeError(w, http.StatusConflict, "PUSH_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "pushed", "robotId": r.PathValue("id")})
}

func (s *Server) deviceCancel(w http.ResponseWriter, r *http.Request) {
	if s.gateway == nil {
		writeError(w, http.StatusServiceUnavailable, "GATEWAY_UNAVAILABLE", "device gateway is not configured")
		return
	}
	var input struct {
		TaskID string `json:"taskId"`
		StepID string `json:"stepId"`
		Reason string `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&input); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_CANCEL", "invalid cancel request")
		return
	}
	if input.Reason == "" {
		input.Reason = "operator cancel"
	}
	args := map[string]string{"task_id": input.TaskID, "step_id": input.StepID, "reason": input.Reason}
	if err := s.gateway.PushCommand(r.PathValue("id"), "cancel_step", args); err != nil {
		writeError(w, http.StatusConflict, "PUSH_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "pushed", "robotId": r.PathValue("id")})
}

func (s *Server) ingestTelemetry(w http.ResponseWriter, r *http.Request) {
	if s.telemetry == nil {
		writeError(w, http.StatusServiceUnavailable, "TELEMETRY_UNAVAILABLE", "telemetry store is not configured")
		return
	}
	var sample fleettelemetry.Sample
	if err := json.NewDecoder(r.Body).Decode(&sample); err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_TELEMETRY", err.Error())
		return
	}
	robotID, ok := deviceRobotIdentity(w, r, sample.RobotID)
	if !ok {
		return
	}
	sample.RobotID = robotID
	if sample.ObservedAt.IsZero() {
		sample.ObservedAt = time.Now().UTC()
	}
	if err := s.telemetry.Ingest(r.Context(), sample); err != nil {
		writeError(w, http.StatusBadRequest, "INGEST_FAILED", err.Error())
		return
	}
	// Degraded-mode presence: a telemetry report also renews the device
	// lease so the console shows the robot ONLINE even without the mTLS
	// Link channel (the Link remains the primary presence path).
	if s.registry != nil {
		_, _ = s.registry.Register(r.Context(), registry.Device{
			RobotID: sample.RobotID, Adapter: sample.Adapter,
		}, 30*time.Second)
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"status": "accepted", "robotId": sample.RobotID})
}

func deviceRobotIdentity(w http.ResponseWriter, r *http.Request, claimed string) (string, bool) {
	robotID, ok := auth.DeviceRobotID(r.Context())
	if !ok {
		writeError(w, http.StatusForbidden, "DEVICE_PRINCIPAL_REQUIRED", "authenticated robot device principal is required")
		return "", false
	}
	claimed = strings.TrimSpace(claimed)
	if claimed != "" && claimed != robotID {
		writeError(w, http.StatusForbidden, "ROBOT_IDENTITY_MISMATCH", fmt.Sprintf("authenticated robot %q cannot act as %q", robotID, claimed))
		return "", false
	}
	return robotID, true
}

func (s *Server) getTelemetry(w http.ResponseWriter, r *http.Request) {
	if s.telemetry == nil {
		writeError(w, http.StatusServiceUnavailable, "TELEMETRY_UNAVAILABLE", "telemetry store is not configured")
		return
	}
	robotID := strings.TrimSpace(r.URL.Query().Get("robot_id"))
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	if limit <= 0 {
		limit = 60
	}
	if robotID == "" {
		robots, err := s.telemetry.Robots(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, "READ_FAILED", err.Error())
			return
		}
		response := map[string]any{"robots": robots, "adapters": []string{"fleet"}}
		writeJSON(w, http.StatusOK, response)
		return
	}
	latest, ok, err := s.telemetry.Latest(r.Context(), robotID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "READ_FAILED", err.Error())
		return
	}
	trajectory, err := s.telemetry.Trajectory(r.Context(), robotID, limit)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "READ_FAILED", err.Error())
		return
	}
	response := map[string]any{"robotId": robotID, "hasLatest": ok, "latest": latest, "trajectory": trajectory}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) globalMap(w http.ResponseWriter, r *http.Request) {
	if s.telemetry == nil {
		writeError(w, http.StatusServiceUnavailable, "TELEMETRY_UNAVAILABLE", "telemetry store is not configured")
		return
	}
	robots, err := s.telemetry.Robots(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "READ_FAILED", err.Error())
		return
	}
	samples := map[string]fleettelemetry.Sample{}
	trajectories := map[string][]fleettelemetry.Sample{}
	for _, robotID := range robots {
		latest, ok, err := s.telemetry.Latest(r.Context(), robotID)
		if err != nil || !ok {
			continue
		}
		samples[robotID] = latest
		trajectories[robotID], _ = s.telemetry.Trajectory(r.Context(), robotID, 600)
	}
	cellSize, _ := strconv.ParseFloat(r.URL.Query().Get("cell_size"), 64)
	global := fusion.Global(samples, cellSize)
	global = fusion.WithTrajectories(global, trajectories)
	global.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	writeJSON(w, http.StatusOK, global)
}

// listSceneFrames reports which robots currently stream live frames.
func (s *Server) listSceneFrames(w http.ResponseWriter, r *http.Request) {
	if s.telemetry == nil {
		writeError(w, http.StatusServiceUnavailable, "TELEMETRY_UNAVAILABLE", "telemetry store is not configured")
		return
	}
	robots, err := s.telemetry.Robots(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "READ_FAILED", err.Error())
		return
	}
	result := []map[string]any{}
	for _, robotID := range robots {
		frame, ok, err := s.telemetry.Frame(r.Context(), robotID)
		if err != nil {
			continue
		}
		if ok {
			result = append(result, map[string]any{"robotId": robotID, "mediaType": frame.MediaType, "bytes": len(frame.Data)})
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"frames": result})
}

// getSceneFrame streams one robot's latest live frame (PNG/JPEG).
func (s *Server) getSceneFrame(w http.ResponseWriter, r *http.Request) {
	if s.telemetry == nil {
		writeError(w, http.StatusServiceUnavailable, "TELEMETRY_UNAVAILABLE", "telemetry store is not configured")
		return
	}
	frame, ok, err := s.telemetry.Frame(r.Context(), r.PathValue("robot"))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "READ_FAILED", err.Error())
		return
	}
	if !ok || len(frame.Data) == 0 {
		writeError(w, http.StatusNotFound, "SCENE_FRAME_UNAVAILABLE", "no live frame for this robot")
		return
	}
	w.Header().Set("Content-Type", frame.MediaType)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(frame.Data)
}

// worldState is the machine-readable harness snapshot: every robot's pose,
// semantics (held/placements/activity) and the fused entities, so a harness
// agent can observe the whole environment and self-orchestrate tasks.
func (s *Server) worldState(w http.ResponseWriter, r *http.Request) {
	if s.world != nil {
		snapshot, err := s.world.Snapshot(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, "WORLD_READ_FAILED", err.Error())
			return
		}
		writeJSON(w, http.StatusOK, snapshot)
		return
	}
	if s.telemetry == nil {
		writeError(w, http.StatusServiceUnavailable, "TELEMETRY_UNAVAILABLE", "telemetry store is not configured")
		return
	}
	robots, err := s.telemetry.Robots(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "READ_FAILED", err.Error())
		return
	}
	samples := map[string]fleettelemetry.Sample{}
	trajectories := map[string][]fleettelemetry.Sample{}
	for _, robotID := range robots {
		latest, ok, err := s.telemetry.Latest(r.Context(), robotID)
		if err != nil || !ok {
			continue
		}
		samples[robotID] = latest
		trajectories[robotID], _ = s.telemetry.Trajectory(r.Context(), robotID, 600)
	}
	global := fusion.WithTrajectories(fusion.Global(samples, 0.1), trajectories)

	world := map[string]any{
		"updatedAt": time.Now().UTC().Format(time.RFC3339),
		"robots":    global.Robots,
		"entities":  global.Entities,
		"tasks":     map[string]any{},
	}
	if s.coordinator != nil {
		tasksList, err := s.service.List(r.Context())
		if err == nil {
			active := []map[string]any{}
			for _, task := range tasksList {
				if isTaskTerminal(string(task.State)) {
					continue
				}
				summary := map[string]any{
					"id": task.ID, "state": string(task.State), "request": task.Request, "approved": task.Approved,
				}
				view, viewErr := s.coordinator.Snapshot(r.Context(), task.ID)
				if viewErr == nil {
					summary["intents"] = view.Intents
				}
				active = append(active, summary)
			}
			world["tasks"] = active
		}
	}
	writeJSON(w, http.StatusOK, world)
}

func isTaskTerminal(state string) bool {
	switch state {
	case "SUCCEEDED", "FAILED", "CANCELLED", "SAFETY_STOPPED":
		return true
	default:
		return false
	}
}

func (s *Server) metrics(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.service.OrchestrationMetrics(r.Context()))
}

func withFleetSecurityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", strings.Join([]string{
			"default-src 'self'",
			"script-src 'self'",
			"style-src 'self' 'unsafe-inline'",
			"img-src 'self' blob: data:",
			"connect-src 'self' ws: wss:",
			"object-src 'none'",
			"base-uri 'none'",
			"frame-ancestors 'none'",
			"form-action 'self'",
		}, "; "))
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]string{"code": code, "message": message})
}

var _ = fmt.Sprintf
