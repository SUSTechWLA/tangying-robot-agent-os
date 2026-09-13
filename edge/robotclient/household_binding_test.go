package robotclient

import (
	"encoding/json"
	"math"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"google.golang.org/protobuf/types/known/structpb"
)

func householdSemanticClient(t *testing.T, objectArea, destinationArea string) *Client {
	t.Helper()
	client, _ := householdSemanticFixture(t, objectArea, destinationArea)
	return client
}

func householdSemanticFixture(t *testing.T, objectArea, destinationArea string) (*Client, *strictSceneServer) {
	t.Helper()
	client, server := strictScene(t)
	profile := server.info.RobotProfile.AsMap()
	profile["embodiment"] = "mobile_manipulator"
	profile["tools"] = []any{"observe_scene", "emergency_stop", "navigation.navigate"}
	profile["actionLimits"] = map[string]any{
		"navigation.x": map[string]any{"min": -4.0, "max": 4.0, "unit": "m"},
		"navigation.y": map[string]any{"min": -2.0, "max": 8.0, "unit": "m"},
		"navigation.z": map[string]any{"min": 0.0, "max": .1, "unit": "m"},
	}
	server.info.RobotProfile, _ = structpb.NewStruct(profile)
	server.info.Capabilities = append(server.info.Capabilities, &robotv1.CapabilityInfo{
		Name: "navigation.navigate", Available: true, SafetyLevel: "physical_motion",
	})
	state := semanticTestState()
	semantic := state["semantic_navigation"].(map[string]any)
	semantic["goals"] = map[string]any{
		"living_room": []any{0.0, 0.0, .035, 1.0, 0.0, 0.0, 0.0},
		"kitchen":     []any{2.0, 0.0, .035, 1.0, 0.0, 0.0, 0.0},
		"bedroom":     []any{-2.0, 0.0, .035, 1.0, 0.0, 0.0, 0.0},
		"bathroom":    []any{-2.0, 2.0, .035, 1.0, 0.0, 0.0, 0.0},
	}
	semantic["aliases"] = map[string]any{"厨房": "kitchen", "操作台": "kitchen"}
	state["base_pose"] = []any{0.0, 0.0, .035, 1.0, 0.0, 0.0, 0.0}
	state["semantic_objects"] = []any{
		map[string]any{"id": "ceramic-mug", "category": "cup", "attributes": map[string]any{}, "confidence": .93, "workArea": objectArea},
		map[string]any{"id": "kitchen-tray", "category": "storage_bin", "attributes": map[string]any{}, "confidence": .98, "workArea": destinationArea},
	}
	server.obs.RobotState, _ = structpb.NewStruct(state)
	return client, server
}

func TestNamedSameRoomReturnUsesSemanticGoalDespiteObservedStartOffset(t *testing.T) {
	namedGoal := []float64{0, -1.25, .035, math.Cos(math.Pi / 4), 0, 0, math.Sin(math.Pi / 4)}
	for _, start := range []struct {
		name string
		pose []any
	}{
		{"SLAM heading tolerance", []any{0.0, -1.25, .035, math.Cos((math.Pi/2 - .01) / 2), 0.0, 0.0, math.Sin((math.Pi/2 - .01) / 2)}},
		{"position offset", []any{.06, -1.20, .035, math.Cos(math.Pi / 4), 0.0, 0.0, math.Sin(math.Pi / 4)}},
		{"observed in another room", []any{2.0, 0.0, .035, 1.0, 0.0, 0.0, 0.0}},
	} {
		for _, test := range []struct {
			request string
			steps   int
		}{
			{"从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅", 12},
			{"从客厅出发，去厨房确认一下环境，然后回到客厅", 7},
		} {
			t.Run(start.name+"/"+test.request, func(t *testing.T) {
				parsed, err := intent.NewDeterministicParser().Parse(test.request)
				if err != nil {
					t.Fatal(err)
				}
				client, server := householdSemanticFixture(t, "kitchen", "kitchen")
				state := server.obs.RobotState.AsMap()
				state["base_pose"] = start.pose
				state["semantic_navigation"].(map[string]any)["goals"].(map[string]any)["living_room"] = []any{namedGoal[0], namedGoal[1], namedGoal[2], namedGoal[3], namedGoal[4], namedGoal[5], namedGoal[6]}
				server.obs.RobotState, _ = structpb.NewStruct(state)
				grounded, err := client.Ground(t.Context(), parsed)
				if err != nil {
					t.Fatal(err)
				}
				plan := manipulation.Plan(grounded, time.Now().Add(time.Minute))
				if len(plan.Steps) != test.steps || parsed.ReturnToStart || grounded.ReturnToStart ||
					!slices.Equal(grounded.RouteRooms, []string{"living_room", "kitchen", "living_room"}) {
					t.Fatalf("named room gained an exact observed-return step: steps=%d intent=%+v grounded=%+v", len(plan.Steps), parsed, grounded)
				}
				var finalGoal []float64
				for _, step := range plan.Steps {
					if step.Skill == "navigation.navigate" {
						finalGoal, _ = step.Arguments["goalPose"].([]float64)
					}
				}
				if !slices.Equal(finalGoal, namedGoal) {
					t.Fatalf("final heading/position is observed start instead of named room goal: %v", finalGoal)
				}
			})
		}
	}
}

func TestParsedHouseholdOperationCannotMoveToAnotherCatalogueRoom(t *testing.T) {
	for _, request := range []string{
		"从客厅出发，去卧室把杯子放进托盘，然后去厨房，最后回到客厅",
		"从客厅出发，去厨房确认一下环境，然后去卧室把杯子放进托盘，最后回到客厅",
		"从客厅出发，去卧室把杯子放进托盘，然后回到厨房",
	} {
		t.Run(request, func(t *testing.T) {
			parsed, err := intent.NewDeterministicParser().Parse(request)
			if err != nil {
				t.Fatal(err)
			}
			client := householdSemanticClient(t, "kitchen", "厨房")
			grounded, err := client.Ground(t.Context(), parsed)
			if err == nil || !strings.Contains(err.Error(), "operation checkpoint") || grounded.Action != "" {
				t.Fatalf("operation was silently relocated: grounded=%+v error=%v", grounded, err)
			}
		})
	}
}

func TestParsedGroundedPlanKeepsOperationAtTheSpecifiedVisit(t *testing.T) {
	for _, test := range []struct {
		request, area, arrival, next string
	}{
		{"从客厅出发，去卧室把杯子放进托盘，然后去厨房，最后回到客厅", "bedroom", "verify_arrival_01", "navigate_02"},
		{"从客厅出发，去厨房确认一下环境，然后去卧室把杯子放进托盘，最后回到客厅", "bedroom", "verify_arrival_02", "navigate_03"},
		{"从客厅出发，去厨房确认一下环境，然后去卧室，再去厨房把杯子放进托盘，最后回到客厅", "kitchen", "verify_arrival_03", "navigate_04"},
		{"从厨房出发，把杯子放进托盘，然后去客厅", "kitchen", "verify_arrival_00", "navigate_01"},
		{"从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅", "kitchen", "verify_arrival_01", "navigate_02"},
	} {
		t.Run(test.request, func(t *testing.T) {
			parsed, err := intent.NewDeterministicParser().Parse(test.request)
			if err != nil {
				t.Fatal(err)
			}
			client := householdSemanticClient(t, test.area, test.area)
			grounded, err := client.Ground(t.Context(), parsed)
			if err != nil {
				t.Fatal(err)
			}
			if grounded.ManipulationRouteIndex == nil || *grounded.ManipulationRouteIndex != *parsed.ManipulationRouteIndex {
				t.Fatalf("grounding dropped operation binding: %+v", grounded)
			}
			plan := manipulation.Plan(grounded, time.Now().Add(time.Minute))
			var ids []string
			for _, step := range plan.Steps {
				ids = append(ids, step.ID)
			}
			if slices.Index(ids, "pick") <= slices.Index(ids, test.arrival) || slices.Index(ids, "verify_place") >= slices.Index(ids, test.next) {
				t.Fatalf("operation moved across route visits: %v", ids)
			}
			if err := plan.ValidateShape(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestParsedExplicitFinalRoomIsLastGroundedAndPlannedGoal(t *testing.T) {
	for _, request := range []string{
		"从客厅出发，去厨房把杯子放进托盘，然后回到卧室",
		"从客厅出发，去厨房确认一下环境，然后回到卧室",
	} {
		t.Run(request, func(t *testing.T) {
			parsed, err := intent.NewDeterministicParser().Parse(request)
			if err != nil {
				t.Fatal(err)
			}
			client := householdSemanticClient(t, "厨房", "操作台")
			grounded, err := client.Ground(t.Context(), parsed)
			if err != nil {
				t.Fatal(err)
			}
			if grounded.ReturnToStart || !slices.Equal(grounded.RouteRooms, []string{"living_room", "kitchen", "bedroom"}) {
				t.Fatalf("explicit endpoint gained an observed-start goal: %+v", grounded)
			}
			plan := manipulation.Plan(grounded, time.Now().Add(time.Minute))
			var finalGoal []float64
			for _, step := range plan.Steps {
				if step.Skill == "navigation.navigate" {
					finalGoal, _ = step.Arguments["goalPose"].([]float64)
				}
			}
			if !slices.Equal(finalGoal, []float64{-2, 0, .035, 1, 0, 0, 0}) {
				t.Fatalf("planner changed the explicit bedroom endpoint: %v", finalGoal)
			}
		})
	}
}

func TestGroundedRouteBindingKeepsAliasesZeroAndOmissionDistinct(t *testing.T) {
	for _, index := range []*int{nil, new(int)} {
		client := householdSemanticClient(t, "厨房", "操作台")
		parsed := manipulation.Intent{
			Action: manipulation.ActionHomeManipulation, RouteRooms: []string{"厨房"},
			Object: manipulation.EntitySelector{Category: "cup"}, Destination: manipulation.EntitySelector{Category: "storage_bin"},
			ManipulationRouteIndex: index,
		}
		raw, err := json.Marshal(parsed)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(raw), "manipulationRouteIndex") != (index != nil) {
			t.Fatalf("zero and absent operation binding serialized identically: %s", raw)
		}
		grounded, err := client.Ground(t.Context(), parsed)
		if err != nil || (grounded.ManipulationRouteIndex == nil) != (index == nil) || grounded.Object.WorkArea != "kitchen" {
			t.Fatalf("legacy omission or zero/alias binding was lost: %+v %v", grounded, err)
		}
	}
	for _, index := range []int{-1, 1} {
		client := householdSemanticClient(t, "kitchen", "kitchen")
		_, err := client.Ground(t.Context(), manipulation.Intent{
			Action: manipulation.ActionHomeManipulation, RouteRooms: []string{"kitchen"},
			Object: manipulation.EntitySelector{Category: "cup"}, Destination: manipulation.EntitySelector{Category: "storage_bin"},
			ManipulationRouteIndex: &index,
		})
		if err == nil || !strings.Contains(err.Error(), "index") {
			t.Fatalf("out-of-range route binding was accepted: index=%d error=%v", index, err)
		}
	}
}
