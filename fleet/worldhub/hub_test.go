package worldhub

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

func TestHubPublishesOnlyAcceptedWorldRevisions(t *testing.T) {
	hub := New("world-test", time.Second, 64)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	subscription, err := hub.Subscribe(ctx, 0)
	if err != nil {
		t.Fatal(err)
	}
	event := validObservation(1, "obs-1")
	if _, err := hub.Ingest(context.Background(), event); err != nil {
		t.Fatal(err)
	}
	if _, err := hub.Ingest(context.Background(), event); err != nil {
		t.Fatal(err)
	}
	select {
	case delta := <-subscription:
		if delta.Revision != 1 || delta.Snapshot.Entities["red-block"].EntityID != "red-block" {
			t.Fatalf("delta=%#v", delta)
		}
	case <-time.After(time.Second):
		t.Fatal("missing accepted delta")
	}
	select {
	case delta := <-subscription:
		t.Fatalf("duplicate published delta %#v", delta)
	case <-time.After(20 * time.Millisecond):
	}
}

func TestHubRequiresResyncWhenRetentionCannotCoverCursor(t *testing.T) {
	hub := New("world-test", time.Second, 2)
	for sequence := uint64(1); sequence <= 4; sequence++ {
		if _, err := hub.Ingest(context.Background(), validObservation(sequence, "obs-"+string(rune('0'+sequence)))); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := hub.Subscribe(context.Background(), 1); err != worldmodel.ErrResyncRequired {
		t.Fatalf("gap error=%v", err)
	}
}

func validObservation(sequence uint64, id string) observation.Envelope {
	now := time.Unix(100+int64(sequence), 0).UTC()
	return observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: id, WorldID: "world-test",
		SourceID: "robot-1/sim", RobotID: "robot-1", SourceType: observation.SourceSimGroundTruth,
		SourceSequence: sequence, ObservedAt: now, ReceivedAt: now,
		FrameID: "world", TransformRevision: "scene-v1", Kind: observation.EntityUpsert,
		Payload:    observation.EntityPayload{EntityID: "red-block", Category: "block", Pose: []float64{float64(sequence), 0, 0}},
		Confidence: 1, Provenance: observation.Provenance{Adapter: "mujoco", Version: "0.1.0"},
	}
}
