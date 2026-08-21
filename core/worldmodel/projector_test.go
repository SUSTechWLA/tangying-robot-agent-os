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

func TestProjectorSuppressesUnchangedStaticEntityRevisions(t *testing.T) {
	p := NewProjector("world-test", time.Second)
	first := staticEntityObservation(1, "fixture-1", "0,0,0,1,1,1", time.Unix(100, 0).UTC())
	if snapshot, changed, err := p.Apply(first); err != nil || !changed || snapshot.Revision != 1 {
		t.Fatalf("first snapshot=%#v changed=%v err=%v", snapshot, changed, err)
	}
	unchanged := staticEntityObservation(2, "fixture-2", "0,0,0,1,1,1", time.Unix(101, 0).UTC())
	if snapshot, changed, err := p.Apply(unchanged); err != nil || changed || snapshot.Revision != 1 {
		t.Fatalf("unchanged snapshot=%#v changed=%v err=%v", snapshot, changed, err)
	}
	if p.sourceSequence[unchanged.SourceID] != 2 {
		t.Fatalf("suppressed event did not advance private high-water: %d", p.sourceSequence[unchanged.SourceID])
	}
	if snapshot := p.Snapshot(); snapshot.Sources[unchanged.SourceID].SourceSequence != 1 || snapshot.EventCursor != "fixture-1" {
		t.Fatalf("suppressed event changed public snapshot: %#v", snapshot)
	}
	changedBounds := staticEntityObservation(3, "fixture-3", "0,0,0,2,1,1", time.Unix(102, 0).UTC())
	if snapshot, changed, err := p.Apply(changedBounds); err != nil || !changed || snapshot.Revision != 2 {
		t.Fatalf("changed snapshot=%#v changed=%v err=%v", snapshot, changed, err)
	}
}

func TestStaticEntityRemainsFreshAsAVersionedModelFact(t *testing.T) {
	now := time.Unix(100, 0).UTC()
	p := NewProjector("world-test", time.Second)
	p.SetNow(func() time.Time { return now })
	if _, _, err := p.Apply(staticEntityObservation(1, "fixture-1", "0,0,0,1,1,1", now)); err != nil {
		t.Fatal(err)
	}
	p.SetNow(func() time.Time { return now.Add(2 * time.Second) })
	snapshot := p.Snapshot()
	if freshness := snapshot.Entities["fixture"].Freshness; freshness != Fresh {
		t.Fatalf("static fixture freshness=%q", freshness)
	}
	if freshness := snapshot.Sources["robot-1/sim"].Freshness; freshness != Stale {
		t.Fatalf("source freshness=%q", freshness)
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

func TestRobotJointStateSurvivesProjectionAndSnapshotCloning(t *testing.T) {
	now := time.Unix(100, 0).UTC()
	p := NewProjector("world-test", time.Second)
	p.SetNow(func() time.Time { return now })
	event := observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: "robot-joints-1", WorldID: "world-test",
		SourceID: "robot-1/proprioception", RobotID: "robot-1", SourceType: observation.SourceProprioception,
		SourceSequence: 1, ObservedAt: now, ReceivedAt: now, FrameID: "world", TransformRevision: "scene-v1",
		Kind: observation.RobotStateUpsert,
		Payload: observation.RobotPayload{
			RobotID: "robot-1", Pose: []float64{0, 0, 0},
			State: map[string]float64{"joint.left.rotation": 0.25},
		},
		Confidence: 1, Provenance: observation.Provenance{Adapter: "mujoco", Version: "0.1.0"},
	}
	if _, accepted, err := p.Apply(event); err != nil || !accepted {
		t.Fatalf("accepted=%v err=%v", accepted, err)
	}
	first := p.Snapshot()
	first.Robots["robot-1"].State["joint.left.rotation"] = -1
	if got := p.Snapshot().Robots["robot-1"].State["joint.left.rotation"]; got != 0.25 {
		t.Fatalf("joint state after projection and cloning = %v, want 0.25", got)
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

func staticEntityObservation(sequence uint64, id, bounds string, observedAt time.Time) observation.Envelope {
	event := entityObservation(sequence, id, 0.5, "kitchen", observedAt)
	event.Payload = observation.EntityPayload{
		EntityID: "fixture", Category: "counter", Pose: []float64{0.5, 0.5, 0.5},
		Attributes: map[string]string{"static": "true", "bounds": bounds},
		Relations:  map[string]string{"fixed_in": "kitchen"},
	}
	return event
}
