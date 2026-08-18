package console_test

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
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
	service := orchestrator.NewService(orchestrator.NewMemoryStore(), intent.NewDeterministicParser())
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
	service := orchestrator.NewService(orchestrator.NewMemoryStore(), intent.NewDeterministicParser())
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

func newLocalTestServer(t *testing.T) (*httptest.Server, *executorSpy) {
	t.Helper()
	service := orchestrator.NewService(orchestrator.NewMemoryStore(), intent.NewDeterministicParser())
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
