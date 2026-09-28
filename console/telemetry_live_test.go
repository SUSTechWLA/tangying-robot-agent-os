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
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
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

func TestTelemetryDefaultsToSemanticStateAndRequiresExplicitGeometry(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	service.PublishTelemetry(context.Background(), telemetry.Snapshot{
		Adapter: "gazebo", RobotID: "robot-1", ObservedAt: time.Now(),
		Reconstruction: &robotcontract.Reconstruction{SourceID: "camera-1", Points: [][]float64{{1, 2, 3}}, PointColors: [][]int{{255, 0, 0}}},
		RobotState:     map[string]any{"base_pose": []any{1.0, 2.0, 0.0}, "imuSamples": []any{1.0, 2.0}, "pose_fusion_source": "imu_roll_pitch_odom_yaw", "simulation": true, "nested": map[string]any{"rgbImage": "raw", "ready": true}},
	})
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
	defer server.Close()
	for _, sample := range []struct {
		query    string
		geometry bool
	}{{"", false}, {"&detail=geometry", true}} {
		response, err := http.Get(server.URL + "/v1/telemetry?adapter=gazebo&history=false" + sample.query)
		if err != nil {
			t.Fatal(err)
		}
		var result struct {
			Latest struct {
				Reconstruction struct {
					Points [][]float64 `json:"points"`
				} `json:"reconstruction"`
				RobotState map[string]any `json:"robotState"`
			} `json:"latest"`
		}
		err = json.NewDecoder(response.Body).Decode(&result)
		response.Body.Close()
		if err != nil || response.StatusCode != http.StatusOK {
			t.Fatalf("query=%q status=%d error=%v", sample.query, response.StatusCode, err)
		}
		if (len(result.Latest.Reconstruction.Points) > 0) != sample.geometry {
			t.Fatalf("query=%q geometry leaked or omitted", sample.query)
		}
		state := result.Latest.RobotState
		if state["imuSamples"] != nil || state["pose_fusion_source"] != "imu_roll_pitch_odom_yaw" || state["simulation"] != true || state["base_pose"] == nil || state["nested"].(map[string]any)["rgbImage"] != nil {
			t.Fatalf("query=%q raw state leaked or fused state lost: %v", sample.query, state)
		}
	}
}
