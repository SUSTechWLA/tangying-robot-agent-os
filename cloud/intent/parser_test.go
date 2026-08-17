package intent_test

import (
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/intent"
)

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
