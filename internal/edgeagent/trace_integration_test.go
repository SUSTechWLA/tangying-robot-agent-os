package edgeagent

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// Exercise the production Host child journal, including its advisory parent
// mirrors. The diagnostic API must load the actual child aggregate.
func TestHostTraceLoadsAuthoritativeChildAtomicCalls(t *testing.T) {
	robot := &missionRobot{}
	contract, decider := objectiveFixture(t, robot, 3)
	host, task, store := hostFixture(t, robot, contract, decider)
	if _, err := host.Run(context.Background(), task, nil, nil); err != nil {
		t.Fatal(err)
	}
	trace, err := tasks.NewService(store, nil).Trace(context.Background(), task.ID, store)
	if err != nil {
		t.Fatal(err)
	}
	if trace.OriginalRequest.Text != contract.Goal || len(trace.Children) != 1 {
		t.Fatalf("root identity lost: %+v", trace)
	}
	child := trace.Children[0]
	if child.Coverage != "found" || !child.BindingVerified || child.Task == nil || len(child.Steps) != 3 || len(child.RPCCalls) != 6 {
		t.Fatalf("child evidence missing: %+v", child)
	}
	for _, rpc := range child.RPCCalls {
		if len(rpc.EventSequences) != 2 || rpc.RobotID != "edge-test" || rpc.CommandID == "" {
			t.Fatalf("child RPC misbound: %+v", rpc)
		}
	}
	if directory := os.Getenv("TRACE_CAMPAIGN_OUTPUT"); directory != "" {
		if err = os.MkdirAll(directory, 0755); err != nil {
			t.Fatal(err)
		}
		data, _ := json.MarshalIndent(map[string]any{"evidenceClass": "software-contract-fixture", "scenario": "host-three-stations", "physicalWrites": len(robot.writes), "trace": trace}, "", "  ")
		if err = os.WriteFile(filepath.Join(directory, "host-three-stations.json"), append(data, '\n'), 0644); err != nil {
			t.Fatal(err)
		}
	}
	t.Logf("parent_events=%d child_events=%d commands=%d rpc=%d coverage=%s", trace.Source.EventCount, len(child.Task.Events), len(child.Steps), len(child.RPCCalls), child.Coverage)
}
