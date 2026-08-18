package tasks

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func TestTelemetryHubOwnsFrameBytesAndOmitsThemFromJSON(t *testing.T) {
	hub := NewTelemetryHub()
	original := telemetry.Snapshot{Adapter: "mujoco", Frame: []byte("png"), FrameMediaType: "image/png"}
	hub.Publish(original)
	original.Frame[0] = 'X'

	latest, ok := hub.Latest("mujoco")
	if !ok || string(latest.Frame) != "png" {
		t.Fatalf("latest = %#v, ok = %v", latest, ok)
	}
	latest.Frame[0] = 'Y'
	again, _ := hub.Latest("mujoco")
	if string(again.Frame) != "png" {
		t.Fatalf("latest aliases caller memory: %q", again.Frame)
	}
	history := hub.History("mujoco", 1)
	history[0].Frame[0] = 'Z'
	again, _ = hub.Latest("mujoco")
	if string(again.Frame) != "png" {
		t.Fatalf("history aliases cached frame: %q", again.Frame)
	}
	encoded, err := json.Marshal(again)
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) == 0 || bytes.Contains(encoded, []byte("png")) {
		t.Fatalf("frame leaked into JSON: %s", encoded)
	}
}
