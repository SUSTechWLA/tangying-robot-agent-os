package robotclient

import (
	"crypto/sha256"
	"encoding/hex"
	"slices"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/sensors"
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

func TestObservationToTelemetryCopiesSynchronizedRGBDCapture(t *testing.T) {
	capture := validRobotCapture()
	legacy := []byte("overview")
	got := observationToTelemetry(runtime.Snapshot{Adapter: "mujoco", RobotID: "robot1"}, &robotv1.Observation{
		CompressedImage: legacy,
		ImageMediaType:  "image/png",
		Capture:         capture,
	}, "task-1")

	capture.Frames[0].Data[0] = 'X'
	capture.Frames[0].Intrinsics[0] = 99
	if got.Capture == nil || string(got.Capture.Frames[0].Data) != "rgb-frame" || got.Capture.Frames[0].Intrinsics[0] != 1 {
		t.Fatalf("capture was not deep-copied: %#v", got.Capture)
	}
	if string(got.Frame) != "overview" || got.FrameMediaType != "image/png" {
		t.Fatalf("legacy overview changed: frame=%q media=%q", got.Frame, got.FrameMediaType)
	}
}

func TestObservationToTelemetryFallsBackToHeadRGB(t *testing.T) {
	got := observationToTelemetry(runtime.Snapshot{}, &robotv1.Observation{Capture: validRobotCapture()}, "")
	if string(got.Frame) != "rgb-frame" || got.FrameMediaType != "image/jpeg" {
		t.Fatalf("fallback frame=%q media=%q", got.Frame, got.FrameMediaType)
	}
}

func TestObservationToTelemetryDropsInvalidCaptureButKeepsLegacyState(t *testing.T) {
	capture := validRobotCapture()
	capture.Frames[0].Sha256 = "invalid"
	got := observationToTelemetry(runtime.Snapshot{}, &robotv1.Observation{
		CompressedImage: []byte("overview"), ImageMediaType: "image/png", Capture: capture,
	}, "")
	if got.Capture != nil {
		t.Fatalf("invalid capture retained: %#v", got.Capture)
	}
	if !slices.Contains(got.Anomalies, sensors.AnomalyInvalidCapture) || string(got.Frame) != "overview" {
		t.Fatalf("legacy telemetry or anomaly missing: %#v", got)
	}
}

func validRobotCapture() *robotv1.SensorCapture {
	rgb := []byte("rgb-frame")
	depth := []byte("depth-frame")
	return &robotv1.SensorCapture{
		SchemaVersion: sensors.SchemaVersionV1, CaptureId: "capture-1", RobotId: "robot1", EpisodeId: "episode-1",
		SimulationStep: 9, SourceSequence: 3, CapturedUnixMs: time.Unix(1_700_000_000, 0).UnixMilli(),
		FrameId: "robot1/rgbd_head", TransformRevision: "transform-1", WorldRevision: 2,
		Frames: []*robotv1.SensorFrame{
			{SensorId: "robot1/rgbd_head", Modality: sensors.ModalityRGB, MediaType: "image/jpeg", Width: 64, Height: 48,
				Sha256: testDigest(rgb), Intrinsics: testIdentity3(), CameraToWorld: testIdentity4(), Data: rgb},
			{SensorId: "robot1/rgbd_head", Modality: sensors.ModalityDepth, MediaType: "application/x-depth-f32", Width: 64, Height: 48,
				Sha256: testDigest(depth), DepthScaleM: 1, MinRangeM: 0.05, MaxRangeM: 5,
				Intrinsics: testIdentity3(), CameraToWorld: testIdentity4(), Data: depth},
		},
	}
}

func testDigest(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func testIdentity3() []float64 { return []float64{1, 0, 0, 0, 1, 0, 0, 0, 1} }
func testIdentity4() []float64 {
	return []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}
}
