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
