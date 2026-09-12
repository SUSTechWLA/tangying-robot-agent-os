package robotclient_test

import (
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/robotclient"
)

func TestClientFailsClosedWithoutTLSOrExplicitDevMode(t *testing.T) {
	if _, err := robotclient.New(robotclient.Config{Address: "127.0.0.1:50051"}); err == nil {
		t.Fatal("client without TLS must require DevInsecure")
	}
	client, err := robotclient.New(robotclient.Config{Address: "127.0.0.1:50051", DevInsecure: true})
	if err != nil {
		t.Fatal(err)
	}
	_ = client.Close()
}

func TestEveryCommissionedObjectGroundsToItsSceneEntity(t *testing.T) {
	// The table used to answer only for red/cup. A request naming a second object
	// then parsed correctly and failed to ground, so the first transfer ran to
	// completion and the task stopped at "home task object is not commissioned:
	// blue/cup" - a feature the grammar understood and the grounding table had never
	// heard of. This pins the table to what the household scene actually contains:
	// red, blue and green cups and a yellow plate.
	// A slice of pairs rather than a map: EntitySelector holds a map, so it cannot
	// be a map key.
	for _, entry := range []struct{ category, color, expected string }{
		{"cup", "red", "red-cup"},
		{"cup", "blue", "blue-cup"},
		{"cup", "green", "green-cup"},
		{"plate", "yellow", "yellow-plate"},
	} {
		selector := manipulation.EntitySelector{Category: entry.category,
			Attributes: map[string]string{"color": entry.color}}
		expected := entry.expected
		grounded, err := robotclient.GroundHomeObject(selector)
		if err != nil {
			t.Fatalf("%v should be commissioned: %v", selector, err)
		}
		if grounded != expected {
			t.Fatalf("%v grounded to %q, expected %q", selector, grounded, expected)
		}
	}
}

func TestAnObjectTheSceneDoesNotContainIsRefused(t *testing.T) {
	// The refusal has to stay, or a request for something that is not there would
	// ground to a plausible name and fail later at the gripper instead of here.
	for _, selector := range []manipulation.EntitySelector{
		{Category: "cup", Attributes: map[string]string{"color": "purple"}},
		{Category: "bowl", Attributes: map[string]string{"color": "red"}},
		{Category: "cup"},
	} {
		if grounded, err := robotclient.GroundHomeObject(selector); err == nil {
			t.Fatalf("%v must be refused, grounded to %q instead", selector, grounded)
		}
	}
}
