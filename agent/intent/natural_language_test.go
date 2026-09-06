package intent_test

import (
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
)

func TestNaturalLanguageRobotFramingAndAliases(t *testing.T) {
	for _, request := range []string{
		"请帮我让一号机器人把红色方块放到交接区。",
		"麻烦 1 号机器人将红色积木移到交接点。",
		"Robot 1, please move the red block to the handoff zone.",
		"请让机器人1号把红色方块放到交接区",
	} {
		t.Run(request, func(t *testing.T) {
			got, err := intent.NewDeterministicParser().Parse(request)
			if err != nil {
				t.Fatal(err)
			}
			if got.RobotID != "robot-1" || got.Object.Category != "block" || got.Object.Attributes["color"] != "red" || got.Destination.Category != "handoff_zone" {
				t.Fatalf("wrong meaning: %+v", got)
			}
		})
	}
}

func TestPronounsUseOnlyThePreviousClauseObjectAndDestination(t *testing.T) {
	parser := intent.NewDeterministicParser()
	for _, second := range []string{"把它放到右侧目标区", "放到右侧目标区"} {
		got, err := parser.Parse("先让一号机器人把红色方块放到交接区，然后让二号机器人" + second + "。")
		if err != nil {
			t.Fatal(err)
		}
		steps := got.Tasks()
		if len(steps) != 2 || steps[0].RobotID != "robot-1" || steps[1].RobotID != "robot-2" || steps[1].Object.Attributes["color"] != "red" || steps[1].Source.Category != "handoff_zone" || steps[1].Destination.Category != "target_zone" || steps[1].Destination.Relation != "right_side" {
			t.Fatalf("wrong sequence: %+v", steps)
		}
	}
	if _, err := parser.Parse("把它放到交接区"); !errors.Is(err, intent.ErrUnsupportedIntent) {
		t.Fatalf("standalone pronoun must request clarification: %v", err)
	}
	if _, err := parser.Parse("把红色方块放到交接区，然后把那个玩具放到右侧目标区"); err == nil {
		t.Fatal("explicit unknown object must not inherit the previous object")
	}
}

func TestObjectSourceAndDestinationAreParsedSeparately(t *testing.T) {
	parser := intent.NewDeterministicParser()
	for _, request := range []string{
		"把红色方块从右侧目标区放到交接区",
		"从右侧目标区把红色方块放到交接区",
	} {
		got, err := parser.Parse(request)
		if err != nil {
			t.Fatal(err)
		}
		if got.Source.Category != "target_zone" || got.Source.Relation != "right_side" || got.Destination.Category != "handoff_zone" {
			t.Fatalf("source became destination: %+v", got)
		}
	}
	for range 30 {
		got, err := parser.Parse("把红色方块放到右侧蓝色目标区")
		if err != nil {
			t.Fatal(err)
		}
		if got.Object.Attributes["color"] != "red" || got.Destination.Attributes["color"] != "blue" {
			t.Fatalf("object/destination colors mixed: %+v", got)
		}
	}
}

func TestNonExecutableOrPartlyUnderstoodRequestsNeverBecomeActions(t *testing.T) {
	for _, request := range []string{
		"把红色方块放到交接区是不允许的",
		"不要把红色方块放到交接区",
		"Do not put the red block into the right bin",
		"If the person leaves, put the red block into the right bin",
		"put the red block into the right bin unless someone is nearby",
		"put the red block into the right bin and open the door",
		"把红色和蓝色方块放到交接区",
		"把红色方块放到交接区或者右侧目标区",
		"让1号机器人把红色方块放进冰箱",
		"把红色杯子放在收纳盒上",
	} {
		t.Run(request, func(t *testing.T) {
			if _, err := intent.NewDeterministicParser().Parse(request); !errors.Is(err, intent.ErrUnsupportedIntent) {
				t.Fatalf("request must be rejected without guessing: %v", err)
			}
		})
	}
}
