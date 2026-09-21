// Package console exposes the Local Agent API and embedded web application.
// Distributed control-plane mutation routes do not belong here.
//
// # What guards it
//
// It binds to loopback by default, and that default is enforced where the listen
// address is parsed rather than assumed here (cmd/local-agent/listen.go): a
// non-loopback bind is refused unless the operator asks for it by name.
//
// Over either binding, every mutating route requires a console session — a
// SameSite=Strict cookie for the page, or the X-Tangying-Session header for tests
// and CLI tools — and rejects a request that announces itself as cross-site. See
// guard.go for why: before it, ten mutating handlers accepted a cross-site POST,
// and the recovery-execution endpoint recorded an approval that nothing could
// falsify.
package console

import (
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/latency"
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

type CameraProvider interface {
	TelemetrySource(context.Context, string, string) (telemetry.Snapshot, error)
}

func WithCamera(provider CameraProvider) Option {
	return func(server *Server) { server.camera = provider }
}

func WithSettings(settings Settings) Option {
	return func(server *Server) { server.settings = settings }
}

func WithRuntime(provider RuntimeProvider) Option {
	return func(server *Server) { server.runtime = provider }
}

// LatencyProvider is the step-timing reader behind GET /v1/telemetry/latency.
// It is an interface rather than a *latency.Recorder so a deployment can serve
// the same contract from durable storage later without touching the console.
type LatencyProvider interface {
	StepLatency(groupBy latency.GroupBy, window time.Duration, now time.Time) (latency.Report, error)
}

// WithLatency installs the step-timing reader. Without it the endpoint refuses
// with LATENCY_UNAVAILABLE instead of returning zeros: "we did not measure" and
// "everything was instant" must not look the same.
func WithLatency(provider LatencyProvider) Option {
	return func(server *Server) { server.latency = provider }
}

// WithWorld installs the same authoritative WorldSnapshot reader used by the
// cloud Fleet. Local Brain keeps a different transport/auth profile, but
// Harness Agents consume the identical environment-state contract.
func WithWorld(world worldmodel.Reader) Option {
	return func(server *Server) { server.world = world }
}

type Server struct {
	service       *tasks.Service
	executor      Executor
	settings      Settings
	runtime       RuntimeProvider
	camera        CameraProvider
	navigation    *NavigationReader
	robotServices RobotServiceProvider
	world         worldmodel.Reader
	evidence      tasks.EvidenceStore
	latency       LatencyProvider
	mux           *http.ServeMux
	// session is this process's console session. Every mutating route passes
	// through it; see guard.go for why the console has one at all.
	session *sessionGuard
}

func NewServer(service *tasks.Service, executor Executor, options ...Option) *Server {
	server := &Server{
		service: service, executor: executor, mux: http.NewServeMux(),
		session: newSessionGuard(),
	}
	for _, option := range options {
		option(server)
	}
	server.routes()
	return server
}

// SessionToken returns this console's session token.
//
// It is an in-process accessor, not a route: anything that can call it already
// holds the task service, so it grants nothing new, whereas an endpoint serving
// it to whoever asks would make the token mean "whoever asked" — the exact
// reading this replaced.
func (s *Server) SessionToken() string {
	if s.session == nil {
		return ""
	}
	return s.session.token
}

// WithSessionToken fixes the session token instead of minting one. It exists for
// tests and for embedders that already authenticate the caller; it must not be
// used to share one token between two consoles.
func WithSessionToken(token string) Option {
	return func(s *Server) {
		if s.session == nil {
			s.session = &sessionGuard{}
		}
		s.session.token = token
	}
}

func (s *Server) Handler() http.Handler {
	return withConsoleSecurityHeaders(s.withSessionCookie(s.mux))
}

// withSessionCookie hands the page its session token on reads, so the browser
// sends it back automatically and the front end needs no knowledge of it.
func (s *Server) withSessionCookie(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.session.ensureCookie(w, r)
		next.ServeHTTP(w, r)
	})
}

func (s *Server) routes() {
	s.evidenceRoutes()
	// Liveness, not readiness. It stays a constant on purpose: it answers "is this
	// process alive", which is what a supervisor acts on, and a robot that is
	// safely stopped is a healthy process doing its job. Restarting because of a
	// latched emergency stop would be an outage caused by a working safety
	// feature. "Can this robot be used right now" is /v1/readiness.
	s.mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "mode": "local"})
	})
	s.mux.HandleFunc("GET /v1/readiness", s.readiness)
	s.mux.HandleFunc("GET /v1/robots/discovered", s.discoveredRobots)
	s.mux.HandleFunc("POST /v1/robots/pair", s.pairRobot)
	s.mux.HandleFunc("GET /v1/config/status", s.configStatus)
	s.mux.HandleFunc("PUT /v1/config/llm", s.updateLLM)
	s.mux.HandleFunc("GET /v1/runtime", s.runtimeStatus)
	s.mux.HandleFunc("POST /v1/tasks", s.createTask)
	s.mux.HandleFunc("GET /v1/tasks", s.listTasks)
	s.mux.HandleFunc("GET /v1/tasks/{id}", s.getTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/approve", s.approveTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/cancel", s.cancelTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/pause", s.pauseTask)
	s.mux.HandleFunc("POST /v1/tasks/{id}/resume", s.resumeTask)
	s.mux.HandleFunc("GET /v1/tasks/{id}/recovery", s.localRecovery)
	s.mux.HandleFunc("POST /v1/recovery/execute", s.executeRecovery)
	// The clearing path for an unknown physical outcome. It is a person's
	// conclusion and nothing else can produce one; see console/reconcile.go.
	s.mux.HandleFunc("POST /v1/tasks/{id}/reconcile", s.reconcileStep)
	s.mux.HandleFunc("POST /v1/tasks/{id}/revisions", s.proposeTaskRevision)
	s.mux.HandleFunc("POST /v1/tasks/{id}/revisions/{revision}/confirm", s.confirmTaskRevision)
	s.mux.HandleFunc("GET /v1/tasks/{id}/revisions", s.listTaskRevisions)
	s.mux.HandleFunc("GET /v1/tasks/{id}/experience", s.taskExperience)
	s.mux.HandleFunc("GET /v1/agent/alerts", s.agentAlerts)
	s.mux.HandleFunc("GET /v1/calibration/session", s.calibrationSession)
	s.mux.HandleFunc("GET /v1/calibration", s.calibrationDocument)
	s.mux.HandleFunc("GET /v1/robot/services", s.serviceCatalogue)
	// Read-only: the survey can be watched by anyone who can reach the console,
	// and started only by an operator holding a session. See mapping.go.
	s.mux.HandleFunc("GET /v1/mapping", s.mappingStatus)
	// The natural-language entry for mapping: the write half of the same story.
	// One route watches a survey, this one asks for one from a sentence.
	s.mux.HandleFunc("POST /v1/mapping/request", s.mappingRequest)
	s.mux.HandleFunc("POST /v1/robot/services", s.callRobotService)
	s.registerMapRoutes()
	s.mux.HandleFunc("GET /v1/tasks/{id}/events/ws", s.taskEventsWebSocket)
	s.mux.HandleFunc("GET /v1/telemetry", s.getTelemetry)
	s.mux.HandleFunc("GET /v1/scene/frame", s.getSceneFrame)
	s.mux.HandleFunc("GET /v1/scene/depth", s.getSceneDepth)
	s.mux.HandleFunc("GET /v1/scene/camera", s.cameraObservation)
	s.mux.HandleFunc("GET /v1/navigation/map", s.navigationMap)
	s.mux.HandleFunc("GET /v1/world", s.worldState)
	s.mux.HandleFunc("GET /v1/world/events/ws", s.worldEventsWebSocket)
	s.mux.HandleFunc("GET /v1/orchestration/metrics", s.orchestrationMetrics)
	s.mux.HandleFunc("GET /v1/telemetry/latency", s.stepLatency)
	s.mux.Handle("GET /", operatorweb.Handler())
}

func (s *Server) worldState(w http.ResponseWriter, r *http.Request) {
	if s.world == nil {
		writeError(w, http.StatusServiceUnavailable, "WORLD_UNAVAILABLE", "authoritative world is not configured")
		return
	}
	snapshot, err := s.world.Snapshot(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "WORLD_READ_FAILED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, snapshot)
}

func (s *Server) worldEventsWebSocket(w http.ResponseWriter, r *http.Request) {
	if s.world == nil {
		writeError(w, http.StatusServiceUnavailable, "WORLD_UNAVAILABLE", "authoritative world is not configured")
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
	connection, err := (&websocket.Upgrader{CheckOrigin: func(request *http.Request) bool {
		origin := request.Header.Get("Origin")
		return origin == "" || origin == "http://"+request.Host || origin == "https://"+request.Host
	}}).Upgrade(w, r, nil)
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
		_ = connection.WriteJSON(map[string]string{"type": "WORLD_STREAM_ERROR", "message": err.Error()})
		return
	}
	for delta := range subscription {
		if err := connection.WriteJSON(delta); err != nil {
			return
		}
	}
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
	if !s.allowOperatorWrite(w, r) {
		return
	}
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
	if !s.allowOperatorWrite(w, r) {
		return
	}
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
	if !s.allowOperatorWrite(w, r) {
		return
	}
	// The approval records the session that gave it, not just that someone did.
	// It is the same evidence the recovery endpoint carries, for the same reason:
	// a boolean nobody can audit is not an approval record.
	task, err := s.service.Approve(r.Context(), r.PathValue("id"), s.operatorEvidence(r))
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
	if !s.allowOperatorWrite(w, r) {
		return
	}
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
	// Live camera readers need metadata, not repeated historical point clouds.
	// Keep the default history contract for diagnostics and existing clients.
	history := []telemetry.Snapshot{}
	if r.URL.Query().Get("history") != "false" {
		history = s.service.TelemetryHistory(adapter, limit)
	}
	response := map[string]any{
		"adapter": adapter, "adapters": s.service.TelemetryAdapters(),
		"history": history, "hasLatest": hasLatest,
	}
	if hasLatest {
		response["latest"] = latest
	}
	// A reader who finds no telemetry must be able to tell "the robot said
	// nothing" from "the robot said things and we could not file them". Without
	// this the two look identical here, which is how a robot whose every
	// observation was being discarded still reported itself as merely quiet.
	if unfiled, at := s.service.TelemetryUnfiled(); unfiled > 0 {
		response["unfiled"] = map[string]any{
			"count": unfiled, "lastAt": at.UTC(),
			"reason": "这些观测没有标明 adapter，无法归档；轮询仍在收到数据，是接线问题而不是机器人没说话",
		}
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) getSceneFrame(w http.ResponseWriter, r *http.Request) {
	s.serveSceneFrame(w, r, false)
}

func (s *Server) getSceneDepth(w http.ResponseWriter, r *http.Request) {
	s.serveSceneFrame(w, r, true)
}

func (s *Server) serveSceneFrame(w http.ResponseWriter, r *http.Request, depth bool) {
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	adapter := strings.TrimSpace(r.URL.Query().Get("adapter"))
	if adapter == "" && s.runtime != nil {
		if info, err := s.runtime.Info(r.Context()); err == nil {
			adapter = info.Adapter
		}
	}
	frame, ok := s.service.SceneFrame(adapter)
	issue, issueOK := s.service.SceneFrameIssue(adapter)
	prefix := "SCENE_FRAME"
	if depth {
		frame, ok = s.service.DepthFrame(adapter)
		issue, issueOK = s.service.DepthFrameIssue(adapter)
		prefix = "SCENE_DEPTH"
	}
	if adapter == "" || !ok || len(frame.Data) == 0 || frame.MediaType == "" {
		if adapter != "" && issueOK {
			code := prefix + "_INVALID"
			message := "Scene frame bytes do not match the declared media type"
			if issue == tasks.SceneFrameUnsupported {
				code = prefix + "_UNSUPPORTED"
				message = "Scene frame media type is not supported"
			}
			writeError(w, http.StatusUnsupportedMediaType, code, message)
			return
		}
		writeError(w, http.StatusNotFound, prefix+"_UNAVAILABLE", "No scene frame is available for the requested adapter")
		return
	}
	age := time.Since(frame.ObservedAt)
	if !frame.Fresh(time.Now()) {
		writeError(w, http.StatusServiceUnavailable, prefix+"_STALE", "画面缺少有效采集时间或已经过期，等待新的传感器帧")
		return
	}
	w.Header().Set("X-Observed-At", frame.ObservedAt.UTC().Format(time.RFC3339Nano))
	if age < 0 {
		age = 0
	}
	w.Header().Set("X-Frame-Age-Ms", strconv.FormatInt(age.Milliseconds(), 10))
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
			"connect-src 'self' blob: ws: wss:",
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

// stepLatency reports measured step timings by phase. The window and grouping
// are explicit query parameters with bounded values: an unbounded window on a
// robot that has been up for weeks is a denial-of-service on itself.
func (s *Server) stepLatency(w http.ResponseWriter, r *http.Request) {
	if s.latency == nil {
		writeError(w, http.StatusServiceUnavailable, "LATENCY_UNAVAILABLE",
			"step timing is not being recorded in this deployment")
		return
	}
	groupBy := latency.GroupBy(r.URL.Query().Get("groupBy"))
	if groupBy == "" {
		groupBy = latency.GroupByCapability
	}
	window := time.Hour
	if raw := r.URL.Query().Get("windowMs"); raw != "" {
		parsed, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || parsed < 0 || parsed > int64(30*24*time.Hour/time.Millisecond) {
			writeError(w, http.StatusBadRequest, "INVALID_WINDOW",
				"windowMs must be 0 (everything retained) or up to 30 days of milliseconds")
			return
		}
		window = time.Duration(parsed) * time.Millisecond
	}
	report, err := s.latency.StepLatency(groupBy, window, time.Now())
	if err != nil {
		writeError(w, http.StatusBadRequest, "UNSUPPORTED_GROUPING", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, report)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]string{"code": code, "message": message})
}

// calibrationSession serves the guided calibration progress written by the
// wizard. The step order, the wording and the summary are produced by the Python
// wizard and only rendered by the console, so there is exactly one definition of
// what the operator is asked to do.
//
// The file is re-read on every request: the wizard runs as a separate process and
// the operator expects the page to follow it without restarting anything.
func (s *Server) calibrationSession(w http.ResponseWriter, r *http.Request) {
	path := os.Getenv("TANGYING_CALIBRATION_STATUS")
	if path == "" {
		path = filepath.Join("artifacts", "calibration", "session.status.json")
	}
	raw, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		// Not an error: most deployments never open the wizard, and the console
		// says so instead of showing a failure.
		writeJSON(w, http.StatusOK, map[string]any{
			"available": false,
			"reason":    "还没有标定会话记录；打开标定向导后这里会显示进度。",
		})
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "CALIBRATION_UNREADABLE", err.Error())
		return
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		writeError(w, http.StatusInternalServerError, "CALIBRATION_MALFORMED", err.Error())
		return
	}
	payload["available"] = true
	// Where the file came from, without where it is. The absolute path used to be
	// echoed to every reader, which told anyone who could reach the console the
	// operator's directory layout; the diagnostic question an operator actually
	// asks is "did this come from the default location or from the env var", and
	// that is answered by the source name and the file name alone.
	payload["source"] = calibrationSource()
	payload["file"] = filepath.Base(path)
	writeJSON(w, http.StatusOK, payload)
}

// calibrationDocument serves the calibration itself, not a summary of it.
//
// The page exists so somebody can see what their robot was measured to be and edit it
// if their own procedure disagrees. A progress summary alone left them reading "16
// servos, 2 cameras" with no number behind it.
func (s *Server) calibrationDocument(w http.ResponseWriter, r *http.Request) {
	if s.robotServices != nil {
		result, err := s.invokeRobotService(r.Context(), "calibration.get", "", nil)
		if err != nil {
			writeError(w, 502, "CALIBRATION_UNAVAILABLE", err.Error())
			return
		}
		if !result.Ok {
			writeError(w, 409, result.Code, result.Message)
			return
		}
		writeJSON(w, 200, result.Result.AsMap())
		return
	}
	path := os.Getenv("TANGYING_CALIBRATION_DOCUMENT")
	if path == "" {
		path = filepath.Join("artifacts", "calibration", "sim.json")
	}
	raw, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		writeJSON(w, http.StatusOK, map[string]any{
			"available": false,
			"reason":    "还没有标定结果；跑一次标定向导后这里会显示全部参数。",
		})
		return
	}
	if err != nil {
		writeError(w, http.StatusInternalServerError, "CALIBRATION_UNREADABLE", err.Error())
		return
	}
	var document map[string]any
	if err := json.Unmarshal(raw, &document); err != nil {
		writeError(w, http.StatusInternalServerError, "CALIBRATION_MALFORMED", err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"available": true, "source": calibrationSource(), "file": filepath.Base(path), "document": document,
	})
}

// calibrationSource names which configuration chose the calibration files, which
// is the fact an operator needs when the console shows the wrong document. The
// path itself is not sent: it is the operator's filesystem layout, and nothing
// in this API acts on it.
func calibrationSource() string {
	if strings.TrimSpace(os.Getenv("TANGYING_CALIBRATION_STATUS")) != "" ||
		strings.TrimSpace(os.Getenv("TANGYING_CALIBRATION_DOCUMENT")) != "" {
		return "env"
	}
	return "default"
}
