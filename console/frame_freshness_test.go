package console_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/console"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

func TestColorFrameNeverTreatsMissingStaleOrFutureCaptureTimeAsLive(t *testing.T) {
	for name, observedAt := range map[string]time.Time{"missing": {}, "stale": time.Now().Add(-time.Minute), "future": time.Now().Add(time.Minute)} {
		t.Run(name, func(t *testing.T) {
			service := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
			service.PublishTelemetry(context.Background(), telemetry.Snapshot{Adapter: "mujoco", ObservedAt: observedAt, Frame: encodedPNG(t), FrameMediaType: "image/png"})
			server := httptest.NewServer(console.NewServer(service, &executorSpy{}).Handler())
			defer server.Close()
			assertStatus(t, server, http.MethodGet, "/v1/scene/frame?adapter=mujoco", "", http.StatusServiceUnavailable)
		})
	}
}
