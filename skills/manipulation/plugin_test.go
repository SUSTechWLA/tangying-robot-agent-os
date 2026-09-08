package manipulation_test

import (
	"slices"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestPlanGraspRequiresAndReceivesDestination(t *testing.T) {
	var required []string
	for _, manifest := range manipulation.Catalog() {
		if manifest.Name == "plan_grasp" {
			required = manifest.RequiredParameters
		}
	}
	if !slices.Contains(required, "objectId") || !slices.Contains(required, "destinationId") {
		t.Fatalf("plan_grasp required parameters = %v", required)
	}

	plan := manipulation.Plan(manipulation.GroundedTask{
		TaskID:      "task-plan-targets",
		Object:      manipulation.SceneRef{ID: "cup-1", Confidence: 0.95},
		Destination: manipulation.SceneRef{ID: "bin-1", Confidence: 0.96},
	}, time.Now().Add(time.Minute))
	for _, step := range plan.Steps {
		if step.Skill == "plan_grasp" {
			if step.Arguments["objectId"] != "cup-1" || step.Arguments["destinationId"] != "bin-1" {
				t.Fatalf("plan_grasp arguments = %#v", step.Arguments)
			}
			return
		}
	}
	t.Fatal("plan_grasp step not found")
}

func TestPlanRequiresApprovalForPhysicalSteps(t *testing.T) {
	plan := manipulation.Plan(manipulation.GroundedTask{
		TaskID:      "task-1",
		Object:      manipulation.SceneRef{ID: "cup-1", Confidence: 0.95},
		Destination: manipulation.SceneRef{ID: "bin-1", Confidence: 0.96},
	}, time.Now().Add(time.Minute))
	for _, step := range plan.Steps {
		if step.Skill == "manipulation.pick" || step.Skill == "manipulation.place" {
			if step.ApprovalID == "" || step.LeaseMS == 0 || step.IdempotencyKey == "" {
				t.Fatalf("physical step missing controls: %+v", step)
			}
		}
	}
}

func TestMobilePlanNavigatesAndReobservesBeforeManipulation(t *testing.T) {
	goal := []float64{0, .1, .035, 1, 0, 0, 0}
	plan := manipulation.Plan(manipulation.GroundedTask{TaskID: "mobile", StepIDPrefix: "task01-", NavigationGoal: goal}, time.Now().Add(time.Minute))
	var order []string
	for _, step := range plan.Steps {
		order = append(order, step.Skill)
		if step.Skill == "navigation.navigate" {
			if step.ApprovalID == "" || step.LeaseMS == 0 || step.IdempotencyKey == "" {
				t.Fatalf("uncontrolled navigation: %+v", step)
			}
			if !slices.Equal(step.Arguments["goalPose"].([]float64), goal) {
				t.Fatal("navigation goal changed")
			}
		}
		if step.Skill == "plan_grasp" && !slices.Contains(step.DependsOn, "task01-observe_after_navigation") {
			t.Fatal("grasp planning can bypass post-navigation observation")
		}
	}
	want := []string{"observe_scene", "resolve_targets", "navigation.navigate", "observe_scene", "plan_grasp", "manipulation.pick", "verify_grasp", "manipulation.place", "verify_placement"}
	if !slices.Equal(order, want) {
		t.Fatalf("mobile loop=%v", order)
	}
}
