package intent_test

import (
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
	if got.Action != manipulation.ActionHomeRoute || !got.ReturnToStart || len(got.RouteRooms) != 3 {
		t.Fatalf("home inspection = %+v", got)
	}
}

func TestHomeRoutePlanContainsResumableRoomCheckpoints(t *testing.T) {
	plan := manipulation.Plan(manipulation.GroundedTask{
		TaskID: "home-route", Action: manipulation.ActionHomeRoute,
		RouteRooms: []string{"living_room", "kitchen", "living_room"},
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
