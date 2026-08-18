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

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

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
			Adapter: frame.adapter, Frame: frame.data, FrameMediaType: frame.mediaType,
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
		"object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'",
	} {
		if !strings.Contains(policy, directive) {
			t.Errorf("CSP %q missing %q", policy, directive)
		}
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
