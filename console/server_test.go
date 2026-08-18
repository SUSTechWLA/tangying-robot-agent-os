package console_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
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
	service.PublishTelemetry(context.Background(), telemetry.Snapshot{
		Adapter: "mujoco", Frame: []byte("png"), FrameMediaType: "image/png",
	})
	service.PublishTelemetry(context.Background(), telemetry.Snapshot{
		Adapter: "xlerobot_direct", Frame: []byte("jpeg"), FrameMediaType: "image/jpeg",
	})
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
	defer server.Close()

	response, err := http.Get(server.URL + "/v1/scene/frame?adapter=mujoco")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, _ := io.ReadAll(response.Body)
	if response.StatusCode != http.StatusOK || response.Header.Get("Content-Type") != "image/png" {
		t.Fatalf("status = %d media type = %q body = %q", response.StatusCode, response.Header.Get("Content-Type"), body)
	}
	if response.Header.Get("Cache-Control") != "no-store" || string(body) != "png" {
		t.Fatalf("cache = %q body = %q", response.Header.Get("Cache-Control"), body)
	}
}

func TestSceneFrameRejectsMissingOrUnavailableAdapter(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	service.PublishTelemetry(context.Background(), telemetry.Snapshot{
		Adapter: "mujoco", Frame: []byte("png"), FrameMediaType: "image/png",
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
