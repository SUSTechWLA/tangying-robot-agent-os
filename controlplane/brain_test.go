package controlplane

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
)

func TestLocalBrainCombinesParserAndPlanner(t *testing.T) {
	brain := NewLocalBrain(intent.NewDeterministicParser(), orchestration.DeterministicPlanner{})
	parsed, err := brain.Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	bundle, err := brain.Plan("把红色杯子放进右侧收纳盒", parsed)
	if err != nil {
		t.Fatal(err)
	}
	if bundle.Source != orchestration.SourceDeterministic {
		t.Fatalf("bundle source = %s", bundle.Source)
	}
}
