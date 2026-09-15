package console_test

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/worldhub"
	"github.com/SUSTechWLA/tangying-robot-agent-os/latency"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestLocalAndFleetWorldUseTheSameSchemaVersion(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	hub := worldhub.New("local-default", time.Minute, 8)
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}, console.WithWorld(hub)).Handler())
	defer server.Close()

	response, err := http.Get(server.URL + "/v1/world")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var snapshot worldmodel.Snapshot
	if err := json.NewDecoder(response.Body).Decode(&snapshot); err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK || snapshot.SchemaVersion != worldmodel.SchemaVersion || snapshot.WorldID != "local-default" {
		t.Fatalf("status=%d snapshot=%#v", response.StatusCode, snapshot)
	}
}

type settingsStub struct {
	status console.ConfigStatus
}

func (s *settingsStub) Status() console.ConfigStatus { return s.status }

func (s *settingsStub) UpdateLLM(console.LLMConfig) error { return nil }

type executorSpy struct {
	enqueued  []string
	cancelled []string
}

func (s *executorSpy) Enqueue(taskID string) error {
	s.enqueued = append(s.enqueued, taskID)
	return nil
}

func (s *executorSpy) Cancel(taskID string) error {
	s.cancelled = append(s.cancelled, taskID)
	return nil
}

func TestLocalRoutesExcludeDistributedControl(t *testing.T) {
	server, _ := newLocalTestServer(t)
	assertStatus(t, server, http.MethodGet, "/healthz", "", http.StatusOK)
	assertStatus(t, server, http.MethodGet, "/v1/tasks", "", http.StatusOK)
	assertStatus(t, server, http.MethodPost, "/v1/agents/laptop/claim", "", http.StatusMethodNotAllowed)
	assertStatus(t, server, http.MethodPost, "/v1/leases/lease-1/renew", `{}`, http.StatusMethodNotAllowed)
	assertStatus(t, server, http.MethodPost, "/v1/telemetry", `{}`, http.StatusMethodNotAllowed)
}

func TestApprovalEnqueuesTaskInLocalExecutor(t *testing.T) {
	server, executor := newLocalTestServer(t)
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	executor = &executorSpy{}
	server = httptest.NewServer(console.NewServer(service, executor).Handler())
	t.Cleanup(server.Close)
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	assertStatus(t, server, http.MethodPost, "/v1/tasks/"+task.ID+"/approve", "", http.StatusOK)
	if len(executor.enqueued) != 1 || executor.enqueued[0] != task.ID {
		t.Fatalf("enqueued = %#v", executor.enqueued)
	}
	approved, err := service.Get(context.Background(), task.ID)
	if err != nil || !approved.Approved {
		t.Fatalf("approved task = %#v, err = %v", approved, err)
	}
}

func TestLocalRevisionEndpointsMatchFleetExperienceContract(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
	defer server.Close()
	task, err := service.Create(context.Background(), "让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	body := `{"expectedRevision":1,"request":"最后放到右侧蓝色垫子上","idempotencyKey":"2e8cc3dd-43f9-4e59-a930-070c73bca333"}`
	request, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/tasks/"+task.ID+"/revisions", strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	var preview map[string]any
	if err := json.NewDecoder(response.Body).Decode(&preview); err != nil {
		response.Body.Close()
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusCreated || preview["schemaVersion"] != "task.revision-preview.v1" {
		t.Fatalf("status=%d preview=%#v", response.StatusCode, preview)
	}
	confirmBody := `{"expectedCurrentRevision":1,"idempotencyKey":"2e8cc3dd-43f9-4e59-a930-070c73bca334"}`
	confirm, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/tasks/"+task.ID+"/revisions/2/confirm", strings.NewReader(confirmBody))
	confirm.Header.Set("Content-Type", "application/json")
	confirmed, err := http.DefaultClient.Do(confirm)
	if err != nil {
		t.Fatal(err)
	}
	confirmed.Body.Close()
	if confirmed.StatusCode != http.StatusOK {
		t.Fatalf("confirm status=%d", confirmed.StatusCode)
	}
	for path, schema := range map[string]string{
		"/v1/tasks/" + task.ID + "/revisions":  "task.revisions.v1",
		"/v1/tasks/" + task.ID + "/experience": "task.experience.v1",
	} {
		result, err := http.Get(server.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		var value map[string]any
		if err := json.NewDecoder(result.Body).Decode(&value); err != nil {
			result.Body.Close()
			t.Fatal(err)
		}
		result.Body.Close()
		if result.StatusCode != http.StatusOK || value["schemaVersion"] != schema {
			t.Fatalf("path=%s status=%d value=%#v", path, result.StatusCode, value)
		}
	}
}

func TestConfigStatusNeverReturnsAPIKey(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	settings := &settingsStub{status: console.ConfigStatus{
		Provider: "openai", BaseURL: "https://llm.example/v1", Model: "robot-model", HasAPIKey: true,
	}}
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}, console.WithSettings(settings)).Handler())
	defer server.Close()
	response, err := http.Get(server.URL + "/v1/config/status")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body := new(bytes.Buffer)
	_, _ = body.ReadFrom(response.Body)
	if response.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", response.StatusCode)
	}
	if bytes.Contains(body.Bytes(), []byte("secret")) || bytes.Contains(body.Bytes(), []byte("apiKey\"")) {
		t.Fatalf("configuration response leaked secret: %s", body.String())
	}
	if !bytes.Contains(body.Bytes(), []byte(`"hasApiKey":true`)) {
		t.Fatalf("configuration status = %s", body.String())
	}
}

func TestSceneFrameReturnsLatestImageForRequestedAdapter(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	frames := []struct {
		adapter   string
		mediaType string
		data      []byte
	}{
		{adapter: "mujoco", mediaType: "image/png", data: encodedPNG(t)},
		{adapter: "xlerobot_direct", mediaType: "image/jpeg", data: encodedJPEG(t)},
		{adapter: "xlerobot_ros2", mediaType: "image/webp", data: encodedWebP(t)},
	}
	for _, frame := range frames {
		service.PublishTelemetry(context.Background(), telemetry.Snapshot{
			Adapter: frame.adapter, ObservedAt: time.Now(), Frame: frame.data, FrameMediaType: frame.mediaType,
		})
	}
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
	defer server.Close()

	for _, frame := range frames {
		response, err := http.Get(server.URL + "/v1/scene/frame?adapter=" + frame.adapter)
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(response.Body)
		response.Body.Close()
		if response.StatusCode != http.StatusOK || response.Header.Get("Content-Type") != frame.mediaType {
			t.Fatalf("adapter=%s status=%d media type=%q body=%q", frame.adapter, response.StatusCode, response.Header.Get("Content-Type"), body)
		}
		if response.Header.Get("Cache-Control") != "no-store" || !bytes.Equal(body, frame.data) {
			t.Fatalf("adapter=%s cache=%q body changed=%v", frame.adapter, response.Header.Get("Cache-Control"), !bytes.Equal(body, frame.data))
		}
	}
}

func TestSceneFrameRejectsUnsupportedMalformedAndMismatchedImages(t *testing.T) {
	cases := []struct {
		name      string
		mediaType string
		data      []byte
		code      string
	}{
		{name: "html-as-png", mediaType: "image/png", data: []byte("<!doctype html><h1>no</h1>"), code: "SCENE_FRAME_INVALID"},
		{name: "svg", mediaType: "image/svg+xml", data: []byte(`<svg xmlns="http://www.w3.org/2000/svg"/>`), code: "SCENE_FRAME_UNSUPPORTED"},
		{name: "png-declared-jpeg", mediaType: "image/jpeg", data: encodedPNG(t), code: "SCENE_FRAME_INVALID"},
		{name: "truncated-webp", mediaType: "image/webp", data: encodedWebP(t)[:20], code: "SCENE_FRAME_INVALID"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
			service.PublishTelemetry(context.Background(), telemetry.Snapshot{
				Adapter: "mujoco", Frame: test.data, FrameMediaType: test.mediaType,
			})
			server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
			defer server.Close()

			response, err := http.Get(server.URL + "/v1/scene/frame?adapter=mujoco")
			if err != nil {
				t.Fatal(err)
			}
			var body map[string]string
			if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
				response.Body.Close()
				t.Fatal(err)
			}
			response.Body.Close()
			if response.StatusCode != http.StatusUnsupportedMediaType || body["code"] != test.code || body["message"] == "" {
				t.Fatalf("status=%d body=%#v", response.StatusCode, body)
			}
		})
	}
}

func TestSceneFrameRejectsMissingOrUnavailableAdapter(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	service.PublishTelemetry(context.Background(), telemetry.Snapshot{
		Adapter: "mujoco", Frame: encodedPNG(t), FrameMediaType: "image/png",
	})
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
	defer server.Close()

	for _, query := range []string{"", "?adapter=xlerobot_direct"} {
		response, err := http.Get(server.URL + "/v1/scene/frame" + query)
		if err != nil {
			t.Fatal(err)
		}
		var body map[string]string
		if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
			response.Body.Close()
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusNotFound || body["code"] != "SCENE_FRAME_UNAVAILABLE" {
			t.Fatalf("query %q status = %d body = %#v", query, response.StatusCode, body)
		}
	}
}

func TestSceneFrameDoesNotServeLastGoodFrameAfterNewestSnapshotHasNoUsableFrame(t *testing.T) {
	cases := []telemetry.Snapshot{
		{Adapter: "mujoco"},
		{Adapter: "mujoco", Frame: encodedPNG(t)},
		{Adapter: "mujoco", Frame: make([]byte, tasks.MaxSceneFrameBytes+1), FrameMediaType: "image/png"},
	}
	for index, unusable := range cases {
		service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
		service.PublishTelemetry(context.Background(), telemetry.Snapshot{
			Adapter: "mujoco", Frame: encodedPNG(t), FrameMediaType: "image/png",
		})
		service.PublishTelemetry(context.Background(), unusable)
		server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())

		response, err := http.Get(server.URL + "/v1/scene/frame?adapter=mujoco")
		if err != nil {
			server.Close()
			t.Fatal(err)
		}
		var body map[string]string
		if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
			response.Body.Close()
			server.Close()
			t.Fatal(err)
		}
		response.Body.Close()
		server.Close()
		if response.StatusCode != http.StatusNotFound || body["code"] != "SCENE_FRAME_UNAVAILABLE" {
			t.Fatalf("case=%d status=%d body=%#v", index, response.StatusCode, body)
		}
	}
}

func TestConsoleResponsesSetRestrictiveContentSecurityPolicy(t *testing.T) {
	server, _ := newLocalTestServer(t)
	response, err := http.Get(server.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	policy := response.Header.Get("Content-Security-Policy")
	for _, directive := range []string{
		"default-src 'self'", "script-src 'self'", "img-src 'self' blob:",
		"connect-src 'self' blob: ws: wss:",
		"object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'",
	} {
		if !strings.Contains(policy, directive) {
			t.Errorf("CSP %q missing %q", policy, directive)
		}
	}
}

func TestConsoleServesImmutableGLBWithRestrictiveContentSecurityPolicy(t *testing.T) {
	server, _ := newLocalTestServer(t)
	response, err := http.Get(server.URL + "/assets/scenes/robocasa-handoff-v1/xlerobot.glb?v=" + strings.Repeat("a", 64))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()

	if response.StatusCode != http.StatusOK || response.Header.Get("Content-Type") != "model/gltf-binary" {
		t.Fatalf("status=%d content-type=%q", response.StatusCode, response.Header.Get("Content-Type"))
	}
	if !strings.Contains(response.Header.Get("Cache-Control"), "immutable") {
		t.Fatalf("cache=%q, want immutable", response.Header.Get("Cache-Control"))
	}
	if policy := response.Header.Get("Content-Security-Policy"); !strings.Contains(policy, "default-src 'self'") || !strings.Contains(policy, "script-src 'self'") {
		t.Fatalf("CSP=%q", policy)
	}
}

func encodedPNG(t *testing.T) []byte {
	t.Helper()
	var output bytes.Buffer
	if err := png.Encode(&output, image.NewNRGBA(image.Rect(0, 0, 1, 1))); err != nil {
		t.Fatal(err)
	}
	return output.Bytes()
}

func encodedJPEG(t *testing.T) []byte {
	t.Helper()
	var output bytes.Buffer
	pixel := image.NewRGBA(image.Rect(0, 0, 1, 1))
	pixel.Set(0, 0, color.RGBA{R: 20, G: 40, B: 60, A: 255})
	if err := jpeg.Encode(&output, pixel, nil); err != nil {
		t.Fatal(err)
	}
	return output.Bytes()
}

func encodedWebP(t *testing.T) []byte {
	t.Helper()
	frame, err := base64.StdEncoding.DecodeString("UklGRh4AAABXRUJQVlA4TBEAAAAvAAAAAAfQ//73v/+BiOh/AAA=")
	if err != nil {
		t.Fatal(err)
	}
	return frame
}

func newLocalTestServer(t *testing.T) (*httptest.Server, *executorSpy) {
	t.Helper()
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	executor := &executorSpy{}
	server := httptest.NewServer(console.NewServer(service, executor).Handler())
	t.Cleanup(server.Close)
	return server, executor
}

func assertStatus(t *testing.T, server *httptest.Server, method, path, body string, expected int) {
	t.Helper()
	request, err := http.NewRequest(method, server.URL+path, bytes.NewBufferString(body))
	if err != nil {
		t.Fatal(err)
	}
	if body != "" {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != expected {
		t.Fatalf("%s %s status = %d, want %d", method, path, response.StatusCode, expected)
	}
}

// ── step latency endpoint ───────────────────────────────────────────────────
// The console used to derive durations from task CreatedAt/UpdatedAt, which can
// show a number but cannot name the slow phase. These cases hold the two things
// that make the new endpoint trustworthy: it refuses when nothing is measured
// (instead of reporting zeros), and it refuses an unsupported grouping instead
// of quietly answering a different question.

func TestStepLatencyRefusesWhenNothingIsMeasured(t *testing.T) {
	server := console.NewServer(nil, nil)
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, httptest.NewRequest("GET", "/v1/telemetry/latency", nil))
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503: unmeasured must not look like instant", recorder.Code)
	}
	if !strings.Contains(recorder.Body.String(), "LATENCY_UNAVAILABLE") {
		t.Fatalf("body = %s", recorder.Body.String())
	}
}

func TestStepLatencyReportsPhasesGroupedByCapability(t *testing.T) {
	timings := latency.New(64)
	now := time.Now()
	timings.Record(latency.Sample{At: now, StepID: "navigate_01", Capability: "navigation.navigate",
		SafetyLevel: "PHYSICAL", RobotID: "robot-local", Outcome: latency.OutcomeCompleted,
		Queue: 5 * time.Millisecond, Admission: time.Millisecond, Execute: 800 * time.Millisecond,
		Verify: 2 * time.Millisecond})
	timings.Record(latency.Sample{At: now, StepID: "observe", Capability: "observe_scene",
		SafetyLevel: "READ", RobotID: "robot-local", Outcome: latency.OutcomeCompleted,
		Execute: 40 * time.Millisecond})
	server := console.NewServer(nil, nil, console.WithLatency(timings))

	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder,
		httptest.NewRequest("GET", "/v1/telemetry/latency?groupBy=capability&windowMs=60000", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", recorder.Code, recorder.Body.String())
	}
	var report latency.Report
	if err := json.Unmarshal(recorder.Body.Bytes(), &report); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if report.Count != 2 || len(report.Groups) != 2 {
		t.Fatalf("report = %#v", report)
	}
	byKey := map[string]latency.Group{}
	for _, group := range report.Groups {
		byKey[group.Key] = group
	}
	if byKey["navigation.navigate"].Phases[latency.Execute].Max != 800 {
		t.Fatalf("execute phase missing: %#v", byKey["navigation.navigate"])
	}
	if byKey["navigation.navigate"].Phases[latency.Queue].Max != 5 {
		t.Fatalf("queue phase missing: %#v", byKey["navigation.navigate"])
	}
	if byKey["navigation.navigate"].SlowestStepID != "navigate_01" {
		t.Fatalf("slowest step not named: %#v", byKey["navigation.navigate"])
	}
}

func TestStepLatencyRejectsAnUnknownGroupingAndAnAbsurdWindow(t *testing.T) {
	server := console.NewServer(nil, nil, console.WithLatency(latency.New(8)))
	for _, target := range []string{
		"/v1/telemetry/latency?groupBy=task",
		"/v1/telemetry/latency?windowMs=-1",
		"/v1/telemetry/latency?windowMs=999999999999",
		"/v1/telemetry/latency?windowMs=abc",
	} {
		recorder := httptest.NewRecorder()
		server.Handler().ServeHTTP(recorder, httptest.NewRequest("GET", target, nil))
		if recorder.Code != http.StatusBadRequest {
			t.Fatalf("%s: status = %d, want 400", target, recorder.Code)
		}
	}
	// Zero is meaningful: everything still retained.
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, httptest.NewRequest("GET", "/v1/telemetry/latency?windowMs=0", nil))
	if recorder.Code != http.StatusOK {
		t.Fatalf("a zero window must mean everything retained, got %d", recorder.Code)
	}
}
