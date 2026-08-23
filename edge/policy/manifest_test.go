package policy

import (
	"bytes"
	"encoding/json"
	"errors"
	"testing"
	"time"
)

func validManifest() Manifest {
	return Manifest{
		SchemaVersion:              "policy.manifest.v1",
		PolicyID:                   "xlerobot-tabletop-vla",
		Version:                    "2026.08.1",
		Framework:                  FrameworkVLA,
		ArtifactSHA256:             "4f7b2f76c8f6018f6f29f76f4ec7d9910d413458a0f048cf94b6df7e15851508",
		Capabilities:               []string{"manipulation.place", "manipulation.pick"},
		RobotModels:                []string{"xlerobot-dual-arm"},
		Adapters:                   []string{"xlerobot_direct"},
		ObservationSchema:          "policy.observation.v1",
		RequiredObservationSources: []string{"scene", "proprioception"},
		MaxObservationAge:          500 * time.Millisecond,
		ActionSchema:               "xlerobot.named-joints.v1",
		MaxActionChunkLength:       32,
		ActionBounds: map[string]ActionBound{
			"left_arm_shoulder.pos": {Minimum: -12, Maximum: 12},
			"left_arm_gripper.pos":  {Minimum: 0, Maximum: 100},
		},
		TransformRevision:   "lab-map-v3",
		CalibrationRevision: "xlerobot-01-cal-v7",
	}
}

func TestManifestRevisionIsCanonicalAcrossSetAndMapOrder(t *testing.T) {
	first := validManifest()
	second := validManifest()
	second.Capabilities = []string{"manipulation.pick", "manipulation.place"}
	second.RequiredObservationSources = []string{"proprioception", "scene"}

	firstRevision, err := first.Revision()
	if err != nil {
		t.Fatal(err)
	}
	secondRevision, err := second.Revision()
	if err != nil {
		t.Fatal(err)
	}
	if firstRevision != secondRevision || len(firstRevision) != 64 {
		t.Fatalf("revisions = %q %q", firstRevision, secondRevision)
	}
}

func TestManifestFailsClosedForUnidentifiedLearnedArtifact(t *testing.T) {
	manifest := validManifest()
	manifest.ArtifactSHA256 = ""
	if err := manifest.Validate(); !errors.Is(err, ErrArtifactIdentity) {
		t.Fatalf("error = %v", err)
	}
}

func TestManifestCompatibilityChecksCapabilityRobotAdapterAndCalibration(t *testing.T) {
	manifest := validManifest()
	compatible := Compatibility{
		Capability: "manipulation.pick", RobotModel: "xlerobot-dual-arm",
		Adapter: "xlerobot_direct", TransformRevision: "lab-map-v3",
		CalibrationRevision: "xlerobot-01-cal-v7",
	}
	if err := manifest.CheckCompatibility(compatible); err != nil {
		t.Fatal(err)
	}
	for name, mutate := range map[string]func(*Compatibility){
		"capability": func(value *Compatibility) { value.Capability = "navigation.navigate" },
		"robot":      func(value *Compatibility) { value.RobotModel = "other" },
		"adapter":    func(value *Compatibility) { value.Adapter = "mujoco" },
		"transform":  func(value *Compatibility) { value.TransformRevision = "lab-map-v2" },
		"calibration": func(value *Compatibility) {
			value.CalibrationRevision = "old-calibration"
		},
	} {
		t.Run(name, func(t *testing.T) {
			value := compatible
			mutate(&value)
			if err := manifest.CheckCompatibility(value); !errors.Is(err, ErrPolicyIncompatible) {
				t.Fatalf("error = %v", err)
			}
		})
	}
}

func TestDeterministicManifestMayUseSyntheticArtifactIdentity(t *testing.T) {
	manifest := validManifest()
	manifest.Framework = FrameworkDeterministic
	manifest.ArtifactSHA256 = "deterministic:test-handoff-v1"
	if err := manifest.Validate(); err != nil {
		t.Fatal(err)
	}
}

func TestManifestWireUsesExplicitObservationAgeMilliseconds(t *testing.T) {
	manifest := validManifest()
	wire, err := json.Marshal(manifest)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(wire, []byte(`"maxObservationAgeMs":500`)) || bytes.Contains(wire, []byte(`"maxObservationAge":`)) {
		t.Fatalf("wire = %s", wire)
	}
	var decoded Manifest
	if err := json.Unmarshal(wire, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.MaxObservationAge != 500*time.Millisecond {
		t.Fatalf("age = %s", decoded.MaxObservationAge)
	}
}

func TestManifestRevisionMatchesPythonSidecarCanonicalContract(t *testing.T) {
	revision, err := validManifest().Revision()
	if err != nil {
		t.Fatal(err)
	}
	const crossLanguageRevision = "166ec1d18312132c11fd3650facaafea0615598081e5a3a6c117cb0c4eed09e8"
	if revision != crossLanguageRevision {
		t.Fatalf("revision = %s", revision)
	}
}
