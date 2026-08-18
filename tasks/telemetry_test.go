package tasks

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func TestTelemetryHubStoresFramesOutsideLatestAndHistoryMetadata(t *testing.T) {
	hub := NewTelemetryHub()
	original := telemetry.Snapshot{Adapter: "mujoco", Frame: []byte("png"), FrameMediaType: "image/png"}
	hub.Publish(original)
	original.Frame[0] = 'X'

	latest, ok := hub.Latest("mujoco")
	if !ok || len(latest.Frame) != 0 || latest.FrameMediaType != "" {
		t.Fatalf("latest = %#v, ok = %v", latest, ok)
	}
	history := hub.History("mujoco", 1)
	if len(history) != 1 || len(history[0].Frame) != 0 || history[0].FrameMediaType != "" {
		t.Fatalf("history retained frame payload: %#v", history)
	}
	frame, ok := hub.LatestFrame("mujoco")
	if !ok || string(frame.Data) != "png" || frame.MediaType != "image/png" {
		t.Fatalf("frame = %#v, ok = %v", frame, ok)
	}
	frame.Data[0] = 'Y'
	again, _ := hub.LatestFrame("mujoco")
	if string(again.Data) != "png" {
		t.Fatalf("frame aliases caller memory: %q", again.Data)
	}
	encoded, err := json.Marshal(map[string]any{"latest": latest, "history": history})
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) == 0 || bytes.Contains(encoded, []byte("png")) {
		t.Fatalf("frame leaked into JSON: %s", encoded)
	}
}

func TestTelemetryHubKeepsPerAdapterFramesAndDropsOversizedPayloads(t *testing.T) {
	hub := NewTelemetryHub()
	hub.Publish(telemetry.Snapshot{
		Adapter: "mujoco", Frame: []byte("png"), FrameMediaType: "image/png",
	})
	hub.Publish(telemetry.Snapshot{
		Adapter: "xlerobot_direct", Frame: []byte("jpeg"), FrameMediaType: "image/jpeg",
	})

	sim, simOK := hub.LatestFrame("mujoco")
	real, realOK := hub.LatestFrame("xlerobot_direct")
	if !simOK || !realOK || string(sim.Data) != "png" || string(real.Data) != "jpeg" {
		t.Fatalf("per-adapter frames: sim=%#v/%v real=%#v/%v", sim, simOK, real, realOK)
	}

	hub.Publish(telemetry.Snapshot{
		Adapter: "mujoco", Frame: make([]byte, MaxSceneFrameBytes+1), FrameMediaType: "image/png",
	})
	sim, simOK = hub.LatestFrame("mujoco")
	if !simOK || string(sim.Data) != "png" {
		t.Fatalf("oversized frame replaced last bounded frame: %#v/%v", sim, simOK)
	}
	latest, ok := hub.Latest("mujoco")
	if !ok {
		t.Fatal("metadata should still publish when its frame is oversized")
	}
	if len(latest.Frame) != 0 || latest.FrameMediaType != "" {
		t.Fatalf("oversized frame leaked into metadata: %#v", latest)
	}
}
