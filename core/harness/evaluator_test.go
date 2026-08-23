package harness

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/worldmodel"
)

func TestRejectsToolSuccessWithoutPostCommandWorldEvidence(t *testing.T) {
	input := satisfiedInput()
	input.Snapshot.Revision = input.Basis.WorldRevision

	verdict := New(time.Second).Evaluate(input)

	if verdict.Status != Waiting || verdict.Reason != "WORLD_REVISION_NOT_ADVANCED" {
		t.Fatalf("verdict=%#v", verdict)
	}
}

func TestAcceptsStableFreshPostCommandPlacement(t *testing.T) {
	verdict := New(time.Second).Evaluate(satisfiedInput())

	if verdict.Status != Satisfied || verdict.Reason != "PHYSICAL_POSTCONDITIONS_SATISFIED" {
		t.Fatalf("verdict=%#v", verdict)
	}
	if len(verdict.EvidenceIDs) != 2 {
		t.Fatalf("evidence=%v", verdict.EvidenceIDs)
	}
}

func TestEmergencyStopFailsSafeBeforeRelationEvaluation(t *testing.T) {
	input := satisfiedInput()
	robot := input.Snapshot.Robots["robot-1"]
	robot.EmergencyStopped = true
	input.Snapshot.Robots["robot-1"] = robot
	entity := input.Snapshot.Entities["red-block"]
	entity.Relations["inside"] = "wrong-zone"
	input.Snapshot.Entities["red-block"] = entity

	verdict := New(time.Second).Evaluate(input)

	if verdict.Status != FailedSafe || verdict.Reason != "ROBOT_EMERGENCY_STOPPED" {
		t.Fatalf("verdict=%#v", verdict)
	}
}

func TestStaleFencingFailsSafe(t *testing.T) {
	input := satisfiedInput()
	resource := input.Snapshot.Resources["block:red-block"]
	resource.FencingToken--
	input.Snapshot.Resources["block:red-block"] = resource

	verdict := New(time.Second).Evaluate(input)

	if verdict.Status != FailedSafe || verdict.Reason != "FENCING_TOKEN_MISMATCH" {
		t.Fatalf("verdict=%#v", verdict)
	}
}

func TestAuthoritativeResourceLeaseDoesNotExpireWithSensorFreshness(t *testing.T) {
	input := satisfiedInput()
	resource := input.Snapshot.Resources["block:red-block"]
	resource.Evidence.ObservedAt = input.Snapshot.ProjectedAt.Add(-time.Hour)
	resource.ExpiresAt = input.Snapshot.ProjectedAt.Add(time.Minute)
	input.Snapshot.Resources["block:red-block"] = resource

	verdict := New(time.Second).Evaluate(input)

	if verdict.Status != Satisfied || verdict.Reason != "PHYSICAL_POSTCONDITIONS_SATISFIED" {
		t.Fatalf("verdict=%#v", verdict)
	}
}

func TestExpiredResourceLeaseCannotConfirmCompletion(t *testing.T) {
	input := satisfiedInput()
	resource := input.Snapshot.Resources["block:red-block"]
	resource.ExpiresAt = input.Snapshot.ProjectedAt
	input.Snapshot.Resources["block:red-block"] = resource

	verdict := New(time.Second).Evaluate(input)

	if verdict.Status != Waiting || verdict.Reason != "RESOURCE_LEASE_EXPIRED" {
		t.Fatalf("verdict=%#v", verdict)
	}
}

func satisfiedInput() Input {
	started := time.Unix(100, 0).UTC()
	now := started.Add(100 * time.Millisecond)
	entityEvidence := worldmodel.EvidenceRef{
		ObservationID: "entity-post-2", SourceID: "robot-1/scene", SourceSequence: 12,
		ObservedAt: now, FrameID: "world", TransformRevision: "robocasa-world-v1",
	}
	robotEvidence := worldmodel.EvidenceRef{
		ObservationID: "robot-post-1", SourceID: "robot-1/proprioception", SourceSequence: 21,
		ObservedAt: now, FrameID: "world", TransformRevision: "robocasa-world-v1",
	}
	return Input{
		Intent: Intent{Action: "place", RobotID: "robot-1"},
		Basis: EvidenceBasis{
			WorldRevision: 5, EntityObservationCount: 8, EntitySourceID: "robot-1/scene",
			EntitySourceSequence: 10, RobotSourceID: "robot-1/proprioception",
			RobotSourceSequence: 20, CommandStartedAt: started,
		},
		Snapshot: worldmodel.Snapshot{
			Revision: 9, ProjectedAt: now,
			Entities: map[string]worldmodel.EntityState{
				"red-block": {
					EntityID: "red-block", Relations: map[string]string{"inside": "handoff-zone"},
					ObservationCount: 10, StableObservations: 2, Evidence: entityEvidence,
				},
			},
			Robots: map[string]worldmodel.RobotState{
				"robot-1": {RobotID: "robot-1", Held: "", Evidence: robotEvidence},
			},
			Resources: map[string]worldmodel.ResourceState{
				"block:red-block": {
					ResourceID: "block:red-block", Owner: "robot-1", FencingToken: 7,
					Evidence: worldmodel.EvidenceRef{ObservationID: "lease-7", ObservedAt: now},
				},
			},
			Sources: map[string]worldmodel.SourceState{
				"robot-1/scene":          {SourceID: "robot-1/scene", SourceSequence: 12, LastObservedAt: now},
				"robot-1/proprioception": {SourceID: "robot-1/proprioception", SourceSequence: 21, LastObservedAt: now},
			},
		},
		EntityID: "red-block", DestinationID: "handoff-zone", RobotID: "robot-1",
		ExpectedResource:           ExpectedResource{ResourceID: "block:red-block", Owner: "robot-1", FencingToken: 7},
		RequiredStableObservations: 2,
	}
}
