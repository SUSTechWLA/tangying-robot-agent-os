package tasks

import (
	"bytes"
	"encoding/json"
	"image"
	"image/png"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

func TestTelemetryHubStoresFramesOutsideLatestAndHistoryMetadata(t *testing.T) {
	hub := NewTelemetryHub()
	wantFrame := telemetryTestPNG(t)
	original := telemetry.Snapshot{Adapter: "mujoco", Frame: append([]byte(nil), wantFrame...), FrameMediaType: "image/png"}
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
	if !ok || !bytes.Equal(frame.Data, wantFrame) || frame.MediaType != "image/png" {
		t.Fatalf("frame = %#v, ok = %v", frame, ok)
	}
	frame.Data[0] = 'Y'
	again, _ := hub.LatestFrame("mujoco")
	if !bytes.Equal(again.Data, wantFrame) {
		t.Fatal("frame aliases caller memory")
	}
	encoded, err := json.Marshal(map[string]any{"latest": latest, "history": history})
	if err != nil {
		t.Fatal(err)
	}
	if len(encoded) == 0 || bytes.Contains(encoded, []byte("png")) {
		t.Fatalf("frame leaked into JSON: %s", encoded)
	}
}

func TestTelemetryHubKeepsPerAdapterFramesAndInvalidatesOnEveryUnusableNewestFrame(t *testing.T) {
	hub := NewTelemetryHub()
	validFrame := telemetryTestPNG(t)
	hub.Publish(telemetry.Snapshot{
		Adapter: "mujoco", Frame: validFrame, FrameMediaType: "image/png",
	})
	hub.Publish(telemetry.Snapshot{
		Adapter: "xlerobot_direct", Frame: validFrame, FrameMediaType: "image/png",
	})

	sim, simOK := hub.LatestFrame("mujoco")
	real, realOK := hub.LatestFrame("xlerobot_direct")
	if !simOK || !realOK || !bytes.Equal(sim.Data, validFrame) || !bytes.Equal(real.Data, validFrame) {
		t.Fatalf("per-adapter frames: sim=%#v/%v real=%#v/%v", sim, simOK, real, realOK)
	}

	unusable := []telemetry.Snapshot{
		{Adapter: "mujoco"},
		{Adapter: "mujoco", Frame: validFrame},
		{Adapter: "mujoco", Frame: make([]byte, MaxSceneFrameBytes+1), FrameMediaType: "image/png"},
		{Adapter: "mujoco", Frame: []byte("not an image"), FrameMediaType: "image/png"},
		{Adapter: "mujoco", Frame: validFrame, FrameMediaType: "image/svg+xml"},
	}
	for index, snapshot := range unusable {
		hub.Publish(telemetry.Snapshot{Adapter: "mujoco", Frame: validFrame, FrameMediaType: "image/png"})
		hub.Publish(snapshot)
		if frame, ok := hub.LatestFrame("mujoco"); ok {
			t.Fatalf("case %d retained stale frame: %#v", index, frame)
		}
	}
	latest, ok := hub.Latest("mujoco")
	if !ok {
		t.Fatal("metadata should still publish when its frame is oversized")
	}
	if len(latest.Frame) != 0 || latest.FrameMediaType != "" {
		t.Fatalf("oversized frame leaked into metadata: %#v", latest)
	}
}

func telemetryTestPNG(t *testing.T) []byte {
	t.Helper()
	var output bytes.Buffer
	if err := png.Encode(&output, image.NewNRGBA(image.Rect(0, 0, 1, 1))); err != nil {
		t.Fatal(err)
	}
	return output.Bytes()
}
