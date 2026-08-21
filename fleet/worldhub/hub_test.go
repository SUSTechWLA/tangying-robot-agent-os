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

func TestHubSuppressesOnlyPubliclyIdenticalStaticFacts(t *testing.T) {
	hub := New("world-test", time.Second, 64)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	subscription, err := hub.Subscribe(ctx, 0)
	if err != nil {
		t.Fatal(err)
	}
	first := staticObservation(1, "fixture-1")
	if _, err := hub.Ingest(ctx, first); err != nil {
		t.Fatal(err)
	}
	<-subscription
	before, _ := hub.Snapshot(ctx)
	unchanged := staticObservation(2, "fixture-2")
	if _, err := hub.Ingest(ctx, unchanged); err != nil {
		t.Fatal(err)
	}
	after, _ := hub.Snapshot(ctx)
	if after.Revision != before.Revision || after.EventCursor != before.EventCursor ||
		after.Sources[first.SourceID].SourceSequence != before.Sources[first.SourceID].SourceSequence {
		t.Fatalf("suppressed event changed public snapshot: before=%#v after=%#v", before, after)
	}
	select {
	case delta := <-subscription:
		t.Fatalf("publicly identical static fact published delta %#v", delta)
	case <-time.After(20 * time.Millisecond):
	}

	degraded := staticObservation(3, "fixture-3")
	degraded.Quality.Anomalies = []string{"MODEL_GEOMETRY_DEGRADED"}
	if _, err := hub.Ingest(ctx, degraded); err != nil {
		t.Fatal(err)
	}
	select {
	case delta := <-subscription:
		if delta.Revision != before.Revision+1 || len(delta.Snapshot.Health.DegradedSources) != 1 {
			t.Fatalf("health delta=%#v", delta)
		}
	case <-time.After(time.Second):
		t.Fatal("missing static source health delta")
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

func staticObservation(sequence uint64, id string) observation.Envelope {
	event := validObservation(sequence, id)
	event.Payload = observation.EntityPayload{
		EntityID: "fixture", Category: "counter", Pose: []float64{0.5, 0.5, 0.5},
		Attributes: map[string]string{"static": "true", "bounds": "0,0,0,1,1,1"},
		Relations:  map[string]string{"fixed_in": "kitchen"},
	}
	return event
}
