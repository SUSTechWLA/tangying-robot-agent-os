package orchestration_test

import (
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
)

// state is the shape the robot runtime actually publishes, taken from a live
// snapshot of the reference deployment.
func state() map[string]any {
	return map[string]any{
		"base_pose": []any{0.0, -1.25, 0.035, 0.707, 0.0, 0.0, 0.707},
		"semantic_navigation": map[string]any{
			"goals": map[string]any{
				"kitchen":     []any{2.05, 3.0, 0.035},
				"living_room": []any{0.0, -1.25, 0.035},
				"bedroom":     []any{-2.05, 3.35, 0.035},
			},
		},
		"semantic_objects": []any{
			map[string]any{"id": "ceramic-mug", "category": "cup", "workArea": "kitchen"},
			map[string]any{"id": "kitchen-tray", "category": "storage_bin", "workArea": "kitchen"},
		},
	}
}

func TestWorldReadsWhereTheRobotIsAndWhereThingsAre(t *testing.T) {
	world := orchestration.WorldFrom(state())
	if world.RobotRoom != "living_room" {
		t.Fatalf("robot room = %q", world.RobotRoom)
	}
	if rooms := world.Rooms("cup"); len(rooms) != 1 || rooms[0] != "kitchen" {
		t.Fatalf("cup rooms = %v", rooms)
	}
}

// The instruction that matters is the constraint, not the datum. A model told only
// "the cup is in the kitchen" may still plan to look from the living room; a model
// told it cannot see through walls will not.
func TestTheDescriptionStatesTheConstraint(t *testing.T) {
	described := orchestration.WorldFrom(state()).Describe()
	for _, want := range []string{"living_room", "cup in the kitchen", "cannot see through walls"} {
		if !strings.Contains(described, want) {
			t.Fatalf("description lacks %q:\n%s", want, described)
		}
	}
}

// An unknown world says so. Silently omitting the location would let the model
// assume the object is within reach, which is the bug this whole type exists for.
func TestAnUnknownWorldSaysItIsUnknown(t *testing.T) {
	world := orchestration.World{}
	if world.Known() {
		t.Fatal("an empty world reported itself as known")
	}
	described := world.Describe()
	if !strings.Contains(described, "unknown") {
		t.Fatalf("description = %q", described)
	}
	if !strings.Contains(described, "assumption") {
		t.Fatalf("an unknown world did not ask the planner to state its assumption:\n%s", described)
	}
}

// A robot far from every named goal is not in the nearest one.
func TestARobotNowhereNearAGoalHasNoRoom(t *testing.T) {
	remote := state()
	remote["base_pose"] = []any{500.0, 500.0, 0.0}
	if room := orchestration.WorldFrom(remote).RobotRoom; room != "" {
		t.Fatalf("a robot 700 m from every goal was placed in %q", room)
	}
}

// A state that cannot be read leaves the world unknown rather than defaulted.
func TestAnUnreadableStateIsUnknown(t *testing.T) {
	world := orchestration.WorldFrom(map[string]any{"base_pose": "not a pose"})
	if world.RobotRoom != "" {
		t.Fatalf("robot room = %q", world.RobotRoom)
	}
	if world.Known() {
		t.Fatal("an unreadable state reported itself as known")
	}
}

// Duplicate categories in one room collapse, so the prompt does not repeat itself.
func TestRepeatedObjectsCollapse(t *testing.T) {
	repeated := state()
	repeated["semantic_objects"] = []any{
		map[string]any{"category": "cup", "workArea": "kitchen"},
		map[string]any{"category": "cup", "workArea": "kitchen"},
		map[string]any{"category": "cup", "workArea": "bedroom"},
	}
	world := orchestration.WorldFrom(repeated)
	if len(world.Objects) != 2 {
		t.Fatalf("objects = %+v", world.Objects)
	}
	if rooms := world.Rooms("cup"); len(rooms) != 2 {
		t.Fatalf("rooms = %v", rooms)
	}
}
