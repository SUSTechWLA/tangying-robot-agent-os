package intent_test

import (
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestParseSharedBlockHandoff(t *testing.T) {
	parsed, err := intent.NewDeterministicParser().Parse("让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区")
	if err != nil {
		t.Fatal(err)
	}
	intents := parsed.Tasks()
	if len(intents) != 2 || intents[0].RobotID != "robot-1" || intents[1].RobotID != "robot-2" {
		t.Fatalf("intents=%#v", intents)
	}
	if intents[0].Destination.Category != manipulation.CategoryHandoffZone {
		t.Fatalf("sender destination=%#v", intents[0].Destination)
	}
	if intents[1].Source.Category != manipulation.CategoryHandoffZone ||
		intents[1].Destination.Category != manipulation.CategoryTargetZone ||
		intents[1].Destination.Relation != "right_side" {
		t.Fatalf("receiver source/destination=%#v %#v", intents[1].Source, intents[1].Destination)
	}
}

func TestParserUnderstandsChinesePickAndPlace(t *testing.T) {
	got, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != "pick_and_place" || got.Object.Attributes["color"] != "red" {
		t.Fatalf("intent = %+v", got)
	}
	if got.Destination.Relation != "right_side" {
		t.Fatalf("destination relation = %q", got.Destination.Relation)
	}
}

// People point at containers ("右边那个盒子") instead of naming the catalogue
// phrase ("右侧收纳盒"). Both must resolve to the same commissioned container;
// this is phrasing, not a wider set of supported destinations.
func TestParserUnderstandsDemonstrativeDestinations(t *testing.T) {
	for _, request := range []string{
		"把红色杯子放到右边那个盒子里",
		"麻烦把红色杯子放到右边那个盒子里",
		"请把红色杯子放进右侧那个收纳盒",
		"把红色杯子放进这个箱子里",
		"把红色杯子放进左边那个盒子",
	} {
		got, err := intent.NewDeterministicParser().Parse(request)
		if err != nil {
			t.Fatalf("%q: %v", request, err)
		}
		if got.Object.Category != "cup" || got.Object.Attributes["color"] != "red" {
			t.Fatalf("%q object = %+v", request, got.Object)
		}
		if got.Destination.Category != manipulation.CategoryStorageBin {
			t.Fatalf("%q destination = %+v", request, got.Destination)
		}
	}
	locational, err := intent.NewDeterministicParser().Parse("把红色杯子放到右边那个盒子里")
	if err != nil {
		t.Fatal(err)
	}
	if locational.Destination.Relation != "right_side" {
		t.Fatalf("right demonstrative relation = %q", locational.Destination.Relation)
	}
	left, err := intent.NewDeterministicParser().Parse("把红色杯子放进左边那个盒子")
	if err != nil {
		t.Fatal(err)
	}
	if left.Destination.Relation != "left_side" {
		t.Fatalf("left demonstrative relation = %q", left.Destination.Relation)
	}
	// A demonstrative names no side, so the destination stays relation-free and
	// the scene must still be checked for a unique container.
	unsided, err := intent.NewDeterministicParser().Parse("把红色杯子放进这个箱子里")
	if err != nil {
		t.Fatal(err)
	}
	if unsided.Destination.Relation != "" {
		t.Fatalf("unsided demonstrative invented a side: %q", unsided.Destination.Relation)
	}
	// "那个盒子" must not widen what counts as a container.
	if _, err := intent.NewDeterministicParser().Parse("把红色杯子放进那个冰箱里"); err == nil {
		t.Fatal("an unsupported appliance became a container")
	}
}

func TestParserRejectsUnsupportedIntent(t *testing.T) {
	if _, err := intent.NewDeterministicParser().Parse("帮我做晚饭"); err == nil {
		t.Fatal("unsupported request should fail closed")
	}
}

func TestParserUnderstandsChineseFetch(t *testing.T) {
	got, err := intent.NewDeterministicParser().Parse("让xlerobot把红色杯子拿过来")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != "fetch" || got.Object.Attributes["color"] != "red" {
		t.Fatalf("intent = %+v", got)
	}
	if got.Destination.Category != "delivery_tray" || got.Destination.Relation != "front_side" {
		t.Fatalf("destination = %+v", got.Destination)
	}
}

func TestParserUnderstandsEnglishFetch(t *testing.T) {
	got, err := intent.NewDeterministicParser().Parse("bring me the blue cup")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != "fetch" || got.Object.Attributes["color"] != "blue" {
		t.Fatalf("intent = %+v", got)
	}
}

func TestParserUnderstandsChineseBottleAndBlock(t *testing.T) {
	parser := intent.NewDeterministicParser()
	got, err := parser.Parse("把绿色瓶子放进左侧收纳盒")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != "pick_and_place" || got.Object.Category != "bottle" || got.Destination.Relation != "left_side" {
		t.Fatalf("intent = %+v", got)
	}
	got, err = parser.Parse("把红色积木拿过来")
	if err != nil {
		t.Fatal(err)
	}
	if got.Action != "fetch" || got.Object.Category != "block" {
		t.Fatalf("intent = %+v", got)
	}
}

func TestParserUnderstandsCompoundRequestAsOrderedSequence(t *testing.T) {
	got, err := intent.NewDeterministicParser().Parse("把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来")
	if err != nil {
		t.Fatal(err)
	}
	tasks := got.Tasks()
	if len(tasks) != 2 {
		t.Fatalf("tasks = %+v", tasks)
	}
	if tasks[0].Action != "pick_and_place" || tasks[0].Destination.Relation != "right_side" {
		t.Fatalf("first = %+v", tasks[0])
	}
	if tasks[1].Action != "fetch" || tasks[1].Object.Category != "bottle" {
		t.Fatalf("second = %+v", tasks[1])
	}
}

func TestTwoObjectRequestsAreRefusedRatherThanPartiallyExecuted(t *testing.T) {
	// The dangerous outcome is not a failure, it is success. Asked to move two cups
	// and given a grammar that describes one transfer, taking the first match and
	// ignoring the rest moves one object while reporting the whole instruction done.
	// Nobody rechecks a task that says it succeeded, so this must be refused.
	parser := intent.NewDeterministicParser()
	for _, request := range []string{
		"从客厅出发，去厨房拿红色杯子放进蓝色收纳盒，再拿蓝色杯子放进蓝色收纳盒，然后回到客厅",
		"去厨房拿红色杯子和蓝色杯子放进蓝色收纳盒",
		"从客厅去厨房拿红色杯子，再拿蓝色杯子，放进蓝色收纳盒",
	} {
		parsed, err := parser.Parse(request)
		if err == nil {
			t.Fatalf("%q was accepted as %#v; it must be refused, not partially executed", request, parsed)
		}
		if parsed.Action != "" {
			t.Fatalf("%q produced an action despite the error: %#v", request, parsed)
		}
		// Any clarification is acceptable; what matters is that the request is
		// refused with something a person can act on rather than half executed.
		if !strings.Contains(err.Error(), "clarification") {
			t.Fatalf("%q was refused without asking for clarification: %v", request, err)
		}
	}
}

func TestASingleTransferStillParsesWithItsOwnColours(t *testing.T) {
	// The guard must not cost the working case: one object, one destination.
	parser := intent.NewDeterministicParser()
	for request, colour := range map[string]string{
		"从客厅出发，去厨房拿红色杯子放进蓝色收纳盒，然后回到客厅": "red",
		"从客厅出发，去厨房拿蓝色杯子放进蓝色收纳盒，然后回到客厅": "blue",
		"从客厅出发，去厨房拿绿色杯子放进蓝色收纳盒，然后回到客厅": "green",
	} {
		parsed, err := parser.Parse(request)
		if err != nil {
			t.Fatalf("%q should parse: %v", request, err)
		}
		if parsed.Object.Attributes["color"] != colour {
			t.Fatalf("%q grounded %q, expected %q", request, parsed.Object.Attributes["color"], colour)
		}
	}
}
