package policy

import (
	"errors"
	"math"
	"testing"
	"time"
)

func validObservation(now time.Time) ObservationBundle {
	return ObservationBundle{
		SchemaVersion: "policy.observation.v1", ObservationID: "obs-42",
		ObservedAt: now.Add(-100 * time.Millisecond), RobotID: "robot-1",
		Adapter: "xlerobot_direct", RobotModel: "xlerobot-dual-arm",
		TransformRevision: "lab-map-v3", CalibrationRevision: "xlerobot-01-cal-v7",
		Sources: map[string]ObservationSource{
			"scene":          {Fresh: true, Confidence: 0.96},
			"proprioception": {Fresh: true, Confidence: 1},
		},
		RobotState: map[string]float64{"left_arm_shoulder.pos": 1.5},
		Entities:   []Entity{{EntityID: "red-block", Category: "block", Pose: []float64{0.1, 0.2, 0.8}}},
	}
}

func validRequest(t *testing.T, now time.Time) InferenceRequest {
	t.Helper()
	manifest := validManifest()
	revision, err := manifest.Revision()
	if err != nil {
		t.Fatal(err)
	}
	return InferenceRequest{
		SchemaVersion: "policy.inference.request.v1", RequestID: "infer-request-1",
		CommandID: "command-1", TaskID: "task-1", TaskRevision: 2, StepID: "handoff/sender",
		RobotID: "robot-1", Capability: "manipulation.pick", TargetRef: "red-block",
		ManifestRevision: revision, Observation: validObservation(now),
		WorldRevision: 19, ResourceID: "block:red-block", FencingToken: 7,
	}
}

func TestObservationValidationRejectsStaleOrDegradedRequiredSources(t *testing.T) {
	now := time.Date(2026, 8, 24, 12, 0, 0, 0, time.UTC)
	manifest := validManifest()
	stale := validObservation(now)
	stale.ObservedAt = now.Add(-time.Second)
	if err := ValidateObservation(manifest, stale, now); !errors.Is(err, ErrObservationStale) {
		t.Fatalf("stale error = %v", err)
	}
	degraded := validObservation(now)
	degraded.Sources["scene"] = ObservationSource{Fresh: false, Anomalies: []string{"CAMERA_LOST"}}
	if err := ValidateObservation(manifest, degraded, now); !errors.Is(err, ErrObservationUnavailable) {
		t.Fatalf("degraded error = %v", err)
	}
}

func TestInferenceResultMustEchoIdentityAndManifestRevision(t *testing.T) {
	now := time.Date(2026, 8, 24, 12, 0, 0, 0, time.UTC)
	manifest := validManifest()
	request := validRequest(t, now)
	result := InferenceResult{
		SchemaVersion: "policy.inference.result.v1", RequestID: request.RequestID,
		CommandID: request.CommandID, ManifestRevision: request.ManifestRevision,
		InferenceID: "inference-9", ObservationID: request.Observation.ObservationID,
		Actions: []Action{{Values: map[string]float64{"left_arm_shoulder.pos": 2}}},
	}
	if _, err := ValidateInference(manifest, request, result); err != nil {
		t.Fatal(err)
	}
	result.CommandID = "different-command"
	if _, err := ValidateInference(manifest, request, result); !errors.Is(err, ErrInferenceIdentity) {
		t.Fatalf("identity error = %v", err)
	}
}

func TestInferenceResultRejectsUnsafeActions(t *testing.T) {
	now := time.Date(2026, 8, 24, 12, 0, 0, 0, time.UTC)
	manifest := validManifest()
	request := validRequest(t, now)
	base := InferenceResult{
		SchemaVersion: "policy.inference.result.v1", RequestID: request.RequestID,
		CommandID: request.CommandID, ManifestRevision: request.ManifestRevision,
		InferenceID: "inference-9", ObservationID: request.Observation.ObservationID,
	}
	for name, actions := range map[string][]Action{
		"empty":        nil,
		"unknown":      {{Values: map[string]float64{"base.x.vel": 1}}},
		"out-of-range": {{Values: map[string]float64{"left_arm_shoulder.pos": 13}}},
		"non-finite":   {{Values: map[string]float64{"left_arm_shoulder.pos": math.NaN()}}},
	} {
		t.Run(name, func(t *testing.T) {
			result := base
			result.Actions = actions
			if _, err := ValidateInference(manifest, request, result); !errors.Is(err, ErrActionUnsafe) {
				t.Fatalf("error = %v", err)
			}
		})
	}

	tooLong := base
	tooLong.Actions = make([]Action, manifest.MaxActionChunkLength+1)
	for index := range tooLong.Actions {
		tooLong.Actions[index] = Action{Values: map[string]float64{"left_arm_gripper.pos": 50}}
	}
	if _, err := ValidateInference(manifest, request, tooLong); !errors.Is(err, ErrActionUnsafe) {
		t.Fatalf("length error = %v", err)
	}
}
