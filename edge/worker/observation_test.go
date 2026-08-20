package worker

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	fleettelemetry "github.com/SUSTechWLA/tangying-robot-agent-os/fleet/telemetry"
)

type staticObserver struct{ snapshot telemetry.Snapshot }

func (observer staticObserver) Telemetry(context.Context, string) (telemetry.Snapshot, error) {
	return observer.snapshot, nil
}

func TestObservationProtoPreservesWorldIdentity(t *testing.T) {
	worker := New(Config{RobotID: "robot-1", Adapter: "mujoco", WorldID: "world-test", TransformRevision: "scene-v1"})
	envelope := worker.observationsFromSample(fleettelemetry.Sample{
		RobotID: "robot-1", Adapter: "mujoco", ObservedAt: time.Unix(100, 0).UTC(),
		Pose: []float64{1, 2, 0.035, 0.5}, Activity: "IDLE", Held: "red-block",
	})[0]
	wire, err := observationToProto(envelope)
	if err != nil {
		t.Fatal(err)
	}
	if wire.WorldId != "world-test" || wire.SourceSequence != 1 || wire.TransformRevision != "scene-v1" || wire.Payload == nil {
		t.Fatalf("wire=%#v", wire)
	}
}

func TestBuildObservationsUsesMonotonicPerSourceSequence(t *testing.T) {
	observedAt := time.Unix(100, 0).UTC()
	worker := New(Config{
		RobotID: "robot-1", Adapter: "mujoco", WorldID: "world-test", TransformRevision: "scene-v1",
		Observer: staticObserver{snapshot: telemetry.Snapshot{
			ObservedAt: observedAt, RobotID: "runtime-robot", Activity: "IDLE",
			RobotState: map[string]any{"base_pose": []any{0.0, 0.0, 0.035}, "held": ""},
			Entities:   []telemetry.Entity{{EntityID: "red-block", Category: "block", Pose: []float64{0.5, 0.3, 0.8}, Confidence: 1}},
		}},
	})
	first, err := worker.BuildObservations(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	second, err := worker.BuildObservations(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != 2 || len(second) != 2 {
		t.Fatalf("observation counts %d %d", len(first), len(second))
	}
	if first[0].Kind != observation.RobotStateUpsert || first[1].Kind != observation.EntityUpsert {
		t.Fatalf("kinds=%q %q", first[0].Kind, first[1].Kind)
	}
	if first[0].SourceSequence+1 != second[0].SourceSequence || first[1].SourceSequence+1 != second[1].SourceSequence {
		t.Fatalf("source sequences %#v -> %#v", first, second)
	}
	if first[0].Provenance.Adapter != "mujoco" || first[0].TransformRevision != "scene-v1" {
		t.Fatalf("bad envelope %#v", first[0])
	}
}

func TestLocalTelemetryCanPublishTheFleetWorldContract(t *testing.T) {
	worker := New(Config{
		RobotID: "robot-local", Adapter: "mujoco", WorldID: "local-default",
		TransformRevision: "local-world-v1", AdapterVersion: "adapter-v1",
	})
	snapshot := telemetry.Snapshot{
		ObservedAt: time.Unix(200, 0).UTC(), RobotID: "runtime-id", Activity: "IDLE",
		RobotState: map[string]any{"base_pose": []any{0.1, 0.2, 0.03}, "held": "red-block"},
		Entities: []telemetry.Entity{{
			EntityID: "red-block", Category: "block", Pose: []float64{0.5, 0.3, 0.8}, Confidence: 1,
		}},
	}

	envelopes := worker.ObservationsFromTelemetry(snapshot)

	if len(envelopes) != 2 || envelopes[0].WorldID != "local-default" || envelopes[0].RobotID != "robot-local" {
		t.Fatalf("envelopes=%#v", envelopes)
	}
	robot := envelopes[0].Payload.(observation.RobotPayload)
	if robot.Held != "red-block" || len(robot.Pose) < 3 {
		t.Fatalf("robot payload=%#v", robot)
	}
}

func TestRoboCasaDefaultsToSharedHandoffWorldIdentity(t *testing.T) {
	instance := New(Config{RobotID: "robot-1", Adapter: "robocasa"})

	if instance.config.WorldID != "robocasa-handoff-v1" {
		t.Fatalf("world id=%q", instance.config.WorldID)
	}
	if instance.config.TransformRevision != "robocasa-world-v1" {
		t.Fatalf("transform revision=%q", instance.config.TransformRevision)
	}
}

func TestObservationSequenceBaseSurvivesEdgeProcessRestart(t *testing.T) {
	first := New(Config{RobotID: "robot-1", Adapter: "robocasa", ObservationSequenceBase: 1_000_000})
	second := New(Config{RobotID: "robot-1", Adapter: "robocasa", ObservationSequenceBase: 2_000_000})
	sample := fleettelemetry.Sample{RobotID: "robot-1", ObservedAt: time.Unix(300, 0).UTC()}

	before := first.observationsFromSample(sample)[0].SourceSequence
	after := second.observationsFromSample(sample)[0].SourceSequence

	if after <= before {
		t.Fatalf("restart sequence did not advance: %d -> %d", before, after)
	}
}
