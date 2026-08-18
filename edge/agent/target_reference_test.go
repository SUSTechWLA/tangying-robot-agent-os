package agent

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
)

func TestRuntimeCommandUsesCapabilitySpecificTargetReference(t *testing.T) {
	tests := []struct {
		capability string
		targetRef  string
		want       string
	}{
		{capability: "plan_grasp", targetRef: "right-bin", want: "red-cup"},
		{capability: "verify_grasp", want: "red-cup"},
		{capability: "manipulation.pick", want: "red-cup"},
		{capability: "manipulation.place", want: "right-bin"},
		{capability: "verify_placement", want: "right-bin"},
	}
	arguments := map[string]any{
		"objectId":      "red-cup",
		"destinationId": "right-bin",
	}
	for _, test := range tests {
		t.Run(test.capability, func(t *testing.T) {
			stepArguments := make(map[string]any, len(arguments)+1)
			for key, value := range arguments {
				stepArguments[key] = value
			}
			if test.targetRef != "" {
				stepArguments["targetRef"] = test.targetRef
			}
			command := commandForStep("task-target", taskgraph.SkillStep{
				ID:        "step",
				Skill:     test.capability,
				Arguments: stepArguments,
			})
			if command.TargetRef != test.want {
				t.Fatalf("runtime command target_ref = %q, want %q", command.TargetRef, test.want)
			}
		})
	}
}
