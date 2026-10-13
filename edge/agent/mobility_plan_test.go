package agent

import (
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"testing"
	"time"
)

func TestControllerPreparedModelRouteCannotIntroduceGroundPreposition(t *testing.T) {
	grounded := groundedRoute()
	grounded.Action = manipulation.ActionHomeRoute
	grounded.NavigationPreparation = "controller"
	template := taskgraph.TaskPlan{Steps: []taskgraph.SkillStep{{ID: "go", Skill: "navigation.navigate", Arguments: map[string]any{"goalPose": "kitchen"}}, {ID: "check", Skill: "verify_arrival", DependsOn: []string{"go"}, Arguments: map[string]any{"goalPose": "kitchen"}}}}
	plan, err := materializePlanTemplate(template, "test", grounded, time.Now().Add(time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Steps) != 3 || plan.Steps[0].Skill != "observe_scene" {
		t.Fatalf("invalid controller preparation: %+v", plan.Steps)
	}
	if plan.Steps[1].DependsOn[0] != plan.Steps[0].ID {
		t.Fatal("navigation can bypass observation")
	}
	template.Steps = append([]taskgraph.SkillStep{{ID: "ground-only", Skill: "navigation.pre_position"}}, template.Steps...)
	if _, err := materializePlanTemplate(template, "test", grounded, time.Now().Add(time.Minute)); err == nil {
		t.Fatal("model silently introduced ground preparation")
	}
}
