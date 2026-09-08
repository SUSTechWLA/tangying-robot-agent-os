package worker

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func TestLocalPublisherUsesRuntimeAdapterForLegacySourceProvenance(t *testing.T) {
	for _, adapter := range []string{"mujoco", "robocasa", "xlerobot_direct"} {
		worker := New(Config{RobotID: "robot-local", Adapter: "local-runtime", WorldID: "local-default", TransformRevision: "local-world-v1"})
		envelopes := worker.ObservationsFromTelemetry(telemetry.Snapshot{Adapter: adapter, ObservedAt: time.Now(), Entities: []telemetry.Entity{{EntityID: "cup"}}})
		want := observation.SourceSimGroundTruth
		if adapter == "xlerobot_direct" {
			want = observation.SourceRGBDCamera
		}
		if len(envelopes) != 2 || envelopes[1].SourceType != want || envelopes[1].Provenance.Adapter != adapter {
			t.Fatalf("adapter=%s envelopes=%+v", adapter, envelopes)
		}
	}
}
