package robotclient

import (
	"slices"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	robotv1 "github.com/SUSTechWLA/tangying-robot-agent-os/gen/go/robot/v1"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"google.golang.org/protobuf/types/known/structpb"
)

func TestObservedReturnCoalescesOnlyTheSamePhysicalPose(t *testing.T) {
	start := []float64{0, -1.25, .035, 1, 0, 0, 0}
	for _, tc := range []struct {
		name  string
		last  []float64
		count int
	}{
		{"roundoff", []float64{1e-16, -1.25, .035, 1, 0, 0, 0}, 1},
		{"opposite quaternion", []float64{0, -1.25, .035, -1, 0, 0, 0}, 1},
		{"different point", []float64{.01, -1.25, .035, 1, 0, 0, 0}, 2},
		{"different heading", []float64{0, -1.25, .035, 0, 0, 0, 1}, 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rooms, goals := withObservedReturn([]string{"home"}, [][]float64{tc.last}, start)
			if len(rooms) != tc.count || len(goals) != tc.count || !slices.Equal(goals[len(goals)-1], start) {
				t.Fatalf("return did not preserve measured pose: %v %v", rooms, goals)
			}
		})
	}
}

func semanticTestInfo(adapter string) runtime.Snapshot {
	return runtime.Snapshot{
		Adapter: adapter,
		RobotID: "robot-a",
		RobotProfile: &robotcontract.Profile{ActionLimits: map[string]robotcontract.ActionLimit{
			"navigation.x": {Min: -4, Max: 4, Unit: "m"},
			"navigation.y": {Min: -2, Max: 8, Unit: "m"},
			"navigation.z": {Min: 0, Max: .1, Unit: "m"},
		}},
	}
}

func semanticTestState() map[string]any {
	return map[string]any{
		"active_map": map[string]any{
			"mapId": "map-a", "mapRevision": "map-rev-a", "calibrationRevision": "cal-a",
		},
		"semantic_navigation": map[string]any{
			"schemaVersion": "semantic.navigation.v1", "frameId": "world", "robotId": "robot-a",
			"mapId": "map-a", "mapRevision": "map-rev-a", "calibrationRevision": "cal-a",
			"goals": map[string]any{
				"workbench": []any{2.2, 3.35, .035, .7071067812, 0.0, 0.0, .7071067812},
			},
			"aliases": map[string]any{"操作台": "workbench"},
		},
		"semantic_objects": []any{
			map[string]any{"id": "red-cup", "category": "cup", "attributes": map[string]any{"color": "red"}, "confidence": .93, "workArea": "workbench"},
			map[string]any{"id": "blue-cup", "category": "cup", "attributes": map[string]any{"color": "blue"}, "confidence": .91, "workArea": "workbench"},
			map[string]any{"id": "blue-bin", "category": "storage_bin", "attributes": map[string]any{"color": "blue"}, "confidence": .98, "workArea": "workbench"},
		},
		"base_pose_frame": "world",
		"base_pose":       []any{1.0, 2.0, .035, 1.0, 0.0, 0.0, 0.0},
	}
}

func TestSemanticNavigationIsAdapterAndSimulationIndependent(t *testing.T) {
	for _, adapter := range []string{"mujoco", "custom_mobile", "vendor_bridge"} {
		for _, simulation := range []bool{false, true} {
			state := semanticTestState()
			state["simulation"] = simulation
			goals, err := semanticRouteGoals(semanticTestInfo(adapter), state, []string{"操作台"})
			if err != nil || len(goals) != 1 || goals[0][0] != 2.2 {
				t.Fatalf("adapter=%s simulation=%t: goals=%v err=%v", adapter, simulation, goals, err)
			}
		}
	}
}

func TestSemanticNavigationRejectsMissingOrMismatchedIdentity(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(map[string]any, map[string]any)
		field  string
	}{
		{"missing contract", func(state, _ map[string]any) { delete(state, "semantic_navigation") }, "contract"},
		{"wrong schema", func(_, semantic map[string]any) { semantic["schemaVersion"] = "semantic.navigation.v2" }, "schema"},
		{"wrong frame", func(_, semantic map[string]any) { semantic["frameId"] = "map" }, "frame"},
		{"wrong robot", func(_, semantic map[string]any) { semantic["robotId"] = "robot-b" }, "robot"},
		{"wrong map", func(_, semantic map[string]any) { semantic["mapId"] = "map-b" }, "mapId"},
		{"wrong map revision", func(_, semantic map[string]any) { semantic["mapRevision"] = "map-rev-b" }, "mapRevision"},
		{"wrong calibration", func(_, semantic map[string]any) { semantic["calibrationRevision"] = "cal-b" }, "calibrationRevision"},
	} {
		t.Run(test.name, func(t *testing.T) {
			state := semanticTestState()
			semantic := state["semantic_navigation"].(map[string]any)
			test.mutate(state, semantic)
			_, err := semanticRouteGoals(semanticTestInfo("any-adapter"), state, []string{"workbench"})
			if err == nil || !strings.Contains(err.Error(), test.field) {
				t.Fatalf("mismatch accepted or unclear error: %v", err)
			}
		})
	}
}

func TestSemanticNavigationValidatesGoalAndWorkspaceLimits(t *testing.T) {
	for _, test := range []struct {
		name string
		pose []any
	}{
		{"outside workspace", []any{20.0, 3.35, .035, 1.0, 0.0, 0.0, 0.0}},
		{"invalid quaternion", []any{2.2, 3.35, .035, 0.0, 0.0, 0.0, 0.0}},
		{"wrong length", []any{2.2, 3.35}},
		{"wrong type", []any{2.2, "3.35", .035, 1.0, 0.0, 0.0, 0.0}},
	} {
		t.Run(test.name, func(t *testing.T) {
			state := semanticTestState()
			state["semantic_navigation"].(map[string]any)["goals"].(map[string]any)["workbench"] = test.pose
			if _, err := semanticRouteGoals(semanticTestInfo("any-adapter"), state, []string{"workbench"}); err == nil {
				t.Fatal("invalid semantic goal became executable")
			}
		})
	}
}

func TestSemanticObjectsResolveUniquelyByFullSelector(t *testing.T) {
	state := semanticTestState()
	ref, err := semanticObjectRef(state, manipulation.EntitySelector{
		Category: "cup", Attributes: map[string]string{"color": "red"},
	})
	if err != nil || ref.ID != "red-cup" || ref.Confidence != .93 || ref.WorkArea != "workbench" {
		t.Fatalf("declared object did not ground: %+v %v", ref, err)
	}
	for _, selector := range []manipulation.EntitySelector{
		{Category: "cup"},
		{Category: "cup", Attributes: map[string]string{"color": "purple"}},
		{Category: "cup", Attributes: map[string]string{"color": "red"}, Relation: "inside:bin"},
	} {
		if _, err := semanticObjectRef(state, selector); err == nil {
			t.Fatalf("selector %+v did not fail closed", selector)
		}
	}
}

func TestRouteStartUsesOnlyObservedWorldPose(t *testing.T) {
	state := semanticTestState()
	start, err := routeStartPose(state)
	if err != nil || start[0] != 1 || start[1] != 2 {
		t.Fatalf("observed start: %v %v", start, err)
	}
	for _, mutate := range []func(map[string]any){
		func(state map[string]any) { delete(state, "base_pose") },
		func(state map[string]any) { state["base_pose_frame"] = "odom" },
		func(state map[string]any) { state["base_pose"] = []any{0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0} },
	} {
		state = semanticTestState()
		mutate(state)
		if _, err := routeStartPose(state); err == nil {
			t.Fatal("unobserved or invalid start pose became a return goal")
		}
	}
}

func TestClientGroundsRouteAndReferencesFromOneGenericContract(t *testing.T) {
	for _, adapter := range []string{"mujoco", "vendor-mobile"} {
		t.Run(adapter, func(t *testing.T) {
			client, server := strictScene(t)
			profile := server.info.RobotProfile.AsMap()
			profile["adapterId"] = adapter
			profile["embodiment"] = "mobile_manipulator"
			profile["tools"] = []any{"observe_scene", "emergency_stop", "navigation.navigate"}
			profile["actionLimits"] = map[string]any{
				"navigation.x": map[string]any{"min": -4.0, "max": 4.0, "unit": "m"},
				"navigation.y": map[string]any{"min": -2.0, "max": 8.0, "unit": "m"},
				"navigation.z": map[string]any{"min": 0.0, "max": .1, "unit": "m"},
			}
			server.info.RobotProfile, _ = structpb.NewStruct(profile)
			server.info.Adapter = adapter
			server.info.Capabilities = append(server.info.Capabilities, &robotv1.CapabilityInfo{
				Name: "navigation.navigate", Available: true, SafetyLevel: "physical_motion",
			})
			server.obs.RobotState, _ = structpb.NewStruct(semanticTestState())

			grounded, err := client.Ground(t.Context(), manipulation.Intent{
				Action: manipulation.ActionHomeManipulation,
				Object: manipulation.EntitySelector{
					Category: "cup", Attributes: map[string]string{"color": "red"},
				},
				Destination: manipulation.EntitySelector{
					Category: "storage_bin", Attributes: map[string]string{"color": "blue"},
				},
				RouteRooms: []string{"操作台"}, ReturnToStart: true,
			})
			if err != nil {
				t.Fatal(err)
			}
			if grounded.Object.ID != "red-cup" || grounded.Object.WorkArea != "workbench" ||
				grounded.Destination.ID != "blue-bin" || len(grounded.RouteGoals) != 2 ||
				grounded.RouteRooms[0] != "workbench" || grounded.RouteRooms[1] != "return_to_start" ||
				grounded.RouteGoals[1][0] != 1 || grounded.RouteGoals[1][1] != 2 {
				t.Fatalf("generic semantic grounding = %+v", grounded)
			}
		})
	}
}
