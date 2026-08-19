package fleet_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestFleetServerCreatesAndApprovesDistributedTask(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	queue := memory.NewQueue[string](8)
	server := httptest.NewServer(fleet.NewServer(service, queue).Handler())
	defer server.Close()

	response, err := http.Post(server.URL+"/v1/tasks", "application/json", bytes.NewBufferString(`{"request":"把红色杯子放进右侧收纳盒","adapter":"mujoco"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		t.Fatalf("create status = %d", response.StatusCode)
	}
	var task tasks.Task
	if err := json.NewDecoder(response.Body).Decode(&task); err != nil {
		t.Fatal(err)
	}

	approve, err := http.Post(server.URL+"/v1/tasks/"+task.ID+"/approve", "application/json", nil)
	if err != nil {
		t.Fatal(err)
	}
	approve.Body.Close()
	if approve.StatusCode != http.StatusOK {
		t.Fatalf("approve status = %d", approve.StatusCode)
	}
	queued, err := queue.Dequeue(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if queued != task.ID {
		t.Fatalf("queued task = %q, want %q", queued, task.ID)
	}
}
