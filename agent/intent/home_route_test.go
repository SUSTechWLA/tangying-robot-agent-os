package intent_test

import (
	"errors"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestParserUnderstandsHomeRoute(t *testing.T) {
	got, err := intent.NewDeterministicParser().Parse("从客厅出发，去厨房确认一下环境")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != manipulation.ActionHomeRoute || len(got.RouteRooms) != 2 || got.RouteRooms[0] != "living_room" || got.RouteRooms[1] != "kitchen" {
		t.Fatalf("home route = %+v", got)
	}
}

func TestParserUnderstandsHomeInspectionReturn(t *testing.T) {
	got, err := intent.NewDeterministicParser().Parse("巡检卧室和卫生间，最后回到客厅")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != manipulation.ActionHomeRoute || got.ReturnToStart || len(got.RouteRooms) != 3 || got.RouteRooms[2] != "living_room" {
		t.Fatalf("home inspection = %+v", got)
	}
}

func TestParserUnderstandsHomeManipulationTransfer(t *testing.T) {
	got, err := intent.NewDeterministicParser().Parse("从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != manipulation.ActionHomeManipulation {
		t.Fatalf("home manipulation action = %q", got.Action)
	}
	if len(got.RouteRooms) != 3 || got.RouteRooms[0] != "living_room" || got.RouteRooms[1] != "kitchen" || got.RouteRooms[2] != "living_room" {
		t.Fatalf("home manipulation route = %+v", got.RouteRooms)
	}
	if got.Object.Category != "cup" || got.Object.Attributes["color"] != "red" {
		t.Fatalf("home manipulation object = %+v", got.Object)
	}
	if got.Destination.Category != manipulation.CategoryStorageBin || got.Destination.Attributes["color"] != "blue" {
		t.Fatalf("home manipulation destination = %+v", got.Destination)
	}
}

func TestHouseholdTransferDoesNotRequireColourCues(t *testing.T) {
	for _, request := range []string{
		"从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅",
		"从客厅去厨房，把马克杯放入托盘，然后回到客厅",
	} {
		got, err := intent.NewDeterministicParser().Parse(request)
		if err != nil {
			t.Fatalf("%s: %v", request, err)
		}
		if got.Action != manipulation.ActionHomeManipulation || got.Object.Category != "cup" ||
			got.Destination.Category != manipulation.CategoryStorageBin || got.ReturnToStart ||
			len(got.Object.Attributes) != 0 || len(got.Destination.Attributes) != 0 {
			t.Fatalf("%s: %+v", request, got)
		}
	}
}

func TestEverydayCupAndTrayCommands(t *testing.T) {
	for _, request := range []string{"把杯子放进收纳盘", "把马克杯放入托盘", "put the mug in the tray"} {
		got, err := intent.NewDeterministicParser().Parse(request)
		if err != nil || got.Action != manipulation.ActionPickAndPlace || got.Object.Category != "cup" || got.Destination.Category != manipulation.CategoryStorageBin {
			t.Fatalf("%s: %+v, %v", request, got, err)
		}
	}
}

func TestUnknownHouseholdManipulationCannotBecomeRouteOnlySuccess(t *testing.T) {
	_, err := intent.NewDeterministicParser().Parse("从客厅去厨房，把洗洁精放进水槽")
	if err == nil {
		t.Fatal("unknown household manipulation became an executable route")
	}
}

func TestHouseholdTransferRejectsEveryUnconsumedOperation(t *testing.T) {
	for _, request := range []string{
		"从客厅去厨房，把杯子放进托盘，然后把洗洁精放进水槽，然后回到客厅",
		"从客厅去厨房，把杯子放进托盘，然后打开烤箱，然后回到客厅",
		"从客厅去厨房，打开烤箱，然后把杯子放进托盘，然后回到客厅",
		"从客厅去厨房，把杯子放进托盘并打开烤箱，然后回到客厅",
		"从客厅去厨房，打开烤箱并把杯子放进托盘，然后回到客厅",
		"从客厅去厨房，把杯子和洗洁精放进托盘，然后回到客厅",
		"从客厅去厨房，拿杯子，检查烤箱，放进托盘，然后回到客厅",
		"从客厅去厨房，把杯子放进托盘，然后回到客厅并打开电视",
		"从客厅去书房，把杯子放进托盘，然后回到客厅",
		"从客厅去厨房，拿杯子，然后回到客厅，放进托盘",
	} {
		t.Run(request, func(t *testing.T) {
			got, err := intent.NewDeterministicParser().Parse(request)
			if !errors.Is(err, intent.ErrClarificationRequired) || got.Action != "" || len(got.Sequence) != 0 {
				t.Fatalf("partially understood request must not be executable: intent=%+v, error=%v", got, err)
			}
		})
	}
}

func TestHomeRouteCannotSilentlyDropAnApplianceOperation(t *testing.T) {
	for _, request := range []string{
		"从客厅去厨房，然后打开烤箱",
		"巡检卧室和卫生间并关闭水龙头，最后回到客厅",
	} {
		got, err := intent.NewDeterministicParser().Parse(request)
		if !errors.Is(err, intent.ErrClarificationRequired) || got.Action != "" {
			t.Fatalf("route discarded an unsupported operation: intent=%+v, error=%v", got, err)
		}
	}
}

func TestHouseholdTransferDoesNotDropDestinationRelations(t *testing.T) {
	for _, suffix := range []string{"下面", "下", "旁边", "上面", "上", "里面的盘子", "里和水槽里"} {
		for _, verb := range []string{"放在", "放到", "放进"} {
			for _, phrase := range []string{"把杯子" + verb + "右边那个托盘" + suffix, "拿杯子，" + verb + "右边那个托盘" + suffix} {
				request := "从客厅去厨房，" + phrase + "，然后回到客厅"
				t.Run(request, func(t *testing.T) {
					got, err := intent.NewDeterministicParser().Parse(request)
					if !errors.Is(err, intent.ErrClarificationRequired) || got.Action != "" {
						t.Fatalf("unsupported destination relation became inside: intent=%+v, error=%v", got, err)
					}
				})
			}
		}
	}
}

func TestHouseholdTransferConsumesInsideRelationAndSupportedRouteClauses(t *testing.T) {
	for _, request := range []string{
		"从客厅出发，前往厨房，把杯子放在右边那个托盘里面，最后回到客厅",
		"从客厅去厨房，拿杯子，放到右侧托盘里，最终返回客厅",
		"从客厅出发，去厨房拿杯子和蓝色杯子都放进右侧托盘内，然后回到客厅",
	} {
		t.Run(request, func(t *testing.T) {
			got, err := intent.NewDeterministicParser().Parse(request)
			if err != nil || got.Destination.Category != manipulation.CategoryStorageBin || got.Destination.Relation != "right_side" {
				t.Fatalf("supported complete destination was not preserved: intent=%+v, error=%v", got, err)
			}
		})
	}
}

func TestHouseholdOperationsKeepTheirExactRouteVisit(t *testing.T) {
	for _, test := range []struct {
		request string
		index   int
	}{
		{"从客厅出发，去卧室把杯子放进托盘，然后去厨房，最后回到客厅", 1},
		{"从客厅出发，去厨房确认一下环境，然后去卧室把杯子放进托盘，最后回到客厅", 2},
		{"从客厅出发，去厨房确认一下环境，然后去卧室，再去厨房把杯子放进托盘，最后回到客厅", 3},
		{"从厨房出发，把杯子放进托盘，然后去客厅", 0},
	} {
		t.Run(test.request, func(t *testing.T) {
			got, err := intent.NewDeterministicParser().Parse(test.request)
			if err != nil || got.ManipulationRouteIndex == nil || *got.ManipulationRouteIndex != test.index {
				t.Fatalf("operation lost its room/visit: intent=%+v error=%v", got, err)
			}
		})
	}
}

func TestNamedHouseholdEndpointDoesNotMeanObservedStartingPose(t *testing.T) {
	for _, request := range []string{
		"从客厅出发，去厨房把杯子放进托盘，然后回到客厅",
		"从客厅出发，去厨房确认一下环境，然后回到客厅",
		"从客厅出发，去厨房把杯子放进托盘，然后回到卧室",
		"从客厅出发，去厨房确认一下环境，然后回到卧室",
		"巡检卧室和卫生间，最后回到客厅",
	} {
		t.Run(request, func(t *testing.T) {
			got, err := intent.NewDeterministicParser().Parse(request)
			if err != nil || got.ReturnToStart {
				t.Fatalf("named endpoint became a return to observed start: intent=%+v error=%v", got, err)
			}
		})
	}
}

func TestDifferentRoomOperationsCannotBeFlattenedIntoSharedRoute(t *testing.T) {
	request := "从客厅出发，去厨房把杯子放进托盘，然后去卧室把瓶子放进盒子，最后回到客厅"
	got, err := intent.NewDeterministicParser().Parse(request)
	if !errors.Is(err, intent.ErrClarificationRequired) || got.Action != "" {
		t.Fatalf("distinct operation checkpoints were flattened: intent=%+v error=%v", got, err)
	}
}

func TestHomeRoutePlanContainsResumableRoomCheckpoints(t *testing.T) {
	plan := manipulation.Plan(manipulation.GroundedTask{
		TaskID: "home-route", Action: manipulation.ActionHomeRoute,
		RouteRooms: []string{"living_room", "kitchen", "living_room"},
		RouteGoals: [][]float64{
			{0, 0, 0, 1, 0, 0, 0},
			{1, 0, 0, 1, 0, 0, 0},
			{0, 0, 0, 1, 0, 0, 0},
		},
	}, time.Now().Add(time.Minute))
	if len(plan.Steps) != 7 {
		t.Fatalf("steps = %+v", plan.Steps)
	}
	if plan.Steps[0].Skill != "observe_scene" || plan.Steps[1].Skill != "navigation.navigate" || plan.Steps[2].Skill != "verify_arrival" {
		t.Fatalf("first room checkpoint = %+v", plan.Steps[:3])
	}
	if got := plan.Steps[1].Arguments["goalPose"]; got == nil {
		t.Fatalf("navigation goal pose missing = %+v", plan.Steps[1].Arguments)
	}
	if got := plan.Steps[2].Arguments["goalPose"]; got == nil {
		t.Fatalf("verification goal pose missing = %+v", plan.Steps[2].Arguments)
	}
	for _, key := range []string{"room", "routeSegmentIndex", "returnToStart"} {
		if _, ok := plan.Steps[1].Arguments[key]; ok {
			t.Fatalf("navigation arguments leaked semantic metadata %q: %+v", key, plan.Steps[1].Arguments)
		}
		if _, ok := plan.Steps[2].Arguments[key]; ok {
			t.Fatalf("verification arguments leaked semantic metadata %q: %+v", key, plan.Steps[2].Arguments)
		}
	}
}
