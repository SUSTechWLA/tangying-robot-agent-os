package main

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/observation"
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
