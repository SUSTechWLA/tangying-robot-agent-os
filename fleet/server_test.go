package fleet_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/sensors"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/auth"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/coordinator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/lease"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/queue"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/registry"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/worldhub"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
	"github.com/gorilla/websocket"
)

func TestWorldSnapshotUsesProjectorRevision(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	hub := worldhub.New("fleet-default", time.Minute, 8)
	if _, err := hub.Ingest(context.Background(), fleetWorldObservation(1)); err != nil {
		t.Fatal(err)
	}
	server := fleet.NewServer(service, nil, fleet.WithWorld(hub))
	request := httptest.NewRequest(http.MethodGet, "/v1/world", nil)
	response := httptest.NewRecorder()

	server.Handler().ServeHTTP(response, request)

	var snapshot worldmodel.Snapshot
	if err := json.NewDecoder(response.Body).Decode(&snapshot); err != nil {
		t.Fatal(err)
	}
	if response.Code != http.StatusOK || snapshot.Revision != 1 || snapshot.EventCursor == "" {
		t.Fatalf("status=%d snapshot=%#v", response.Code, snapshot)
	}
}

func TestAcceptanceNonceIsServerBoundToResponseAndWorld(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	hub := worldhub.New("fleet-default", time.Minute, 8)
	if _, err := hub.Ingest(context.Background(), fleetWorldObservation(1)); err != nil {
		t.Fatal(err)
	}
	const nonce = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
	server := fleet.NewServer(service, nil, fleet.WithWorld(hub), fleet.WithAcceptanceNonce(nonce))
	request := httptest.NewRequest(http.MethodGet, "/v1/world", nil)
	response := httptest.NewRecorder()

	server.Handler().ServeHTTP(response, request)

	var snapshot map[string]any
	if err := json.NewDecoder(response.Body).Decode(&snapshot); err != nil {
		t.Fatal(err)
	}
	if response.Header().Get("X-Tangying-Acceptance-Nonce") != nonce || snapshot["acceptanceNonce"] != nonce {
		t.Fatalf("header=%q world nonce=%#v", response.Header().Get("X-Tangying-Acceptance-Nonce"), snapshot["acceptanceNonce"])
	}
}

func TestFleetServesPublicAssetManifestButProtectsAPI(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()

	asset, err := http.Get(f.server.URL + "/assets/scenes/robocasa-handoff-v1/manifest.json")
	if err != nil {
		t.Fatal(err)
	}
	asset.Body.Close()
	if asset.StatusCode != http.StatusOK || asset.Header.Get("Cache-Control") != "no-cache" {
		t.Fatalf("asset status=%d cache=%q", asset.StatusCode, asset.Header.Get("Cache-Control"))
	}

	api, err := http.Get(f.server.URL + "/v1/tasks")
	if err != nil {
		t.Fatal(err)
	}
	api.Body.Close()
	if api.StatusCode != http.StatusUnauthorized {
		t.Fatalf("anonymous API status=%d, want %d", api.StatusCode, http.StatusUnauthorized)
	}
}

func TestTaskSummaryListIsBoundedAndOmitsHeavyExecutionHistory(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	for index := 0; index < 24; index++ {
		task, err := f.service.Create(context.Background(),
			fmt.Sprintf("让1号机器人把红色杯子放进右侧收纳盒 %d", index), "mujoco")
		if err != nil {
			t.Fatal(err)
		}
		for eventIndex := 0; eventIndex < 20; eventIndex++ {
			if _, err := f.service.AppendEvent(context.Background(), task.ID, tasks.TaskEvent{
				Type: "TOOL_ACTIVITY", Payload: map[string]any{"trace": strings.Repeat("x", 1024)},
			}); err != nil {
				t.Fatal(err)
			}
		}
	}
	response := f.do(t, http.MethodGet, "/v1/tasks?view=summary&limit=20", nil, true, false)
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.StatusCode, body)
	}
	if len(body) >= 64*1024 || bytes.Contains(body, []byte(`"events"`)) || bytes.Contains(body, []byte(`"plan"`)) {
		t.Fatalf("summary response is heavy: bytes=%d body-prefix=%s", len(body), body[:min(len(body), 256)])
	}
	var summaries []tasks.TaskSummary
	if err := json.Unmarshal(body, &summaries); err != nil {
		t.Fatal(err)
	}
	if len(summaries) != 20 {
		t.Fatalf("summary count=%d, want 20", len(summaries))
	}
}

func TestFleetContentSecurityPolicyAllowsEmbeddedGLTFTextures(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()

	response, err := http.Get(f.server.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()

	policy := response.Header.Get("Content-Security-Policy")
	for _, directive := range []string{
		"default-src 'self'", "script-src 'self'", "img-src 'self' blob: data:",
		"connect-src 'self' blob: ws: wss:",
		"object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'",
	} {
		if !strings.Contains(policy, directive) {
			t.Errorf("CSP %q missing %q", policy, directive)
		}
	}
}

func TestWorldWebSocketTicketIsConsumedAndReplaysCursor(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	hub := worldhub.New("fleet-default", time.Minute, 8)
	authenticator, err := auth.New(auth.Options{
		OperatorUser: "admin", OperatorPass: "admin123", DeviceCredentials: map[string]string{"robot-1": "test-device-token-1"}, Secret: "secret",
	})
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(fleet.NewServer(service, nil,
		fleet.WithAuthenticator(authenticator), fleet.WithWorld(hub),
	).Handler())
	defer server.Close()
	for sequence := uint64(1); sequence <= 3; sequence++ {
		if _, err := hub.Ingest(context.Background(), fleetWorldObservation(sequence)); err != nil {
			t.Fatal(err)
		}
	}
	operatorToken, _, err := authenticator.Login(context.Background(), "admin", "admin123")
	if err != nil {
		t.Fatal(err)
	}
	ticketRequest, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/auth/ws-ticket", nil)
	ticketRequest.Header.Set("Authorization", "Bearer "+operatorToken)
	ticketResponse, err := http.DefaultClient.Do(ticketRequest)
	if err != nil {
		t.Fatal(err)
	}
	var ticket struct {
		Ticket string `json:"ticket"`
	}
	if err := json.NewDecoder(ticketResponse.Body).Decode(&ticket); err != nil {
		t.Fatal(err)
	}
	ticketResponse.Body.Close()
	wsURL := "ws" + strings.TrimPrefix(server.URL, "http") + "/v1/world/events/ws?after_revision=1&ticket=" + ticket.Ticket
	connection, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	var delta worldmodel.Delta
	if err := connection.ReadJSON(&delta); err != nil {
		t.Fatal(err)
	}
	if delta.Revision != 3 || delta.Snapshot.Revision != 3 {
		t.Fatalf("coalesced replay revision=%d snapshot=%d", delta.Revision, delta.Snapshot.Revision)
	}
	if _, _, err := websocket.DefaultDialer.Dial(wsURL, nil); err == nil {
		t.Fatal("consumed websocket ticket was accepted twice")
	}
}

func fleetWorldObservation(sequence uint64) observation.Envelope {
	now := time.Now().UTC().Add(time.Duration(sequence) * time.Millisecond)
	return observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: fmt.Sprintf("world-observation-%d", sequence),
		WorldID: "fleet-default", SourceID: "robot-1/scene", RobotID: "robot-1",
		SourceType: observation.SourceSimGroundTruth, SourceSequence: sequence,
		ObservedAt: now, ReceivedAt: now, FrameID: "world", TransformRevision: "scene-v1",
		Kind: observation.EntityUpsert, Payload: observation.EntityPayload{
			EntityID: "red-block", Category: "block", Pose: []float64{float64(sequence), 0, 0},
		},
		Confidence: 1, Provenance: observation.Provenance{Adapter: "mujoco", Version: "test"},
	}
}

func TestCompleteIntentReturnsWorldNotReadyConflict(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	f.coordinator.WithWorld(worldhub.New("fleet-default", time.Minute, 16), time.Minute).
		WithResourceLeases(lease.NewMemoryManager(), time.Minute).
		WithCatalogLookup(func(context.Context, string) (string, error) { return "catalog-v1", nil })
	task := createTask(t, f, "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "mujoco")
	approve := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/approve", nil, true, false)
	approve.Body.Close()
	claim := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/intents/next", map[string]string{"robotId": "robot-1"}, false, true)
	claim.Body.Close()

	response := f.do(t, http.MethodPost, fmt.Sprintf("/v1/tasks/%s/intents/0/complete", task.ID), map[string]string{"robotId": "robot-1"}, false, true)
	defer response.Body.Close()
	var payload map[string]string
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusConflict || payload["code"] != "WORLD_NOT_READY" {
		t.Fatalf("status=%d payload=%v", response.StatusCode, payload)
	}
}

func TestRevisionedCompletionRequiresAndAcceptsExactClaimIdentity(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	ctx := context.Background()
	task, err := f.service.Create(ctx, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	proposal, err := f.service.ProposeRevision(ctx, tasks.ProposeRevisionCommand{
		TaskID: task.ID, ExpectedRevision: 1, Request: "最后放到左侧目标区",
		IdempotencyKey: "server-revision-proposal", Creator: "owner",
	}, tasks.RevisionBasis{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.coordinator.ConfirmRevision(ctx, task.ID, proposal.Revision.Revision, 1, "server-revision-confirm"); err != nil {
		t.Fatal(err)
	}
	node, err := f.coordinator.NextIntent(ctx, task.ID, "robot-1")
	if err != nil {
		t.Fatal(err)
	}

	legacy := f.do(t, http.MethodPost, fmt.Sprintf("/v1/tasks/%s/intents/%d/complete", task.ID, node.Index),
		map[string]any{"robotId": "robot-1"}, false, true)
	legacy.Body.Close()
	if legacy.StatusCode == http.StatusOK {
		t.Fatal("revisioned graph accepted identity-free completion")
	}
	exact := f.do(t, http.MethodPost, fmt.Sprintf("/v1/tasks/%s/intents/%d/complete", task.ID, node.Index), map[string]any{
		"robotId": "robot-1", "taskRevision": node.TaskRevision, "aggregateVersion": node.AggregateVersion,
		"stepId": node.StepID, "commandId": node.CommandID, "fencingToken": node.FencingToken,
	}, false, true)
	defer exact.Body.Close()
	if exact.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(exact.Body)
		t.Fatalf("exact completion status=%d body=%s", exact.StatusCode, raw)
	}
}

func TestTaskDomainEventsExposeCoordinatorAuditLog(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	task := createTask(t, f, multiRobotPrompt, "mujoco")
	response := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/approve", nil, true, false)
	response.Body.Close()
	response = f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/intents/next", map[string]string{"robotId": "robot-1"}, false, true)
	response.Body.Close()

	response = f.do(t, http.MethodGet, "/v1/tasks/"+task.ID+"/domain-events", nil, true, false)
	defer response.Body.Close()
	var events []map[string]any
	if err := json.NewDecoder(response.Body).Decode(&events); err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK || len(events) != 2 || events[1]["eventType"] != "INTENT_CLAIMED" {
		t.Fatalf("status=%d events=%v", response.StatusCode, events)
	}
}

func TestRevisionEndpointsReturnPreviewHistoryAndExperience(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	task := createTask(t, f, "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "mujoco")
	proposal := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/revisions", map[string]any{
		"expectedRevision": 1, "request": "最后放到右侧蓝色垫子上",
		"idempotencyKey": "2e8cc3dd-43f9-4e59-a930-070c73bca111",
	}, true, false)
	defer proposal.Body.Close()
	var preview map[string]any
	if err := json.NewDecoder(proposal.Body).Decode(&preview); err != nil {
		t.Fatal(err)
	}
	if proposal.StatusCode != http.StatusCreated || preview["schemaVersion"] != "task.revision-preview.v1" {
		t.Fatalf("proposal status=%d body=%#v", proposal.StatusCode, preview)
	}

	historyResponse := f.do(t, http.MethodGet, "/v1/tasks/"+task.ID+"/revisions", nil, true, false)
	defer historyResponse.Body.Close()
	var history map[string]any
	if err := json.NewDecoder(historyResponse.Body).Decode(&history); err != nil {
		t.Fatal(err)
	}
	if historyResponse.StatusCode != http.StatusOK || history["schemaVersion"] != "task.revisions.v1" || len(history["revisions"].([]any)) != 2 {
		t.Fatalf("history status=%d body=%#v", historyResponse.StatusCode, history)
	}
	confirm := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/revisions/2/confirm", map[string]any{
		"expectedCurrentRevision": 1, "idempotencyKey": "2e8cc3dd-43f9-4e59-a930-070c73bca112",
	}, true, false)
	confirm.Body.Close()
	if confirm.StatusCode != http.StatusOK {
		t.Fatalf("confirm status=%d", confirm.StatusCode)
	}
	replay := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/revisions/2/confirm", map[string]any{
		"expectedCurrentRevision": 1, "idempotencyKey": "2e8cc3dd-43f9-4e59-a930-070c73bca112",
	}, true, false)
	replay.Body.Close()
	if replay.StatusCode != http.StatusOK {
		t.Fatalf("confirm replay status=%d", replay.StatusCode)
	}

	experienceResponse := f.do(t, http.MethodGet, "/v1/tasks/"+task.ID+"/experience", nil, true, false)
	defer experienceResponse.Body.Close()
	var experience tasks.TaskExperience
	if err := json.NewDecoder(experienceResponse.Body).Decode(&experience); err != nil {
		t.Fatal(err)
	}
	if experienceResponse.StatusCode != http.StatusOK || experience.SchemaVersion != "task.experience.v1" ||
		experience.TaskID != task.ID || experience.Revision != 2 {
		t.Fatalf("experience status=%d body=%#v", experienceResponse.StatusCode, experience)
	}
}

func TestRevisionEndpointReturnsCurrentTaskOnConflictAndRejectsDevice(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	task := createTask(t, f, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	conflict := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/revisions", map[string]any{
		"expectedRevision": 99, "request": "最后放到左侧目标区",
		"idempotencyKey": "2e8cc3dd-43f9-4e59-a930-070c73bca222",
	}, true, false)
	defer conflict.Body.Close()
	var payload map[string]any
	if err := json.NewDecoder(conflict.Body).Decode(&payload); err != nil {
		t.Fatal(err)
	}
	if conflict.StatusCode != http.StatusConflict || payload["code"] != "REVISION_CONFLICT" || payload["current"] == nil {
		t.Fatalf("conflict status=%d body=%#v", conflict.StatusCode, payload)
	}
	device := f.do(t, http.MethodGet, "/v1/tasks/"+task.ID+"/experience", nil, false, true)
	device.Body.Close()
	if device.StatusCode != http.StatusForbidden {
		t.Fatalf("device experience status=%d", device.StatusCode)
	}
}

func TestWorldWebSocketRejectsOriginWithHostAsSubstring(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	hub := worldhub.New("fleet-default", time.Minute, 8)
	authenticator, err := auth.New(auth.Options{
		OperatorUser: "admin", OperatorPass: "admin123", DeviceCredentials: map[string]string{"robot-1": "test-device-token-1"}, Secret: "secret",
	})
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(fleet.NewServer(service, nil,
		fleet.WithAuthenticator(authenticator), fleet.WithWorld(hub),
	).Handler())
	defer server.Close()
	operatorToken, _, _ := authenticator.Login(context.Background(), "admin", "admin123")
	ticketRequest, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/auth/ws-ticket", nil)
	ticketRequest.Header.Set("Authorization", "Bearer "+operatorToken)
	ticketResponse, err := http.DefaultClient.Do(ticketRequest)
	if err != nil {
		t.Fatal(err)
	}
	var ticket struct {
		Ticket string `json:"ticket"`
	}
	if err := json.NewDecoder(ticketResponse.Body).Decode(&ticket); err != nil {
		t.Fatal(err)
	}
	ticketResponse.Body.Close()
	header := http.Header{"Origin": []string{"http://evil-" + strings.TrimPrefix(server.URL, "http://") + ".example"}}
	wsURL := "ws" + strings.TrimPrefix(server.URL, "http") + "/v1/world/events/ws?ticket=" + ticket.Ticket
	if connection, _, dialErr := websocket.DefaultDialer.Dial(wsURL, header); dialErr == nil {
		connection.Close()
		t.Fatal("websocket accepted an origin whose host only contained the request host")
	}
}

const (
	multiRobotPrompt = "让1号机器人把红色杯子放进右侧收纳盒，然后让2号机器人把蓝色瓶子放进左侧收纳盒"
)

var testDeviceTokens = map[string]string{"robot-1": "test-device-token-1", "robot-2": "test-device-token-2"}

type testFleet struct {
	server      *httptest.Server
	router      *queue.Router
	service     *tasks.Service
	registry    *registry.Registry
	telemetry   fleettelemetry.Store
	coordinator *coordinator.Coordinator
}

func newTestFleet(t *testing.T) *testFleet {
	t.Helper()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	router := queue.NewRouter(200 * time.Millisecond)
	for _, robotID := range []string{"robot-1", "robot-2", queue.AnyRobot} {
		router.Add(robotID, memory.NewQueue[string](8))
	}
	authenticator, err := auth.New(auth.Options{
		OperatorUser: "admin", OperatorPass: "admin123", DeviceCredentials: testDeviceTokens,
	})
	if err != nil {
		t.Fatal(err)
	}
	deviceRegistry := registry.New(registry.NewMemoryStore())
	telemetryStore := fleettelemetry.NewMemoryStore()
	taskCoordinator := coordinator.New(service)
	handler := fleet.NewServer(service, router,
		fleet.WithAuthenticator(authenticator),
		fleet.WithRegistry(deviceRegistry),
		fleet.WithTelemetry(telemetryStore),
		fleet.WithCoordinator(taskCoordinator),
	).Handler()
	return &testFleet{
		server:      httptest.NewServer(handler),
		router:      router,
		service:     service,
		registry:    deviceRegistry,
		telemetry:   telemetryStore,
		coordinator: taskCoordinator,
	}
}

func (f *testFleet) close() { f.server.Close() }

func (f *testFleet) operatorToken(t *testing.T) string {
	t.Helper()
	body, err := json.Marshal(map[string]string{"user": "admin", "password": "admin123"})
	if err != nil {
		t.Fatal(err)
	}
	response, err := http.Post(f.server.URL+"/v1/auth/login", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("login status = %d", response.StatusCode)
	}
	var info auth.TokenInfo
	if err := json.NewDecoder(response.Body).Decode(&info); err != nil {
		t.Fatal(err)
	}
	if info.Token == "" {
		t.Fatal("login returned an empty token")
	}
	return info.Token
}

// do performs a request with the given auth mode and returns the response.
func (f *testFleet) do(t *testing.T, method, path string, body any, operator, device bool) *http.Response {
	t.Helper()
	var reader *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		reader = bytes.NewReader(raw)
	} else {
		reader = bytes.NewReader(nil)
	}
	request, err := http.NewRequest(method, f.server.URL+path, reader)
	if err != nil {
		t.Fatal(err)
	}
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	if operator {
		request.Header.Set("Authorization", "Bearer "+f.operatorToken(t))
	}
	if device {
		robotID := deviceRobotIDForTest(path, body)
		request.Header.Set("X-Robot-ID", robotID)
		request.Header.Set("X-Device-Token", testDeviceTokens[robotID])
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	return response
}

func deviceRobotIDForTest(path string, body any) string {
	if parsed, err := url.Parse(path); err == nil {
		if robotID := parsed.Query().Get("robot_id"); robotID != "" {
			return robotID
		}
	}
	switch value := body.(type) {
	case map[string]string:
		if value["robotId"] != "" {
			return value["robotId"]
		}
	case fleettelemetry.Sample:
		if value.RobotID != "" {
			return value.RobotID
		}
	}
	return "robot-1"
}

func decode[T any](t *testing.T, response *http.Response) T {
	t.Helper()
	defer response.Body.Close()
	var value T
	if err := json.NewDecoder(response.Body).Decode(&value); err != nil {
		t.Fatal(err)
	}
	return value
}

func createTask(t *testing.T, f *testFleet, request, adapter string) *tasks.Task {
	t.Helper()
	response := f.do(t, http.MethodPost, "/v1/tasks", map[string]string{"request": request, "adapter": adapter}, true, false)
	if response.StatusCode != http.StatusCreated {
		t.Fatalf("create status = %d", response.StatusCode)
	}
	task := decode[tasks.Task](t, response)
	return &task
}

func sampleGrid() *fleettelemetry.OccupancyGrid {
	grid := &fleettelemetry.OccupancyGrid{Width: 11, Height: 11, CellSize: 0.1, OriginX: -0.55, OriginY: -0.55}
	grid.Cells = make([]byte, 11*11)
	grid.Cells[5*11+5] = 100
	return grid
}

func TestHealthzIsPublic(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	response, err := http.Get(f.server.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("healthz status = %d", response.StatusCode)
	}
	var health map[string]string
	if err := json.NewDecoder(response.Body).Decode(&health); err != nil {
		t.Fatal(err)
	}
	if health["mode"] != "fleet" {
		t.Fatalf("health mode = %q", health["mode"])
	}
}

func TestDemoSessionEntersConsoleOnlyWhenExplicitlyEnabled(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	authenticator, err := auth.New(auth.Options{
		OperatorUser: "admin", OperatorPass: "admin123", AuthMode: "demo",
		DeviceCredentials: testDeviceTokens, Secret: "test-secret",
	})
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(fleet.NewServer(service, nil, fleet.WithAuthenticator(authenticator)).Handler())
	defer server.Close()

	healthResponse, err := http.Get(server.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	health := decode[map[string]string](t, healthResponse)
	if health["authMode"] != "demo" {
		t.Fatalf("health authMode=%q, want demo", health["authMode"])
	}

	response, err := http.Post(server.URL+"/v1/auth/demo-session", "application/json", nil)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK {
		t.Fatalf("demo session status=%d, want 200", response.StatusCode)
	}
	info := decode[auth.TokenInfo](t, response)
	if info.Operator != "demo-operator" || info.Token == "" {
		t.Fatalf("demo session=%#v", info)
	}
	request, _ := http.NewRequest(http.MethodGet, server.URL+"/v1/tasks", nil)
	request.Header.Set("Authorization", "Bearer "+info.Token)
	consoleResponse, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer consoleResponse.Body.Close()
	if consoleResponse.StatusCode != http.StatusOK {
		t.Fatalf("demo operator console status=%d, want 200", consoleResponse.StatusCode)
	}
}

func TestDemoSessionIsUnavailableInRequiredAuthMode(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	response, err := http.Post(f.server.URL+"/v1/auth/demo-session", "application/json", nil)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("required-mode demo session status=%d, want 404", response.StatusCode)
	}
}

func TestConsoleRoutesRequireOperatorToken(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	response := f.do(t, http.MethodGet, "/v1/tasks", nil, false, false)
	if response.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthenticated /v1/tasks status = %d, want 401", response.StatusCode)
	}
	response.Body.Close()

	response = f.do(t, http.MethodGet, "/v1/devices", nil, false, false)
	if response.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthenticated /v1/devices status = %d, want 401", response.StatusCode)
	}
	response.Body.Close()

	response = f.do(t, http.MethodGet, "/v1/tasks", nil, true, false)
	if response.StatusCode != http.StatusOK {
		t.Fatalf("operator /v1/tasks status = %d, want 200", response.StatusCode)
	}
	response.Body.Close()
}

func TestDeviceTokenCannotReachConsoleRoutes(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	response := f.do(t, http.MethodGet, "/v1/tasks", nil, false, true)
	if response.StatusCode != http.StatusForbidden {
		t.Fatalf("device token on console route status = %d, want 403", response.StatusCode)
	}
	response.Body.Close()
}

func TestDevicePrincipalCannotActAsAnotherRobot(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	task := createTask(t, f, multiRobotPrompt, "mujoco")
	approved := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/approve", nil, true, false)
	approved.Body.Close()

	body, _ := json.Marshal(map[string]string{"robotId": "robot-2"})
	request, _ := http.NewRequest(http.MethodPost, f.server.URL+"/v1/tasks/"+task.ID+"/intents/next", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("X-Robot-ID", "robot-1")
	request.Header.Set("X-Device-Token", testDeviceTokens["robot-1"])
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusForbidden {
		t.Fatalf("cross-robot claim status = %d, want 403", response.StatusCode)
	}
}

func TestOperatorTokenCannotCallRobotMutationRoute(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	request, _ := http.NewRequest(http.MethodPost, f.server.URL+"/v1/tasks/task-1/intents/next", bytes.NewReader([]byte(`{"robotId":"robot-1"}`)))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+f.operatorToken(t))
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusForbidden {
		t.Fatalf("operator device mutation status = %d, want 403", response.StatusCode)
	}
}

func TestCreateApproveEnqueuesToRobotQueue(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	task := createTask(t, f, "让1号机器人把红色杯子放进右侧收纳盒", "mujoco")
	if task.Intent.RobotID != "robot-1" {
		t.Fatalf("intent robot = %q, want robot-1", task.Intent.RobotID)
	}

	response := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/approve", nil, true, false)
	if response.StatusCode != http.StatusOK {
		t.Fatalf("approve status = %d", response.StatusCode)
	}
	response.Body.Close()

	// Only robot-1's queue received the task id.
	queued, err := f.router.Dequeue(context.Background(), "robot-1")
	if err != nil {
		t.Fatal(err)
	}
	if queued != task.ID {
		t.Fatalf("robot-1 queue = %q, want %q", queued, task.ID)
	}
	if other, _ := f.router.Dequeue(context.Background(), "robot-2"); other != "" {
		t.Fatalf("robot-2 queue should be empty, got %q", other)
	}
}

func TestCreateTaskAcceptsSimulationExecutionContext(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	response := f.do(t, http.MethodPost, "/v1/tasks", map[string]any{
		"request":          multiRobotPrompt,
		"adapter":          "robocasa",
		"executionContext": map[string]any{"mode": "simulation_demo", "newEpisode": true},
	}, true, false)
	if response.StatusCode != http.StatusCreated {
		body, _ := io.ReadAll(response.Body)
		response.Body.Close()
		t.Fatalf("create status=%d body=%s", response.StatusCode, body)
	}
	task := decode[tasks.Task](t, response)
	intents := task.Intent.Tasks()
	if !task.ExecutionContext.NewEpisode || len(intents) != 3 || intents[0].Action != "prepare_simulation" {
		t.Fatalf("task=%#v intents=%#v", task, intents)
	}
}

func TestMultiRobotCoordinatorFlow(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	task := createTask(t, f, multiRobotPrompt, "mujoco")
	intents := task.Intent.Tasks()
	if len(intents) != 2 {
		t.Fatalf("intents = %d, want 2", len(intents))
	}
	if intents[0].RobotID != "robot-1" || intents[1].RobotID != "robot-2" {
		t.Fatalf("robot assignment = %q, %q; want robot-1, robot-2", intents[0].RobotID, intents[1].RobotID)
	}

	response := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/approve", nil, true, false)
	response.Body.Close()

	// robot-2 cannot claim any intent before robot-1 finishes intent 0.
	claim := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/intents/next", map[string]string{"robotId": "robot-2"}, false, true)
	if claim.StatusCode != http.StatusOK {
		t.Fatalf("robot-2 claim status = %d", claim.StatusCode)
	}
	var none struct {
		Intent any `json:"intent"`
	}
	if err := json.NewDecoder(claim.Body).Decode(&none); err != nil {
		t.Fatal(err)
	}
	claim.Body.Close()
	if none.Intent != nil {
		t.Fatalf("robot-2 should not claim any intent before robot-1 finishes, got %v", none.Intent)
	}

	// robot-1 claims intent 0 and completes it.
	claim = f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/intents/next", map[string]string{"robotId": "robot-1"}, false, true)
	var first struct {
		Intent coordinator.IntentNode `json:"intent"`
	}
	if err := json.NewDecoder(claim.Body).Decode(&first); err != nil {
		t.Fatal(err)
	}
	claim.Body.Close()
	if first.Intent.Index != 0 || first.Intent.RobotID != "robot-1" {
		t.Fatalf("first claim = %+v, want index 0 robot-1", first.Intent)
	}
	complete := f.do(t, http.MethodPost, fmt.Sprintf("/v1/tasks/%s/intents/0/complete", task.ID), map[string]string{"robotId": "robot-1"}, false, true)
	if complete.StatusCode != http.StatusOK {
		t.Fatalf("complete status = %d", complete.StatusCode)
	}
	complete.Body.Close()

	// robot-2 claims intent 1 now (event-driven refresh) and completes it.
	claim = f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/intents/next", map[string]string{"robotId": "robot-2"}, false, true)
	var second struct {
		Intent coordinator.IntentNode `json:"intent"`
	}
	if err := json.NewDecoder(claim.Body).Decode(&second); err != nil {
		t.Fatal(err)
	}
	claim.Body.Close()
	if second.Intent.Index != 1 || second.Intent.RobotID != "robot-2" {
		t.Fatalf("second claim = %+v, want index 1 robot-2", second.Intent)
	}
	complete = f.do(t, http.MethodPost, fmt.Sprintf("/v1/tasks/%s/intents/1/complete", task.ID), map[string]string{"robotId": "robot-2"}, false, true)
	if complete.StatusCode != http.StatusOK {
		t.Fatalf("complete status = %d", complete.StatusCode)
	}
	complete.Body.Close()

	// Task-level state must now be SUCCEEDED.
	get := f.do(t, http.MethodGet, "/v1/tasks/"+task.ID, nil, true, false)
	final := decode[tasks.Task](t, get)
	if final.State != "SUCCEEDED" {
		t.Fatalf("task state = %q, want SUCCEEDED", final.State)
	}

	// Coordinator snapshot shows both intents succeeded.
	snapshot := f.do(t, http.MethodGet, "/v1/tasks/"+task.ID+"/intents", nil, true, false)
	var view struct {
		Intents []coordinator.IntentNode `json:"intents"`
		Robots  []string                 `json:"robots"`
	}
	if err := json.NewDecoder(snapshot.Body).Decode(&view); err != nil {
		t.Fatal(err)
	}
	snapshot.Body.Close()
	if len(view.Intents) != 2 || view.Intents[0].Status != coordinator.StatusSucceeded || view.Intents[1].Status != coordinator.StatusSucceeded {
		t.Fatalf("intent statuses = %+v", view.Intents)
	}
	if len(view.Robots) != 2 {
		t.Fatalf("robots = %v, want 2", view.Robots)
	}
}

func TestIntentFailureFailsTheTask(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	task := createTask(t, f, multiRobotPrompt, "mujoco")
	response := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/approve", nil, true, false)
	response.Body.Close()

	claim := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/intents/next", map[string]string{"robotId": "robot-1"}, false, true)
	claim.Body.Close()
	fail := f.do(t, http.MethodPost, fmt.Sprintf("/v1/tasks/%s/intents/0/fail", task.ID), map[string]string{"robotId": "robot-1", "reason": "boom"}, false, true)
	if fail.StatusCode != http.StatusOK {
		t.Fatalf("fail status = %d", fail.StatusCode)
	}
	fail.Body.Close()
	get := f.do(t, http.MethodGet, "/v1/tasks/"+task.ID, nil, true, false)
	final := decode[tasks.Task](t, get)
	if final.State != "FAILED" {
		t.Fatalf("task state = %q, want FAILED", final.State)
	}
}

func TestTelemetryIngestAndGlobalMapFusion(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	samples := []fleettelemetry.Sample{
		{
			RobotID: "robot-1", ObservedAt: time.Now().UTC(), Pose: []float64{0, 0, 0, 0},
			Activity:  "IDLE",
			Entities:  []fleettelemetry.Entity{{EntityID: "red-cup", Category: "cup", Pose: []float64{0.34, 0.49, 0.80}, Confidence: 0.9}},
			Occupancy: sampleGrid(),
		},
		{
			RobotID: "robot-2", ObservedAt: time.Now().UTC(), Pose: []float64{3, 0, 0, 0},
			Activity:  "EXECUTING",
			Entities:  []fleettelemetry.Entity{{EntityID: "blue-bottle", Category: "bottle", Pose: []float64{2.74, 0.52, 0.82}, Confidence: 0.95}},
			Occupancy: sampleGrid(),
		},
	}
	for _, sample := range samples {
		response := f.do(t, http.MethodPost, "/v1/telemetry", sample, false, true)
		if response.StatusCode != http.StatusAccepted {
			t.Fatalf("telemetry ingest status = %d", response.StatusCode)
		}
		response.Body.Close()
	}

	// Operator can read per-robot telemetry.
	latest := f.do(t, http.MethodGet, "/v1/telemetry?robot_id=robot-1", nil, true, false)
	var telemetryView struct {
		RobotID    string                  `json:"robotId"`
		HasLatest  bool                    `json:"hasLatest"`
		Latest     fleettelemetry.Sample   `json:"latest"`
		Trajectory []fleettelemetry.Sample `json:"trajectory"`
	}
	if err := json.NewDecoder(latest.Body).Decode(&telemetryView); err != nil {
		t.Fatal(err)
	}
	latest.Body.Close()
	if !telemetryView.HasLatest || telemetryView.Latest.RobotID != "robot-1" {
		t.Fatalf("telemetry view = %+v", telemetryView)
	}

	// Fused global map contains both robots, occupancy cells and entities.
	maps := f.do(t, http.MethodGet, "/v1/maps/global", nil, true, false)
	var global struct {
		Width    int     `json:"width"`
		Height   int     `json:"height"`
		CellSize float64 `json:"cellSizeM"`
		Cells    []byte  `json:"cells"`
		Robots   []struct {
			RobotID string    `json:"robotId"`
			Pose    []float64 `json:"pose"`
		} `json:"robots"`
		Entities []struct {
			EntityID string `json:"entityId"`
		} `json:"entities"`
	}
	if err := json.NewDecoder(maps.Body).Decode(&global); err != nil {
		t.Fatal(err)
	}
	maps.Body.Close()
	if len(global.Robots) != 2 {
		t.Fatalf("fused robots = %d, want 2", len(global.Robots))
	}
	if global.Width <= 0 || global.Height <= 0 || len(global.Cells) != global.Width*global.Height {
		t.Fatalf("invalid global grid %dx%d len=%d", global.Width, global.Height, len(global.Cells))
	}
	occupied := 0
	for _, cell := range global.Cells {
		if cell > 0 {
			occupied++
		}
	}
	if occupied == 0 {
		t.Fatal("global map has no occupied cells")
	}
	if len(global.Entities) < 2 {
		t.Fatalf("fused entities = %d, want >= 2", len(global.Entities))
	}
}

func TestDevicesListAndStatus(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	if _, err := f.registry.Register(context.Background(), registry.Device{RobotID: "robot-1", Adapter: "mujoco"}, 30*time.Second); err != nil {
		t.Fatal(err)
	}
	response := f.do(t, http.MethodGet, "/v1/devices", nil, true, false)
	var devices []registry.Device
	if err := json.NewDecoder(response.Body).Decode(&devices); err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if len(devices) != 1 || devices[0].RobotID != "robot-1" || !devices[0].Online {
		t.Fatalf("devices = %+v", devices)
	}
}

func TestDeviceQueueNextLongPoll(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	task := createTask(t, f, "让2号机器人把蓝色瓶子放进左侧收纳盒", "mujoco")
	response := f.do(t, http.MethodPost, "/v1/tasks/"+task.ID+"/approve", nil, true, false)
	response.Body.Close()

	// robot-2 pulls its own queue via the device data plane.
	response = f.do(t, http.MethodGet, "/v1/queue/next?robot_id=robot-2", nil, false, true)
	var pulled struct {
		TaskID string `json:"taskId"`
	}
	if err := json.NewDecoder(response.Body).Decode(&pulled); err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if pulled.TaskID != task.ID {
		t.Fatalf("pulled task = %q, want %q", pulled.TaskID, task.ID)
	}
}

func TestWebAppServedToAuthenticatedOperators(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	response := f.do(t, http.MethodGet, "/", nil, true, false)
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("web status = %d", response.StatusCode)
	}
	if !strings.Contains(response.Header.Get("Content-Type"), "text/html") {
		t.Fatalf("web content type = %q", response.Header.Get("Content-Type"))
	}
}

func TestSceneFramesAndWorldState(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	png := []byte{0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3}
	response := f.do(t, http.MethodPost, "/v1/telemetry", fleettelemetry.Sample{
		RobotID: "robot-1", ObservedAt: time.Now().UTC(), Pose: []float64{0, 0, 0, 0},
		Held: "red-cup", Placements: map[string]string{"red-cup": "right-bin"},
		Entities: []fleettelemetry.Entity{{EntityID: "red-cup", Category: "cup", Pose: []float64{0.3, 0.5, 0.8}}},
		Frame:    png, FrameMediaType: "image/png",
	}, false, true)
	response.Body.Close()

	// Frame list + raw bytes for operators.
	frames := f.do(t, http.MethodGet, "/v1/scene/frames", nil, true, false)
	var frameList struct {
		Frames []struct {
			RobotID   string `json:"robotId"`
			MediaType string `json:"mediaType"`
			Bytes     int    `json:"bytes"`
		} `json:"frames"`
	}
	if err := json.NewDecoder(frames.Body).Decode(&frameList); err != nil {
		t.Fatal(err)
	}
	frames.Body.Close()
	if len(frameList.Frames) != 1 || frameList.Frames[0].RobotID != "robot-1" || frameList.Frames[0].MediaType != "image/png" {
		t.Fatalf("frame list = %+v", frameList.Frames)
	}

	raw := f.do(t, http.MethodGet, "/v1/scene/frames/robot-1", nil, true, false)
	body, err := io.ReadAll(raw.Body)
	raw.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
	if raw.Header.Get("Content-Type") != "image/png" || len(body) != len(png) {
		t.Fatalf("frame bytes = %d (%s), want %d", len(body), raw.Header.Get("Content-Type"), len(png))
	}

	// Machine-readable harness world state.
	world := f.do(t, http.MethodGet, "/v1/world", nil, true, false)
	var worldState struct {
		Robots []struct {
			RobotID    string            `json:"robotId"`
			Held       string            `json:"held"`
			Placements map[string]string `json:"placements"`
			Activity   string            `json:"activity"`
		} `json:"robots"`
		Entities []struct {
			EntityID string `json:"entityId"`
		} `json:"entities"`
		Tasks []any `json:"tasks"`
	}
	if err := json.NewDecoder(world.Body).Decode(&worldState); err != nil {
		t.Fatal(err)
	}
	world.Body.Close()
	if len(worldState.Robots) != 1 || worldState.Robots[0].Held != "red-cup" {
		t.Fatalf("world robots = %+v", worldState.Robots)
	}
	if worldState.Robots[0].Placements["red-cup"] != "right-bin" {
		t.Fatalf("world placements = %v", worldState.Robots[0].Placements)
	}
	if len(worldState.Entities) == 0 {
		t.Fatal("world entities missing")
	}
}

func TestSensorCaptureMetadataAndConditionalBytes(t *testing.T) {
	f := newTestFleet(t)
	defer f.close()
	capture := fleetSensorCapture(9)
	if err := f.telemetry.Ingest(context.Background(), fleettelemetry.Sample{
		RobotID: "robot-1", Capture: capture,
	}); err != nil {
		t.Fatal(err)
	}
	if err := f.telemetry.CorrelateCapture(context.Background(), capture.CaptureID, 42); err != nil {
		t.Fatal(err)
	}

	metadata := f.do(t, http.MethodGet, "/v1/sensors/latest/robot-1", nil, true, false)
	view := decode[sensors.Capture](t, metadata)
	if view.WorldRevision != 42 || len(view.Frames) != 2 || view.Frames[0].URI == "" {
		t.Fatalf("view=%#v", view)
	}
	if len(view.Frames[0].Data) != 0 {
		t.Fatal("metadata response exposed raw frame bytes")
	}
	listed := f.do(t, http.MethodGet, "/v1/sensors/captures", nil, true, false)
	var index struct {
		Captures []sensors.Capture `json:"captures"`
	}
	if err := json.NewDecoder(listed.Body).Decode(&index); err != nil {
		t.Fatal(err)
	}
	listed.Body.Close()
	if len(index.Captures) != 1 || index.Captures[0].CaptureID != capture.CaptureID {
		t.Fatalf("capture index=%#v", index.Captures)
	}

	request, err := http.NewRequest(http.MethodGet, f.server.URL+view.Frames[0].URI, nil)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Authorization", "Bearer "+f.operatorToken(t))
	request.Header.Set("If-None-Match", `"`+view.Frames[0].SHA256+`"`)
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNotModified {
		t.Fatalf("conditional status=%d", response.StatusCode)
	}

	media := f.do(t, http.MethodGet, view.Frames[1].URI, nil, true, false)
	depth, err := io.ReadAll(media.Body)
	media.Body.Close()
	if err != nil || string(depth) != "depth-9" || media.Header.Get("ETag") != `"`+view.Frames[1].SHA256+`"` {
		t.Fatalf("depth=%q etag=%q err=%v", depth, media.Header.Get("ETag"), err)
	}
	denied := f.do(t, http.MethodGet, view.Frames[0].URI, nil, false, true)
	denied.Body.Close()
	if denied.StatusCode != http.StatusForbidden {
		t.Fatalf("device sensor read status=%d", denied.StatusCode)
	}

	// Compatibility live-frame route falls back to the latest RGB evidence
	// when an adapter did not publish a separate operator overview.
	legacy := f.do(t, http.MethodGet, "/v1/scene/frames/robot-1", nil, true, false)
	rgb, err := io.ReadAll(legacy.Body)
	legacy.Body.Close()
	if err != nil || string(rgb) != "rgb-9" {
		t.Fatalf("scene RGB alias=%q err=%v", rgb, err)
	}
}

func fleetSensorCapture(sequence uint64) *sensors.Capture {
	label := strconv.FormatUint(sequence, 10)
	rgb := []byte("rgb-" + label)
	depth := []byte("depth-" + label)
	return &sensors.Capture{
		SchemaVersion: sensors.SchemaVersionV1, CaptureID: "capture-" + label,
		RobotID: "robot-1", EpisodeID: "scene:2", SimulationStep: sequence * 4,
		SourceSequence: sequence, CapturedAt: time.Unix(int64(sequence), 0).UTC(),
		FrameID: "robot-1/rgbd_head", TransformRevision: "scene-v1",
		Frames: []sensors.Frame{
			{SensorID: "robot-1/rgbd_head", Modality: sensors.ModalityRGB, MediaType: "image/png", Width: 16, Height: 12,
				SHA256: fleetSensorDigest(rgb), Intrinsics: fleetSensorIdentity3(), CameraToWorld: fleetSensorIdentity4(), Data: rgb},
			{SensorID: "robot-1/rgbd_head", Modality: sensors.ModalityDepth, MediaType: "image/png;depth=uint16-mm", Width: 16, Height: 12,
				SHA256: fleetSensorDigest(depth), DepthScaleM: 0.001, MinRangeM: 0.05, MaxRangeM: 5,
				Intrinsics: fleetSensorIdentity3(), CameraToWorld: fleetSensorIdentity4(), Data: depth},
		},
	}
}

func fleetSensorDigest(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func fleetSensorIdentity3() []float64 { return []float64{1, 0, 0, 0, 1, 0, 0, 0, 1} }
func fleetSensorIdentity4() []float64 {
	return []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}
}
