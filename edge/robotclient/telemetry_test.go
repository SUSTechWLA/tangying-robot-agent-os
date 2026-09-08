package robotclient

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
)

func TestObservationToTelemetryCopiesSceneFrame(t *testing.T) {
	frame := []byte("png-frame")
	got := observationToTelemetry(runtime.Snapshot{Adapter: "mujoco"}, &robotv1.Observation{
		CompressedImage: frame,
		ImageMediaType:  "image/png",
		SemanticState:   &robotv1.SemanticState{},
	}, "")
	frame[0] = 'X'
	if string(got.Frame) != "png-frame" {
		t.Fatalf("frame = %q", got.Frame)
	}
	if got.FrameMediaType != "image/png" {
		t.Fatalf("media type = %q", got.FrameMediaType)
	}
}

func TestObservationToTelemetryToleratesMissingSemanticState(t *testing.T) {
	got := observationToTelemetry(runtime.Snapshot{Adapter: "mujoco"}, &robotv1.Observation{}, "")
	if got.Adapter != "mujoco" || got.Activity != "" {
		t.Fatalf("snapshot = %#v", got)
	}
}

func TestObservationToTelemetryCopiesOriginalDepthAndCaptureTime(t *testing.T) {
	depth := []byte("depth-png")
	got := observationToTelemetry(runtime.Snapshot{Adapter: "mujoco"}, &robotv1.Observation{
		CompressedDepthImage: depth, DepthImageMediaType: "image/png", WallTimeUnixMs: 1725000000123,
	}, "")
	depth[0] = 'X'
	if string(got.DepthFrame) != "depth-png" || got.DepthFrameMediaType != "image/png" || got.ObservedAt.UnixMilli() != 1725000000123 {
		t.Fatalf("snapshot=%+v", got)
	}
}
