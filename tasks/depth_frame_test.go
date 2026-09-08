package tasks

import (
	"bytes"
	"encoding/json"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func TestDepthFramesAreOwnedSeparatelyAndNeverLeakIntoTelemetryJSON(t *testing.T) {
	hub := NewTelemetryHub()
	want := telemetryTestPNG(t)
	snapshot := telemetry.Snapshot{Adapter: "mujoco", ObservedAt: time.Now(), DepthFrame: append([]byte(nil), want...), DepthFrameMediaType: "image/png"}
	hub.Publish(snapshot)
	snapshot.DepthFrame[0] = 'X'
	frame, ok := hub.LatestDepthFrame("mujoco")
	if !ok || !bytes.Equal(frame.Data, want) || frame.ObservedAt != snapshot.ObservedAt {
		t.Fatalf("frame=%+v ok=%v", frame, ok)
	}
	frame.Data[0] = 'Y'
	again, _ := hub.LatestDepthFrame("mujoco")
	if !bytes.Equal(again.Data, want) {
		t.Fatal("depth frame aliases reader")
	}
	latest, _ := hub.Latest("mujoco")
	if latest.DepthFrame != nil || latest.DepthFrameMediaType != "" {
		t.Fatal("depth bytes remained in snapshot")
	}
	if !latest.DepthFrameAvailable || latest.ColorFrameAvailable {
		t.Fatal("availability was not derived from validated bytes")
	}
	encoded, err := json.Marshal(hub.History("mujoco", 1))
	if err != nil || bytes.Contains(encoded, []byte(`"depthFrame":`)) {
		t.Fatalf("metadata=%s err=%v", encoded, err)
	}
	hub.Publish(telemetry.Snapshot{Adapter: "mujoco", ObservedAt: time.Now(), Frame: want, FrameMediaType: "image/png"})
	if _, ok := hub.LatestDepthFrame("mujoco"); ok {
		t.Fatal("missing newest depth kept old cached depth")
	}
	if _, ok := hub.LatestFrame("mujoco"); !ok {
		t.Fatal("missing depth invalidated available color")
	}
}

func TestFrameAvailabilityRejectsSpoofedFlagsAndExpiredCaptures(t *testing.T) {
	hub := NewTelemetryHub()
	hub.Publish(telemetry.Snapshot{Adapter: "mujoco", ColorFrameAvailable: true, DepthFrameAvailable: true})
	latest, _ := hub.Latest("mujoco")
	if latest.ColorFrameAvailable || latest.DepthFrameAvailable {
		t.Fatal("trusted self-reported frame flags")
	}
	hub.Publish(telemetry.Snapshot{Adapter: "mujoco", ObservedAt: time.Now().Add(-time.Minute), Frame: telemetryTestPNG(t), FrameMediaType: "image/png", DepthFrame: telemetryTestPNG(t), DepthFrameMediaType: "image/png"})
	latest, _ = hub.Latest("mujoco")
	if latest.ColorFrameAvailable || latest.DepthFrameAvailable {
		t.Fatal("expired capture reported available")
	}
}
