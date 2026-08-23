package policy

import (
	"context"
	"errors"
	"testing"
	"time"
)

func deterministicManifest() Manifest {
	manifest := validManifest()
	manifest.PolicyID = "simulation-handoff"
	manifest.Framework = FrameworkDeterministic
	manifest.ArtifactSHA256 = "deterministic:simulation-handoff-v1"
	manifest.RobotModels = []string{"xlerobot-sim"}
	manifest.Adapters = []string{"mujoco", "robocasa"}
	manifest.TransformRevision = ""
	manifest.CalibrationRevision = ""
	manifest.MaxObservationAge = time.Second
	return manifest
}

func TestDeterministicProviderReturnsAuditablePickAndPlaceActions(t *testing.T) {
	provider, err := NewDeterministicProvider(deterministicManifest())
	if err != nil {
		t.Fatal(err)
	}
	manifest, err := provider.Manifest(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	revision, _ := manifest.Revision()
	for _, capability := range []string{"manipulation.pick", "manipulation.place"} {
		request := validRequest(t, time.Now().UTC())
		request.Capability = capability
		request.ManifestRevision = revision
		request.Observation.Adapter = "mujoco"
		request.Observation.RobotModel = "xlerobot-sim"
		request.Observation.TransformRevision = "mujoco-world-v1"
		request.Observation.CalibrationRevision = ""
		decision, err := provider.Infer(context.Background(), request)
		if err != nil {
			t.Fatal(err)
		}
		if decision.InferenceID == "" || decision.ObservationID != request.Observation.ObservationID || len(decision.Actions) == 0 {
			t.Fatalf("decision = %#v", decision)
		}
	}
}

func TestDeterministicProviderStillRejectsStaleObservation(t *testing.T) {
	provider, err := NewDeterministicProvider(deterministicManifest())
	if err != nil {
		t.Fatal(err)
	}
	manifest, _ := provider.Manifest(context.Background())
	revision, _ := manifest.Revision()
	request := validRequest(t, time.Now().UTC())
	request.ManifestRevision = revision
	request.Observation.Adapter = "mujoco"
	request.Observation.RobotModel = "xlerobot-sim"
	request.Observation.ObservedAt = time.Now().Add(-2 * time.Second)
	if _, err := provider.Infer(context.Background(), request); !errors.Is(err, ErrObservationStale) {
		t.Fatalf("error = %v", err)
	}
}
