package console_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestLiveTelemetryCanOmitHistoryWithoutDiscardingIt(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	for i := 0; i < 3; i++ {
		service.PublishTelemetry(context.Background(), telemetry.Snapshot{Adapter: "gazebo", RobotID: "camera-robot", ObservedAt: time.Now()})
	}
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
	defer server.Close()
	for _, sample := range []struct {
		query string
		count int
	}{{"&history=false", 0}, {"&limit=2", 2}, {"", 3}} {
		response, err := http.Get(server.URL + "/v1/telemetry?adapter=gazebo" + sample.query)
		if err != nil {
			t.Fatal(err)
		}
		var result struct {
			HasLatest bool                 `json:"hasLatest"`
			Latest    telemetry.Snapshot   `json:"latest"`
			History   []telemetry.Snapshot `json:"history"`
		}
		err = json.NewDecoder(response.Body).Decode(&result)
		response.Body.Close()
		if err != nil || response.StatusCode != http.StatusOK || !result.HasLatest || result.Latest.RobotID != "camera-robot" || len(result.History) != sample.count {
			t.Fatalf("query=%s status=%d history=%d latest=%v error=%v", sample.query, response.StatusCode, len(result.History), result.HasLatest, err)
		}
	}
}
