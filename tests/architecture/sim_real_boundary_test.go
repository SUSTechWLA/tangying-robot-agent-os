package architecture_test

import (
	"context"
	"testing"

	agentruntime "github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
)

type backendNeutralInvoker struct{}

func (backendNeutralInvoker) Invoke(context.Context, agentruntime.Command) (agentruntime.Result, error) {
	return agentruntime.Result{Success: true, Code: "OK"}, nil
}

func TestSimulationAndPhysicalAdaptersShareTheAgentRuntimeBoundary(t *testing.T) {
	var invoker agentruntime.Invoker = backendNeutralInvoker{}
	for _, adapter := range []string{"mujoco", "xlerobot_direct"} {
		result, err := invoker.Invoke(context.Background(), agentruntime.Command{
			SchemaVersion: "robot.v1",
			Capability:    agentruntime.CapabilityPick,
			TargetRef:     "red-cup",
			Parameters:    map[string]any{"configuredAdapter": adapter},
		})
		if err != nil || !result.Success {
			t.Fatalf("adapter %s did not satisfy shared runtime contract: result=%#v err=%v", adapter, result, err)
		}
	}
}
