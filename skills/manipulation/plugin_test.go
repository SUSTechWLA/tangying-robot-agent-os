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

func TestPrepareSimulationPlanUsesRegisteredTools(t *testing.T) {
	plan := manipulation.PrepareSimulationPlan("task-1", "robot-1", time.Now().Add(time.Minute))
	got := make([]string, 0, len(plan.Steps))
	for _, step := range plan.Steps {
		got = append(got, step.Skill)
	}
	want := []string{"simulation.reset_episode", "observe_scene", "verify_episode_ready"}
	if !slices.Equal(got, want) {
		t.Fatalf("skills=%v", got)
	}
	if plan.Steps[0].SafetyLevel != "physical_motion" || plan.Steps[0].IdempotencyKey == "" {
		t.Fatalf("reset safety envelope=%#v", plan.Steps[0])
	}
}
