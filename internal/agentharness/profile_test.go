package agentharness

import (
	"context"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/modelroute"
)

type finishDecider struct{}

func (finishDecider) Decide(context.Context, actionloop.Request) (actionloop.Decision, error) {
	return actionloop.Decision{Done: true, Reason: "inspection complete"}, nil
}

func profile(t *testing.T, role Role) Profile {
	t.Helper()
	models := map[string]modelroute.Endpoint{}
	for _, stage := range []string{modelroute.Intent, modelroute.Planning, modelroute.Recovery, modelroute.System} {
		models[stage] = modelroute.Endpoint{Provider: "deterministic"}
	}
	result, err := New(role, models)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestRoleModelRoutesAreExplicit(t *testing.T) {
	server := profile(t, Server)
	if _, ok := server.Model(modelroute.System); !ok {
		t.Fatal("server has no system model route")
	}
	if _, ok := server.Model(modelroute.Recovery); ok {
		t.Fatal("server inherited single-robot recovery model")
	}
	if _, err := New(Server, map[string]modelroute.Endpoint{modelroute.Intent: {Provider: "deterministic"}}); err == nil {
		t.Fatal("missing server model stage accepted")
	}
	if _, err := New("unknown", nil); err == nil {
		t.Fatal("unknown deployment role accepted")
	}
}

func TestServerCannotSeeRobotToolsAndEdgeCannotSeeFleetTools(t *testing.T) {
	for _, tc := range []struct {
		role  Role
		class ToolClass
	}{
		{Server, RobotWrite}, {Edge, FleetDraft}, {Server, RobotRead}, {Edge, FleetRead},
	} {
		called := false
		capability := Capability{Class: tc.class, Tool: actionloop.Tool{Name: "forbidden", SafetyLevel: skills.SafetyPhysical,
			MutatesWorld: true, Call: func(context.Context, map[string]any) (actionloop.Result, error) {
				called = true
				return actionloop.Result{Success: true}, nil
			}}}
		_, err := profile(t, tc.role).Run(context.Background(), "goal", RunConfig{
			Decider: finishDecider{}, Observe: func(context.Context) (actionloop.Observation, error) { return actionloop.Observation{}, nil },
			Capabilities: []Capability{capability},
		})
		if err == nil || called {
			t.Fatalf("%s accepted %s tool", tc.role, tc.class)
		}
	}
}

func TestServerReadHarnessRunsAndRejectsMislabeledWrite(t *testing.T) {
	server := profile(t, Server)
	tool := actionloop.Tool{Name: "fleet.devices.read", SafetyLevel: skills.SafetyReadOnly,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			return actionloop.Result{Success: true}, nil
		}}
	config := RunConfig{Decider: finishDecider{}, Observe: func(context.Context) (actionloop.Observation, error) {
		return actionloop.Observation{Summary: "fleet"}, nil
	}, Capabilities: []Capability{{Class: FleetRead, Tool: tool}}}
	result, err := server.Run(context.Background(), "inspect", config)
	if err != nil || !result.Completed {
		t.Fatalf("server harness: outcome=%+v err=%v", result, err)
	}
	tool.MutatesWorld = true
	config.Capabilities[0].Tool = tool
	if _, err := server.Run(context.Background(), "inspect", config); err == nil {
		t.Fatal("world mutation mislabeled as fleet read was accepted")
	}
}
