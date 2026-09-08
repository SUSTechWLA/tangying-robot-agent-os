package console_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/agent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/localapp"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/memory"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware/sqlite"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestLocalRecoveryRoutesPauseAndFailClosedForUnknownPhysicalOutcome(t *testing.T) {
	store, err := sqlite.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	service := tasks.NewService(store, intent.NewDeterministicParser())
	app := localapp.New(service, agent.NewRunner(store, nil, nil), memory.NewQueue[string](64))
	server := httptest.NewServer(console.NewServer(service, app).Handler())
	defer server.Close()
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "mujoco")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := service.Approve(context.Background(), task.ID); err != nil {
		t.Fatal(err)
	}
	assertStatus(t, server, http.MethodPost, "/v1/tasks/"+task.ID+"/pause", "", http.StatusOK)
	response, err := http.Get(server.URL + "/v1/tasks/" + task.ID + "/recovery")
	if err != nil {
		t.Fatal(err)
	}
	var view tasks.LocalRecovery
	if err := json.NewDecoder(response.Body).Decode(&view); err != nil {
		response.Body.Close()
		t.Fatal(err)
	}
	response.Body.Close()
	if view.TaskID != task.ID || view.State != taskgraph.StatePaused || !view.CanResume || view.RequiresReconciliation {
		t.Fatalf("view=%+v", view)
	}
	if err := store.MarkStepStarted(context.Background(), middleware.StepRecord{TaskID: task.ID, StepID: "pick", IdempotencyKey: "legacy-uncertain"}); err != nil {
		t.Fatal(err)
	}
	response, err = http.Post(server.URL+"/v1/tasks/"+task.ID+"/resume", "application/json", nil)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var result map[string]any
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusConflict || result["code"] != "PHYSICAL_OUTCOME_UNKNOWN" {
		t.Fatalf("status=%d result=%v", response.StatusCode, result)
	}
	assertStatus(t, server, http.MethodGet, "/v1/tasks/missing/recovery", "", http.StatusNotFound)
}
