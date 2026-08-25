package redis

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strconv"
	"testing"
	"time"

	miniredis "github.com/alicebob/miniredis/v2"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/sensors"
	"github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
)

func TestSensorCaptureOrderingCorrelationAndTTL(t *testing.T) {
	server := miniredis.RunT(t)
	store := NewTelemetryStore(server.Addr(), "", 0)
	t.Cleanup(func() { _ = store.Close() })
	ctx := context.Background()

	for _, sequence := range []uint64{8, 7, 9} {
		if err := store.Ingest(ctx, telemetry.Sample{RobotID: "robot-1", Capture: redisCapture(sequence)}); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.CorrelateCapture(ctx, "capture-9", 42); err != nil {
		t.Fatal(err)
	}
	latest, ok, err := store.LatestCapture(ctx, "robot-1")
	if err != nil || !ok || latest.SourceSequence != 9 || latest.WorldRevision != 42 {
		t.Fatalf("latest=%#v ok=%v err=%v", latest, ok, err)
	}
	latest.Frames[0].Data[0] = 'X'
	stored, ok, err := store.Capture(ctx, "capture-9")
	if err != nil || !ok || string(stored.Frames[0].Data) != "rgb-9" {
		t.Fatalf("stored=%#v ok=%v err=%v", stored, ok, err)
	}
	duplicate := redisCapture(10)
	duplicate.CaptureID = "capture-9"
	if err := store.Ingest(ctx, telemetry.Sample{RobotID: "robot-1", Capture: duplicate}); err != nil {
		t.Fatal(err)
	}
	latest, _, _ = store.LatestCapture(ctx, "robot-1")
	if latest.SourceSequence != 9 || string(latest.Frames[0].Data) != "rgb-9" {
		t.Fatalf("duplicate replaced latest=%#v", latest)
	}
	wrongRobot := redisCapture(10)
	wrongRobot.RobotID = "robot-2"
	if err := store.Ingest(ctx, telemetry.Sample{RobotID: "robot-1", Capture: wrongRobot}); err == nil {
		t.Fatal("wrong-robot capture was accepted")
	}
	collision := redisCapture(9)
	collision.RobotID = "robot-2"
	if err := store.Ingest(ctx, telemetry.Sample{RobotID: "robot-2", Capture: collision}); err == nil {
		t.Fatal("cross-robot capture id collision was accepted")
	}

	if err := store.CorrelateCapture(ctx, "capture-10", 50); err != nil {
		t.Fatal(err)
	}
	if err := store.Ingest(ctx, telemetry.Sample{RobotID: "robot-1", Capture: redisCapture(10)}); err != nil {
		t.Fatal(err)
	}
	latest, _, _ = store.LatestCapture(ctx, "robot-1")
	if latest.SourceSequence != 10 || latest.WorldRevision != 50 {
		t.Fatalf("pre-correlated latest=%#v", latest)
	}

	server.FastForward(25 * time.Hour)
	if _, ok, err := store.LatestCapture(ctx, "robot-1"); err != nil || ok {
		t.Fatalf("expired capture ok=%v err=%v", ok, err)
	}
}

func TestSensorCaptureStorageIsBoundedPerRobot(t *testing.T) {
	server := miniredis.RunT(t)
	store := NewTelemetryStore(server.Addr(), "", 0)
	t.Cleanup(func() { _ = store.Close() })
	ctx := context.Background()
	for sequence := uint64(1); sequence <= 70; sequence++ {
		if err := store.Ingest(ctx, telemetry.Sample{RobotID: "robot-1", Capture: redisCapture(sequence)}); err != nil {
			t.Fatal(err)
		}
	}
	if _, ok, err := store.Capture(ctx, "capture-6"); err != nil || ok {
		t.Fatalf("pruned capture ok=%v err=%v", ok, err)
	}
	if retained, ok, err := store.Capture(ctx, "capture-7"); err != nil || !ok || retained.SourceSequence != 7 {
		t.Fatalf("oldest retained=%#v ok=%v err=%v", retained, ok, err)
	}
	latest, ok, err := store.LatestCapture(ctx, "robot-1")
	if err != nil || !ok || latest.SourceSequence != 70 {
		t.Fatalf("latest=%#v ok=%v err=%v", latest, ok, err)
	}
}

func redisCapture(sequence uint64) *sensors.Capture {
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
				SHA256: redisCaptureDigest(rgb), Intrinsics: redisIdentity3(), CameraToWorld: redisIdentity4(), Data: rgb},
			{SensorID: "robot-1/rgbd_head", Modality: sensors.ModalityDepth, MediaType: "image/png;depth=uint16-mm", Width: 16, Height: 12,
				SHA256: redisCaptureDigest(depth), DepthScaleM: 0.001, MinRangeM: 0.05, MaxRangeM: 5,
				Intrinsics: redisIdentity3(), CameraToWorld: redisIdentity4(), Data: depth},
		},
	}
}

func redisCaptureDigest(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func redisIdentity3() []float64 { return []float64{1, 0, 0, 0, 1, 0, 0, 0, 1} }
func redisIdentity4() []float64 {
	return []float64{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}
}
