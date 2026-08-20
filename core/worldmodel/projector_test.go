package worldmodel

import (
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
)

func TestProjectorRejectsDuplicateAndOutOfOrderSourceSequence(t *testing.T) {
	p := NewProjector("world-test", time.Second)
	first := entityObservation(2, "obs-2", 0.5, "handoff-zone", time.Unix(100, 0).UTC())
	if _, accepted, err := p.Apply(first); err != nil || !accepted {
		t.Fatalf("first accepted=%v err=%v", accepted, err)
	}
	if snapshot, accepted, err := p.Apply(first); err != nil || accepted || snapshot.Revision != 1 {
		t.Fatalf("duplicate snapshot=%#v accepted=%v err=%v", snapshot, accepted, err)
	}
	older := entityObservation(1, "obs-1", -9, "outside", time.Unix(99, 0).UTC())
	if snapshot, accepted, err := p.Apply(older); err != nil || accepted || snapshot.Entities["red-block"].Pose[0] != 0.5 {
		t.Fatalf("old event changed world: %#v accepted=%v err=%v", snapshot, accepted, err)
	}
}

func TestProjectorRejectsConflictingTransformWithoutAdvancing(t *testing.T) {
	p := NewProjector("world-test", time.Second)
	first := entityObservation(1, "obs-1", 0.5, "handoff-zone", time.Unix(100, 0).UTC())
	if _, _, err := p.Apply(first); err != nil {
		t.Fatal(err)
	}
	conflict := entityObservation(2, "obs-2", 0.6, "handoff-zone", time.Unix(101, 0).UTC())
	conflict.TransformRevision = "scene-v2"
	snapshot, accepted, err := p.Apply(conflict)
	if !errors.Is(err, ErrTransformRevisionConflict) || accepted || snapshot.Revision != 1 {
		t.Fatalf("snapshot=%#v accepted=%v err=%v", snapshot, accepted, err)
	}
}

func TestSnapshotIsDeepCopyAndDerivesFreshness(t *testing.T) {
	p := NewProjector("world-test", time.Second)
	now := time.Unix(100, 0).UTC()
	p.SetNow(func() time.Time { return now })
	if _, _, err := p.Apply(entityObservation(1, "obs-1", 0.5, "handoff-zone", now)); err != nil {
		t.Fatal(err)
	}
	first := p.Snapshot()
	entity := first.Entities["red-block"]
	entity.Pose[0] = -10
	entity.Relations["inside"] = "elsewhere"
	first.Entities["red-block"] = entity
	second := p.Snapshot()
	if second.Entities["red-block"].Pose[0] != 0.5 || second.Entities["red-block"].Relations["inside"] != "handoff-zone" {
		t.Fatalf("snapshot mutation leaked: %#v", second.Entities["red-block"])
	}
	if second.Entities["red-block"].Freshness != Fresh {
		t.Fatalf("freshness=%q", second.Entities["red-block"].Freshness)
	}
	p.SetNow(func() time.Time { return now.Add(2 * time.Second) })
	if stale := p.Snapshot().Entities["red-block"].Freshness; stale != Stale {
		t.Fatalf("freshness=%q", stale)
	}
}

func TestAuthoritativeResourceEventRemainsCurrentUntilLeaseExpiry(t *testing.T) {
	now := time.Unix(100, 0).UTC()
	p := NewProjector("world-test", time.Second)
	p.SetNow(func() time.Time { return now })
	event := observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: "grant-7", WorldID: "world-test",
		SourceID: "coordinator/resources/block:red-block", SourceType: observation.SourceToolResultEvidence,
		SourceSequence: 7, ObservedAt: now, ReceivedAt: now, FrameID: "world",
		TransformRevision: "coordinator-world-v1", Kind: observation.ResourceUpsert,
		Payload: observation.ResourcePayload{
			ResourceID: "block:red-block", Owner: "environment", FencingToken: 7,
		},
		Confidence: 1,
		Provenance: observation.Provenance{Adapter: "fleet-coordinator", Version: "v1"},
	}
	if _, accepted, err := p.Apply(event); err != nil || !accepted {
		t.Fatalf("accepted=%v err=%v", accepted, err)
	}

	p.SetNow(func() time.Time { return now.Add(time.Hour) })
	snapshot := p.Snapshot()
	if snapshot.Sources[event.SourceID].Freshness != Fresh {
		t.Fatalf("source freshness=%s", snapshot.Sources[event.SourceID].Freshness)
	}
	if snapshot.Resources["block:red-block"].Freshness != Fresh {
		t.Fatalf("resource freshness=%s", snapshot.Resources["block:red-block"].Freshness)
	}
	if len(snapshot.Health.DegradedSources) != 0 {
		t.Fatalf("degraded=%v", snapshot.Health.DegradedSources)
	}
}

func entityObservation(sequence uint64, id string, x float64, inside string, observedAt time.Time) observation.Envelope {
	return observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: id, WorldID: "world-test",
		SourceID: "robot-1/sim", RobotID: "robot-1", SourceType: observation.SourceSimGroundTruth,
		SourceSequence: sequence, ObservedAt: observedAt, ReceivedAt: observedAt,
		FrameID: "world", TransformRevision: "scene-v1", Kind: observation.EntityUpsert,
		Payload: observation.EntityPayload{
			EntityID: "red-block", Category: "block", Pose: []float64{x, 0.3, 0.8},
			Relations: map[string]string{"inside": inside},
		},
		Confidence: 1, Provenance: observation.Provenance{Adapter: "mujoco", Version: "0.1.0"},
	}
}
