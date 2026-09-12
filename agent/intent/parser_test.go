package intent_test

import (
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

func TestSeveralObjectsBecomeASequenceRatherThanHalfAnInstruction(t *testing.T) {
	// A request naming two objects must produce two transfers. Reading only the
	// first - which this parser used to do - moves one object while reporting the
	// whole instruction done, and nobody rechecks a task that says it succeeded.
	parser := intent.NewDeterministicParser()
	parsed, err := parser.Parse("从客厅出发，去厨房拿红色杯子放进蓝色收纳盒，再拿蓝色杯子放进蓝色收纳盒，然后回到客厅")
	if err != nil {
		t.Fatalf("a two-object request should be understood: %v", err)
	}
	if len(parsed.Sequence) != 2 {
		t.Fatalf("expected two transfers, got %d: %#v", len(parsed.Sequence), parsed.Sequence)
	}
	if parsed.Sequence[0].Object.Attributes["color"] != "red" ||
		parsed.Sequence[1].Object.Attributes["color"] != "blue" {
		t.Fatalf("objects were not carried in order: %#v", parsed.Sequence)
	}
	// Every transfer carries the route. Attaching it only to the first looked like
	// an optimisation, but the planner builds navigation from the intent's own
	// RouteRooms, so the later transfers planned nothing beyond an observation and
	// the task reported success after moving one object.
	for index, transfer := range parsed.Sequence {
		if len(transfer.RouteRooms) == 0 {
			t.Fatalf("transfer %d has no route, so it cannot plan navigation: %#v", index, transfer)
		}
	}
	// Only the last one returns, or the robot would drive home between objects.
	if parsed.Sequence[0].ReturnToStart {
		t.Fatal("the first transfer must not return home before the second runs")
	}
	if !parsed.Sequence[len(parsed.Sequence)-1].ReturnToStart {
		t.Fatal("the last transfer must carry the return instruction")
	}
}

func TestObjectsListedTogetherShareOneDestination(t *testing.T) {
	parser := intent.NewDeterministicParser()
	parsed, err := parser.Parse("从客厅出发，去厨房拿红色杯子和蓝色杯子放进蓝色收纳盒，然后回到客厅")
	if err != nil {
		t.Fatalf("a conjunction should be understood: %v", err)
	}
	if len(parsed.Sequence) != 2 {
		t.Fatalf("expected two transfers, got %d", len(parsed.Sequence))
	}
	for index, want := range []string{"red", "blue"} {
		if parsed.Sequence[index].Object.Attributes["color"] != want {
			t.Fatalf("transfer %d: got %#v", index, parsed.Sequence[index].Object)
		}
	}
}

func TestAnObjectAndItsDestinationMayBeInDifferentClauses(t *testing.T) {
	// "拿红色杯子，放进蓝色收纳盒" splits into two clauses with one half each. Objects
	// are held until a clause supplies a destination rather than requiring both.
	parser := intent.NewDeterministicParser()
	parsed, err := parser.Parse("从客厅出发，去厨房拿红色杯子，放进蓝色收纳盒，然后回到客厅")
	if err != nil {
		t.Fatalf("split clauses should be understood: %v", err)
	}
	if parsed.Object.Attributes["color"] != "red" || parsed.Destination.Category != "storage_bin" {
		t.Fatalf("object and destination were not paired: %#v", parsed)
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
