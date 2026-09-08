package robotclient

import (
	"math"
	"reflect"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

func TestNavigationTypedPoseSurvivesProtobufBoundary(t *testing.T) {
	pose := []float64{0, .05, .035, math.Sqrt(.5), 0, 0, math.Sqrt(.5)}
	wire, err := commandToProto(runtime.Command{Parameters: map[string]any{"goalPose": pose}}, "simulation")
	if err != nil {
		t.Fatal(err)
	}
	values := wire.Parameters.Fields["goalPose"].GetListValue().Values
	got := make([]float64, len(values))
	for i, value := range values {
		got[i] = value.GetNumberValue()
	}
	if !reflect.DeepEqual(got, pose) {
		t.Fatalf("navigation pose changed across protobuf: %v", got)
	}
	pose[0] = 1
	if values[0].GetNumberValue() != 0 {
		t.Fatal("wire parameters alias the mutable planner pose")
	}
}

func TestInvalidTypedParametersNeverProduceCommand(t *testing.T) {
	for _, value := range []any{[]float64{math.NaN()}, []float64{math.Inf(1)}, func() {}} {
		if _, err := commandToProto(runtime.Command{Parameters: map[string]any{"goalPose": value}}, "simulation"); err == nil {
			t.Fatal("invalid parameters produced an executable protobuf command")
		}
	}
}
