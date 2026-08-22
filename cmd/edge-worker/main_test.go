package main

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
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
