package observation

import (
	"errors"
	"testing"
	"time"
)

func TestEnvelopeValidatesTypedEntityPayload(t *testing.T) {
	now := time.Unix(100, 0).UTC()
	envelope := Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: "obs-1", WorldID: "world-test",
		SourceID: "robot-1/sim", RobotID: "robot-1", SourceType: SourceSimGroundTruth,
		SourceSequence: 1, ObservedAt: now, ReceivedAt: now.Add(time.Millisecond),
		FrameID: "world", TransformRevision: "scene-v1", Kind: EntityUpsert,
		Payload:    EntityPayload{EntityID: "red-block", Category: "block", Pose: []float64{0.5, 0.3, 0.8}},
		Confidence: 1, Provenance: Provenance{Adapter: "mujoco", Version: "0.1.0"},
	}
	if err := envelope.Validate(); err != nil {
		t.Fatal(err)
	}
	envelope.Payload = RobotPayload{RobotID: "robot-1"}
	if err := envelope.Validate(); !errors.Is(err, ErrPayloadType) {
		t.Fatalf("wrong payload error = %v", err)
	}
}

func TestEnvelopeRejectsEmbeddedBytesAndAmbiguousFrame(t *testing.T) {
	now := time.Unix(100, 0).UTC()
	envelope := Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: "obs-1", WorldID: "world-test",
		SourceID: "robot-1/camera", RobotID: "robot-1", SourceType: SourceRGBDCamera,
		SourceSequence: 1, ObservedAt: now, ReceivedAt: now, FrameID: "camera",
		TransformRevision: "cal-v1", Kind: FrameReference,
		FrameRef: &FrameRef{URI: "frames/sha256/abc", MIME: "image/png", SHA256: "abc", Width: 640, Height: 480, ObservedAt: now},
		Payload:  []byte("raw-frame"), Confidence: 1,
		Provenance: Provenance{Adapter: "mujoco", Version: "0.1.0"},
	}
	if err := envelope.Validate(); !errors.Is(err, ErrAmbiguousPayload) {
		t.Fatalf("ambiguous frame error = %v", err)
	}
}
