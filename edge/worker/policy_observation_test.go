package worker

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

type policyObservationRuntime struct {
	activityRuntime
	snapshot telemetry.Snapshot
}

func (runtime policyObservationRuntime) Telemetry(context.Context, string) (telemetry.Snapshot, error) {
	return runtime.snapshot, nil
}

func TestWorkerBuildsPolicyObservationFromLocalRuntimeState(t *testing.T) {
	now := time.Date(2026, 8, 24, 13, 0, 0, 0, time.UTC)
	runtimeClient := policyObservationRuntime{snapshot: telemetry.Snapshot{
		ObservedAt: now, RobotID: "runtime-robot", Adapter: "mujoco", Activity: "IDLE",
		RobotState: map[string]any{"reward": 1.25, "held": ""},
		Entities: []telemetry.Entity{{
			EntityID: "red-block", Category: "block", Pose: []float64{0.1, 0.2, 0.8},
			Confidence: 0.97, Relation: "inside:handoff-zone",
		}},
	}}
	worker := New(Config{
		RobotID: "robot-1", Adapter: "mujoco", RobotModel: "xlerobot-sim",
		TransformRevision: "mujoco-world-v1", CalibrationRevision: "sim-cal-v1",
		Runtime: runtimeClient,
	})
	observation, err := worker.buildPolicyObservation(context.Background(), runtime.Command{CommandID: "cmd-1"})
	if err != nil {
		t.Fatal(err)
	}
	if observation.SchemaVersion != "policy.observation.v1" || observation.ObservationID == "" ||
		observation.ObservedAt != now || observation.RobotID != "robot-1" ||
		observation.Adapter != "mujoco" || observation.RobotModel != "xlerobot-sim" ||
		observation.TransformRevision != "mujoco-world-v1" || observation.CalibrationRevision != "sim-cal-v1" {
		t.Fatalf("observation = %#v", observation)
	}
	if observation.RobotState["reward"] != 1.25 || len(observation.Entities) != 1 ||
		!observation.Sources["scene"].Fresh || !observation.Sources["proprioception"].Fresh {
		t.Fatalf("observation content = %#v", observation)
	}
}

func TestWorkerMarksPolicySourcesDegradedWhenRuntimeReportsAnomaly(t *testing.T) {
	worker := New(Config{RobotID: "robot-1", Adapter: "mujoco", Runtime: policyObservationRuntime{snapshot: telemetry.Snapshot{
		ObservedAt: time.Now().UTC(), RobotState: map[string]any{"reward": 0.0},
		Entities:  []telemetry.Entity{{EntityID: "red-block", Category: "block", Confidence: 1}},
		Anomalies: []string{"CAMERA_LOST"},
	}}})
	observation, err := worker.buildPolicyObservation(context.Background(), runtime.Command{CommandID: "cmd-2"})
	if err != nil {
		t.Fatal(err)
	}
	if observation.Sources["scene"].Fresh || len(observation.Sources["scene"].Anomalies) != 1 {
		t.Fatalf("sources = %#v", observation.Sources)
	}
}
