package agent

import (
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"testing"
)

func TestModelNavigationProducesSemanticRouteWithoutCoordinates(t *testing.T) {
	value, err := parseToolCall("navigate_route", `{"rooms":["kitchen","workbench"],"return_to_start":true}`)
	if err != nil {
		t.Fatal(err)
	}
	if value.Action != manipulation.ActionHomeRoute || len(value.RouteRooms) != 2 || value.RouteRooms[1] != "workbench" || !value.ReturnToStart {
		t.Fatalf("lost route: %+v", value)
	}
}

func TestModelCannotHideUnknownFieldsOrCoordinatesInToolCall(t *testing.T) {
	for _, args := range []string{
		`{"rooms":["kitchen"],"approvalId":"fake"}`,
		`{"rooms":["kitchen"],"goalPose":[1,2,3]}`,
		`{"rooms":[]}`, `{"rooms":[""]}`, `{"rooms":[1]}`,
	} {
		if _, err := parseToolCall("navigate_route", args); err == nil {
			t.Fatalf("accepted %s", args)
		}
	}
	if _, err := parseToolCall("fetch", `{"object":{"category":"cup","color":"red","weight":100}}`); err == nil {
		t.Fatal("silently discarded an unknown object constraint")
	}
}
