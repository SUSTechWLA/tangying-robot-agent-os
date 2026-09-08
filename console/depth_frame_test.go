package console_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
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

func TestDepthFrameReturnsOnlyOriginalValidatedPNGWithCaptureHeaders(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	want, observedAt := encodedPNG(t), time.Now().Add(-100*time.Millisecond)
	service.PublishTelemetry(context.Background(), telemetry.Snapshot{Adapter: "mujoco", ObservedAt: observedAt, DepthFrame: want, DepthFrameMediaType: "image/png"})
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
	defer server.Close()
	response, err := http.Get(server.URL + "/v1/scene/depth?adapter=mujoco")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	data, _ := io.ReadAll(response.Body)
	if response.StatusCode != http.StatusOK || !bytes.Equal(data, want) || response.Header.Get("Content-Type") != "image/png" || response.Header.Get("Cache-Control") != "no-store" || response.Header.Get("X-Observed-At") != observedAt.UTC().Format(time.RFC3339Nano) || response.Header.Get("X-Frame-Age-Ms") == "" {
		t.Fatalf("status=%d headers=%v", response.StatusCode, response.Header)
	}
	assertStatus(t, server, http.MethodGet, "/v1/scene/frame?adapter=mujoco", "", http.StatusNotFound)
}

func TestDepthFrameRejectsNonPNGInvalidAndExpiredData(t *testing.T) {
	for _, test := range []struct {
		name   string
		data   []byte
		media  string
		at     time.Time
		status int
		code   string
	}{
		{"missing", nil, "image/png", time.Now(), 404, "SCENE_DEPTH_UNAVAILABLE"},
		{"jpeg", encodedJPEG(t), "image/jpeg", time.Now(), 415, "SCENE_DEPTH_UNSUPPORTED"},
		{"invalid", []byte("not PNG"), "image/png", time.Now(), 415, "SCENE_DEPTH_INVALID"},
		{"mismatch", encodedJPEG(t), "image/png", time.Now(), 415, "SCENE_DEPTH_INVALID"},
		{"missing-time", encodedPNG(t), "image/png", time.Time{}, 503, "SCENE_DEPTH_STALE"},
		{"expired", encodedPNG(t), "image/png", time.Now().Add(-time.Minute), 503, "SCENE_DEPTH_STALE"},
		{"future", encodedPNG(t), "image/png", time.Now().Add(time.Minute), 503, "SCENE_DEPTH_STALE"},
	} {
		t.Run(test.name, func(t *testing.T) {
			service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
			service.PublishTelemetry(context.Background(), telemetry.Snapshot{Adapter: "mujoco", ObservedAt: test.at, DepthFrame: test.data, DepthFrameMediaType: test.media})
			server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
			defer server.Close()
			response, err := http.Get(server.URL + "/v1/scene/depth?adapter=mujoco")
			if err != nil {
				t.Fatal(err)
			}
			defer response.Body.Close()
			var body map[string]string
			if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			if response.StatusCode != test.status || body["code"] != test.code {
				t.Fatalf("status=%d body=%v", response.StatusCode, body)
			}
		})
	}
}

func TestColorAndDepthHonorTheSameShorterDeclaredSensorBudget(t *testing.T) {
	service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	service.PublishTelemetry(context.Background(), telemetry.Snapshot{Adapter: "mujoco", ObservedAt: time.Now().Add(-time.Second), Frame: encodedPNG(t), FrameMediaType: "image/png", DepthFrame: encodedPNG(t), DepthFrameMediaType: "image/png", Reconstruction: &robotcontract.Reconstruction{SourceID: "rgbd"}, RobotProfile: &robotcontract.Profile{Sensors: []robotcontract.Sensor{{SourceID: "rgbd", MaxAgeMS: 500}}}})
	server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
	defer server.Close()
	for _, path := range []string{"frame", "depth"} {
		assertStatus(t, server, http.MethodGet, "/v1/scene/"+path+"?adapter=mujoco", "", http.StatusServiceUnavailable)
	}
}
