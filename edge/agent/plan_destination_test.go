package agent

import (
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestMaterializePlanAddsGroundedDestinationToPlanGrasp(t *testing.T) {
	template := taskgraph.TaskPlan{
		Steps: []taskgraph.SkillStep{{
			ID:        "plan",
			Skill:     "plan_grasp",
			Arguments: map[string]any{"objectId": "@object"},
		}},
	}
	grounded := manipulation.GroundedTask{
		Object:      manipulation.SceneRef{ID: "red-cup"},
		Destination: manipulation.SceneRef{ID: "right-bin"},
	}

	plan, err := materializePlanTemplate(
		template,
		"task-plan-destination",
		grounded,
		time.Now().Add(time.Minute),
	)
	if err != nil {
		t.Fatal(err)
	}
	arguments := plan.Steps[0].Arguments
	if arguments["objectId"] != "red-cup" || arguments["destinationId"] != "right-bin" {
		t.Fatalf("plan_grasp arguments = %#v", arguments)
	}
}
