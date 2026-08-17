package compiler_test

import (
	"reflect"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/compiler"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

func TestCompilerProducesStableTopologicalOrder(t *testing.T) {
	grounded := manipulation.GroundedTask{
		TaskID:      "task-1",
		Object:      manipulation.SceneRef{ID: "cup-1", Confidence: 0.95},
		Destination: manipulation.SceneRef{ID: "bin-1", Confidence: 0.96},
	}
	graph, err := compiler.New().Compile(manipulation.Plan(grounded, time.Now().Add(time.Minute)))
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"observe", "resolve", "plan_grasp", "pick", "verify_grasp", "place", "verify_place"}
	if !reflect.DeepEqual(graph.Order, want) {
		t.Fatalf("order = %v, want %v", graph.Order, want)
	}
}
