package main

import (
	"errors"
	"os"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/worker"
)

func TestRoboCasaAdvertisesSimulationGroundTruth(t *testing.T) {
	_, sources, err := observationAdvertisements("robot-1", "robocasa", "1.0.1", "robocasa-world-v1")
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) != 2 {
		t.Fatalf("sources=%d", len(sources))
	}
	if got := sources[1].SourceType; got != string(observation.SourceSimGroundTruth) {
		t.Fatalf("scene source=%q, want %q", got, observation.SourceSimGroundTruth)
	}
}

func TestBuildPolicyProviderKeepsDisabledModeBackwardCompatible(t *testing.T) {
	t.Setenv("EDGE_POLICY_MODE", "disabled")
	provider, err := buildPolicyProvider("mujoco", "xlerobot-sim", "mujoco-world-v1", "")
	if err != nil || provider != nil {
		t.Fatalf("provider=%v error=%v", provider, err)
	}
}

func TestBuildPolicyProviderAllowsDeterministicModeOnlyForSimulation(t *testing.T) {
	t.Setenv("EDGE_POLICY_MODE", "deterministic")
	provider, err := buildPolicyProvider("mujoco", "xlerobot-sim", "mujoco-world-v1", "")
	if err != nil || provider == nil {
		t.Fatalf("provider=%v error=%v", provider, err)
	}
	manifest, err := provider.Manifest(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if manifest.Framework != "deterministic" || manifest.TransformRevision != "mujoco-world-v1" {
		t.Fatalf("manifest = %#v", manifest)
	}
	if _, err := buildPolicyProvider("xlerobot_direct", "xlerobot-dual-arm", "lab-map-v1", "cal-v1"); !errors.Is(err, errUnsafePolicyMode) {
		t.Fatalf("physical error = %v", err)
	}
}

func TestBuildPolicyProviderRequiresEndpointForHTTPMode(t *testing.T) {
	t.Setenv("EDGE_POLICY_MODE", "http")
	_ = os.Unsetenv("EDGE_POLICY_ENDPOINT")
	if _, err := buildPolicyProvider("xlerobot_direct", "xlerobot-dual-arm", "lab-map-v1", "cal-v1"); err == nil {
		t.Fatal("expected endpoint configuration error")
	}
}

func TestRuntimePolicyRequirementDetectsLearnedActionInput(t *testing.T) {
	snapshot := runtime.Snapshot{Capabilities: []runtime.Capability{{
		Name: "manipulation.pick", InputParameters: []string{"target_ref", "action_chunk"},
	}}}
	if !runtimeNeedsPolicy(snapshot) {
		t.Fatal("action_chunk capability must require policy")
	}
	if err := validatePolicyConfigured(snapshot, nil); !errors.Is(err, worker.ErrPolicyRequired) {
		t.Fatalf("error = %v", err)
	}
}

func TestToolAdvertisementsUseConservativeHumanFallbacks(t *testing.T) {
	_, tools := toolAdvertisements(runtime.Snapshot{Capabilities: []runtime.Capability{{
		Name: "manipulation.pick", SafetyLevel: "physical_motion", Available: true,
		InputParameters: []string{"targetRef", "speed"},
	}}})
	if len(tools) != 1 || tools[0].DisplayName != "拿稳物品" || tools[0].Purpose != "安全拿起指定物品" {
		t.Fatalf("tool advertisement = %#v", tools)
	}
	if len(tools[0].SafeArgumentNames) != 1 || tools[0].SafeArgumentNames[0] != "targetRef" {
		t.Fatalf("safe argument fallback = %v", tools[0].SafeArgumentNames)
	}
}
