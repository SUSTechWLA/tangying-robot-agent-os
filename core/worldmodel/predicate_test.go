package worldmodel

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
)

func TestEntityInsideIsUnknownWhenEvidenceIsStale(t *testing.T) {
	p := NewProjector("world-test", time.Second)
	observedAt := time.Unix(100, 0).UTC()
	if _, _, err := p.Apply(entityObservation(1, "obs-1", 0.5, "handoff-zone", observedAt)); err != nil {
		t.Fatal(err)
	}
	p.SetNow(func() time.Time { return observedAt.Add(time.Second) })
	result := EntityInside("red-block", "handoff-zone", 500*time.Millisecond).Evaluate(p.Snapshot())
	if result.Status != PredicateUnknown || result.Reason != ReasonEvidenceStale {
		t.Fatalf("predicate=%#v", result)
	}
}

func TestHandoffPredicatesRequireStableEntityEmptyHandAndOwner(t *testing.T) {
	p := NewProjector("world-test", time.Second)
	now := time.Unix(100, 0).UTC()
	if _, _, err := p.Apply(entityObservation(1, "obs-1", 0.5, "handoff-zone", now)); err != nil {
		t.Fatal(err)
	}
	if _, _, err := p.Apply(entityObservation(2, "obs-2", 0.5, "handoff-zone", now.Add(time.Millisecond))); err != nil {
		t.Fatal(err)
	}
	if _, _, err := p.Apply(robotObservation(1, "robot-1", "", now)); err != nil {
		t.Fatal(err)
	}
	if _, _, err := p.Apply(resourceObservation(1, "environment", 9, now)); err != nil {
		t.Fatal(err)
	}
	p.SetNow(func() time.Time { return now.Add(2 * time.Millisecond) })
	predicate := All(
		EntityInside("red-block", "handoff-zone", time.Second),
		EntityStable("red-block", 2, time.Second),
		RobotHeld("robot-1", "", time.Second),
		ResourceOwner("red-block", "environment", time.Second),
		SourceFresh("robot-1/sim", time.Second),
	)
	if result := predicate.Evaluate(p.Snapshot()); result.Status != PredicateTrue {
		t.Fatalf("predicate=%#v", result)
	}
}

func robotObservation(sequence uint64, robotID, held string, observedAt time.Time) observation.Envelope {
	return observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: "robot-obs", WorldID: "world-test",
		SourceID: robotID + "/proprioception", RobotID: robotID, SourceType: observation.SourceProprioception,
		SourceSequence: sequence, ObservedAt: observedAt, ReceivedAt: observedAt,
		FrameID: "base", TransformRevision: "urdf-v1", Kind: observation.RobotStateUpsert,
		Payload: observation.RobotPayload{RobotID: robotID, Held: held}, Confidence: 1,
		Provenance: observation.Provenance{Adapter: "mujoco", Version: "0.1.0"},
	}
}

func resourceObservation(sequence uint64, owner string, token uint64, observedAt time.Time) observation.Envelope {
	return observation.Envelope{
		SchemaVersion: "world.observation.v1", ObservationID: "resource-obs", WorldID: "world-test",
		SourceID: "coordinator/resources", SourceType: observation.SourceToolResultEvidence,
		SourceSequence: sequence, ObservedAt: observedAt, ReceivedAt: observedAt,
		FrameID: "world", TransformRevision: "logical-v1", Kind: observation.ResourceUpsert,
		Payload: observation.ResourcePayload{ResourceID: "red-block", Owner: owner, FencingToken: token}, Confidence: 1,
		Provenance: observation.Provenance{Adapter: "coordinator", Version: "0.1.0"},
	}
}
