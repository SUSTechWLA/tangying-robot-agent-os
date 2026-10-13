package intent_test

import (
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
)

func TestHouseholdPlacementReferencesPendingObjectsWithoutPickingTwice(t *testing.T) {
	for _, request := range []string{
		"从客厅出发，去厨房拿杯子，把杯子放进收纳盘，然后回到客厅",
		"从客厅出发，去厨房拿起马克杯，将杯子放进收纳盘，然后回到客厅",
		"从客厅出发，去厨房拿红色杯子，把红色杯子放进收纳盘，然后回到客厅",
	} {
		got, err := intent.NewDeterministicParser().Parse(request)
		if err != nil || len(got.Sequence) != 0 || got.Object.Category != "cup" || len(got.RouteRooms) != 3 {
			t.Fatalf("expected exactly one complete transfer for %q: %+v, %v", request, got, err)
		}
	}
}

func TestHouseholdReferencePreservesExplicitTransferMultiplicity(t *testing.T) {
	for _, request := range []string{
		"从客厅出发，去厨房拿红色杯子和蓝色杯子，把红色杯子和蓝色杯子放进收纳盘，然后回到客厅",
		"从客厅出发，去厨房拿杯子放进收纳盘，再把杯子放进收纳盒，然后回到客厅",
		"从客厅出发，去厨房拿红色杯子，和蓝色杯子放进收纳盘，然后回到客厅",
	} {
		got, err := intent.NewDeterministicParser().Parse(request)
		if err != nil || len(got.Sequence) != 2 {
			t.Fatalf("expected two explicitly requested transfers for %q: %+v, %v", request, got, err)
		}
	}
}

func TestHouseholdConflictingPendingReferenceRequiresClarification(t *testing.T) {
	for _, request := range []string{
		"从客厅出发，去厨房拿红色杯子，把蓝色杯子放进收纳盘，然后回到客厅",
		"从客厅出发，去厨房拿杯子，把瓶子放进收纳盘，然后回到客厅",
		"从客厅出发，去厨房拿红色杯子和蓝色杯子，把红色杯子放进收纳盘，然后回到客厅",
	} {
		got, err := intent.NewDeterministicParser().Parse(request)
		if !errors.Is(err, intent.ErrClarificationRequired) || got.Action != "" {
			t.Fatalf("conflicting references must not authorize extra transfers for %q: %+v, %v", request, got, err)
		}
	}
}
