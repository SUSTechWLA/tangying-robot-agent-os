package intent_test

import (
	"errors"
	"reflect"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
)

func TestRoomInspectionSuffixPreservesNamedReturn(t *testing.T) {
	for _, verb := range []string{"巡检", "巡查"} {
		request := "从客厅出发，去厨房" + verb + "，然后回到客厅"
		got, err := intent.NewDeterministicParser().Parse(request)
		if err != nil || got.ReturnToStart || !reflect.DeepEqual(got.RouteRooms, []string{"living_room", "kitchen", "living_room"}) {
			t.Fatalf("inspection suffix fell through or lost return: request=%q route=%+v err=%v", request, got, err)
		}
	}
	for _, request := range []string{
		"从客厅出发，去厨房巡检温度，然后回到客厅",
		"从客厅出发，去厨房巡检，然后打开烤箱",
		"从客厅出发，去厨房巡检并关闭电源，然后回到客厅",
	} {
		if _, err := intent.NewDeterministicParser().Parse(request); !errors.Is(err, intent.ErrClarificationRequired) {
			t.Fatalf("unknown inspection action was discarded: %q err=%v", request, err)
		}
	}
}
