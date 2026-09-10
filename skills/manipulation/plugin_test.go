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

func TestHomeManipulationPlanRunsNavigationManipulationAndReturnAsIndependentTools(t *testing.T) {
	plan := manipulation.Plan(manipulation.GroundedTask{
		TaskID: "home-task", Action: manipulation.ActionHomeManipulation,
		RouteRooms:  []string{"living_room", "kitchen", "living_room"},
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: .95},
		Destination: manipulation.SceneRef{ID: "kitchen-bin", Confidence: .94},
	}, time.Now().Add(time.Minute))
	var order []string
	for _, step := range plan.Steps {
		order = append(order, step.Skill)
		if step.Skill == "navigation.navigate" {
			if step.ApprovalID == "" || step.LeaseMS == 0 || step.IdempotencyKey == "" {
				t.Fatalf("uncontrolled home navigation: %+v", step)
			}
			if _, ok := step.Arguments["goalPose"]; !ok {
				t.Fatalf("home navigation goal missing: %+v", step.Arguments)
			}
		}
	}
	want := []string{"observe_scene", "navigation.navigate", "verify_arrival", "observe_scene", "resolve_targets", "plan_grasp", "manipulation.pick", "verify_grasp", "manipulation.place", "verify_placement", "navigation.navigate", "verify_arrival"}
	if !slices.Equal(order, want) {
		t.Fatalf("home manipulation loop=%v", order)
	}
	if plan.Domain != "home_task" {
		t.Fatalf("home manipulation domain=%q", plan.Domain)
	}
	for _, step := range plan.Steps {
		if step.Skill == "plan_grasp" && !slices.Contains(step.DependsOn, "observe_after_navigation") {
			t.Fatalf("grasp planning bypasses kitchen RGB-D observation: %+v", step)
		}
	}
}

func TestHomeManipulationKeepsIntermediateRoomsBeforeKitchenArmWork(t *testing.T) {
	plan := manipulation.Plan(manipulation.GroundedTask{
		TaskID: "home-route", Action: manipulation.ActionHomeManipulation,
		RouteRooms:  []string{"living_room", "home_corridor", "kitchen", "living_room"},
		Object:      manipulation.SceneRef{ID: "red-cup", Confidence: .95},
		Destination: manipulation.SceneRef{ID: "kitchen-bin", Confidence: .94},
	}, time.Now().Add(time.Minute))
	var ids []string
	for _, step := range plan.Steps {
		ids = append(ids, step.ID)
	}
	for index, want := range []string{"navigate_01", "verify_arrival_01", "navigate_02", "verify_arrival_02", "observe_after_navigation"} {
		if ids[index+1] != want {
			t.Fatalf("intermediate-room plan ids=%v, expected %q at index %d", ids, want, index+1)
		}
	}
	if slices.Index(ids, "plan_grasp") <= slices.Index(ids, "verify_arrival_02") {
		t.Fatalf("grasp planning must follow kitchen arrival: ids=%v", ids)
	}
	if slices.Index(ids, "navigate_03") <= slices.Index(ids, "verify_placement") {
		t.Fatalf("return navigation must follow placement verification: ids=%v", ids)
	}
}

func TestNavigationBudgetAllowsLongApproachWithoutExtendingManipulationLeases(t *testing.T) {
	plan := manipulation.Plan(manipulation.GroundedTask{TaskID: "mobile", NavigationGoal: []float64{0, .05, .035, 1, 0, 0, 0}}, time.Now().Add(time.Minute))
	leases := make(map[string]uint32)
	for _, manifest := range manipulation.Catalog() {
		leases[manifest.Name] = manifest.DefaultLeaseMS
	}
	for _, step := range plan.Steps {
		if step.Skill == "navigation.navigate" {
			if step.LeaseMS != 60_000 || leases[step.Skill] != step.LeaseMS {
				t.Fatalf("navigation must have one bounded 60s task budget, got %d / %d", step.LeaseMS, leases[step.Skill])
			}
		} else if step.Skill == "manipulation.pick" || step.Skill == "manipulation.place" {
			if step.LeaseMS != 15_000 || leases[step.Skill] != step.LeaseMS {
				t.Fatalf("manipulation lease changed: %s / %d", step.Skill, step.LeaseMS)
			}
		}
	}
}
