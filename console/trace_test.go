package console_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestTraceRouteReturnsDurableOriginAndUnavailableEvidenceExplicitly(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	task, err := service.Create(context.Background(), "把红色杯子放进右侧收纳盒", "gazebo")
	if err != nil {
		t.Fatal(err)
	}
	executor := &executorSpy{}
	server := httptest.NewServer(console.NewServer(service, executor).Handler())
	defer server.Close()
	response, err := http.Get(server.URL + "/v1/tasks/" + task.ID + "/trace")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var trace tasks.Trace
	if err = json.NewDecoder(response.Body).Decode(&trace); err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK || response.Header.Get("Cache-Control") != "private, no-store" || trace.SchemaVersion != "task.trace.v1" || trace.OriginalRequest.Text != task.Request || trace.Integrity.Status != "incomplete" || trace.Source.EvidenceAvailable || !trace.Source.Stable {
		t.Fatalf("response=%+v trace=%+v", response, trace)
	}
	if len(executor.enqueued) != 0 || len(executor.cancelled) != 0 {
		t.Fatal("read-only trace touched executor")
	}
	missing, err := http.Get(server.URL + "/v1/tasks/missing/trace")
	if err != nil {
		t.Fatal(err)
	}
	defer missing.Body.Close()
	if missing.StatusCode != http.StatusNotFound {
		t.Fatalf("missing task status=%d", missing.StatusCode)
	}
}
