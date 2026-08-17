package api_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/api"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/gorilla/websocket"
)

func TestCreateAndReadTask(t *testing.T) {
	service := orchestrator.NewService(orchestrator.NewMemoryStore(), intent.NewDeterministicParser())
	server := httptest.NewServer(api.NewServer(service).Handler())
	defer server.Close()

	body := bytes.NewBufferString(`{"request":"把红色杯子放进右侧收纳盒","adapter":"mujoco"}`)
	response, err := http.Post(server.URL+"/v1/tasks", "application/json", body)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		t.Fatalf("status = %d", response.StatusCode)
	}
	var created orchestrator.Task
	if err := json.NewDecoder(response.Body).Decode(&created); err != nil {
		t.Fatal(err)
	}
	got, err := service.Get(context.Background(), created.ID)
	if err != nil || got.Intent.Object.Attributes["color"] != "red" {
		t.Fatalf("task = %+v, err = %v", got, err)
	}
}

func TestTaskEventsWebSocketStartsWithPersistedEvents(t *testing.T) {
	service := orchestrator.NewService(orchestrator.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(api.NewServer(service).Handler())
	defer server.Close()

	url := "ws" + server.URL[len("http"):] + "/v1/tasks/" + task.ID + "/events/ws"
	connection, response, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		if response != nil {
			t.Fatalf("websocket status = %d, err = %v", response.StatusCode, err)
		}
		t.Fatal(err)
	}
	defer connection.Close()
	_ = connection.SetReadDeadline(time.Now().Add(time.Second))
	var event orchestrator.TaskEvent
	if err := connection.ReadJSON(&event); err != nil {
		t.Fatal(err)
	}
	if event.Type != "TASK_CREATED" || event.Sequence != 1 {
		t.Fatalf("event = %+v", event)
	}
}

func TestOperatorFlowServesConsoleAndApprovesTask(t *testing.T) {
	service := orchestrator.NewService(orchestrator.NewMemoryStore(), intent.NewDeterministicParser())
	server := httptest.NewServer(api.NewServer(service).Handler())
	defer server.Close()

	response, err := http.Get(server.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK || response.Header.Get("Content-Type") == "application/json" {
		t.Fatalf("console response status=%d content-type=%q", response.StatusCode, response.Header.Get("Content-Type"))
	}

	task, _ := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	request, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/tasks/"+task.ID+"/approve", nil)
	approved, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer approved.Body.Close()
	updated, _ := service.Get(context.Background(), task.ID)
	if approved.StatusCode != http.StatusOK || !updated.Approved {
		t.Fatalf("approve status=%d task=%+v", approved.StatusCode, updated)
	}
}
