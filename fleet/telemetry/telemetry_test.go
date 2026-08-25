package telemetry

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strconv"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/sensors"
)

func TestSensorCaptureOrderingCorrelationAndImmutability(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryStore()

	eight := captureForSequence(8)
	if err := store.Ingest(ctx, Sample{RobotID: "robot-1", Capture: eight}); err != nil {
		t.Fatal(err)
	}
	eight.Frames[0].Data[0] = 'X'
	if err := store.Ingest(ctx, Sample{RobotID: "robot-1", Capture: captureForSequence(7)}); err != nil {
		t.Fatal(err)
	}
	latest, ok, err := store.LatestCapture(ctx, "robot-1")
	if err != nil || !ok || latest.SourceSequence != 8 || string(latest.Frames[0].Data) != "rgb-8" {
		t.Fatalf("latest after out-of-order ingest = %#v ok=%v err=%v", latest, ok, err)
	}

	if err := store.Ingest(ctx, Sample{RobotID: "robot-1", Capture: captureForSequence(9)}); err != nil {
		t.Fatal(err)
	}
	if err := store.CorrelateCapture(ctx, "capture-9", 42); err != nil {
		t.Fatal(err)
	}
	if err := store.CorrelateCapture(ctx, "capture-9", 40); err != nil {
		t.Fatal(err)
	}
	latest, ok, err = store.LatestCapture(ctx, "robot-1")
	if err != nil || !ok || latest.SourceSequence != 9 || latest.WorldRevision != 42 {
		t.Fatalf("correlated latest = %#v ok=%v err=%v", latest, ok, err)
	}
	latest.Frames[0].Data[0] = 'Y'
	stored, ok, err := store.Capture(ctx, "capture-9")
	if err != nil || !ok || string(stored.Frames[0].Data) != "rgb-9" {
		t.Fatalf("capture storage was mutable = %#v ok=%v err=%v", stored, ok, err)
	}

	if err := store.CorrelateCapture(ctx, "capture-10", 50); err != nil {
		t.Fatal(err)
	}
	if err := store.Ingest(ctx, Sample{RobotID: "robot-1", Capture: captureForSequence(10)}); err != nil {
		t.Fatal(err)
	}
	latest, _, _ = store.LatestCapture(ctx, "robot-1")
	if latest.SourceSequence != 10 || latest.WorldRevision != 50 {
		t.Fatalf("pre-correlated capture = %#v", latest)
	}
}

func TestSensorCaptureRejectsWrongRobotAndIgnoresDuplicateIdentity(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryStore()
	if err := store.Ingest(ctx, Sample{RobotID: "robot-1", Capture: captureForSequence(9)}); err != nil {
		t.Fatal(err)
	}
	wrongRobot := captureForSequence(10)
	wrongRobot.RobotID = "robot-2"
	if err := store.Ingest(ctx, Sample{RobotID: "robot-1", Capture: wrongRobot}); err == nil {
		t.Fatal("capture from a different robot was accepted")
	}
	collision := captureForSequence(9)
	collision.RobotID = "robot-2"
	if err := store.Ingest(ctx, Sample{RobotID: "robot-2", Capture: collision}); err == nil {
		t.Fatal("cross-robot capture id collision was accepted")
	}
	duplicate := captureForSequence(10)
	duplicate.CaptureID = "capture-9"
	if err := store.Ingest(ctx, Sample{RobotID: "robot-1", Capture: duplicate}); err != nil {
		t.Fatal(err)
	}
	latest, _, _ := store.LatestCapture(ctx, "robot-1")
	if latest.SourceSequence != 9 || string(latest.Frames[0].Data) != "rgb-9" {
		t.Fatalf("duplicate replaced stored capture: %#v", latest)
	}
}

func TestSensorCaptureStorageIsBoundedPerRobot(t *testing.T) {
	ctx := context.Background()
	store := NewMemoryStore()
	for sequence := uint64(1); sequence <= 70; sequence++ {
		if err := store.Ingest(ctx, Sample{RobotID: "robot-1", Capture: captureForSequence(sequence)}); err != nil {
			t.Fatal(err)
		}
	}
	if _, ok, err := store.Capture(ctx, "capture-6"); err != nil || ok {
		t.Fatalf("pruned capture ok=%v err=%v", ok, err)
	}
	if retained, ok, err := store.Capture(ctx, "capture-7"); err != nil || !ok || retained.SourceSequence != 7 {
		t.Fatalf("oldest retained=%#v ok=%v err=%v", retained, ok, err)
	}
}

func captureForSequence(sequence uint64) *sensors.Capture {
	label := strconv.FormatUint(sequence, 10)
	rgb := []byte("rgb-" + label)
	depth := []byte("depth-" + label)
	return &sensors.Capture{
		SchemaVersion: sensors.SchemaVersionV1, CaptureID: "capture-" + label,
		RobotID: "robot-1", EpisodeID: "scene:2", SimulationStep: sequence * 4,
		SourceSequence: sequence, CapturedAt: time.Unix(int64(sequence), 0).UTC(),
		FrameID: "robot-1/rgbd_head", TransformRevision: "scene-v1",
		Frames: []sensors.Frame{
			{SensorID: "robot-1/rgbd_head", Modality: sensors.ModalityRGB, MediaType: "image/png", Width: 16, Height: 12,
				SHA256: captureDigest(rgb), Intrinsics: captureIdentity3(), CameraToWorld: captureIdentity4(), Data: rgb},
			{SensorID: "robot-1/rgbd_head", Modality: sensors.ModalityDepth, MediaType: "image/png;depth=uint16-mm", Width: 16, Height: 12,
				SHA256: captureDigest(depth), DepthScaleM: 0.001, MinRangeM: 0.05, MaxRangeM: 5,
				Intrinsics: captureIdentity3(), CameraToWorld: captureIdentity4(), Data: depth},
		},
	}
}

func captureDigest(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func captureIdentity3() []float64 { return []float64{1, 0, 0, 0, 1, 0, 0, 0, 1} }
func captureIdentity4() []float64 {
	return []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}
}
