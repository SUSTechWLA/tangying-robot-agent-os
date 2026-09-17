package actionloop_test

import (
	"context"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

func singleToolRequest(tool actionloop.Tool, history ...actionloop.Round) actionloop.Request {
	return actionloop.Request{Goal: "看一眼", Tools: []actionloop.Tool{tool}, History: history}
}

// The first question is answered with the call.
func TestSingleToolDeciderCallsTheOnlyTool(t *testing.T) {
	decision, err := actionloop.SingleToolDecider{}.Decide(context.Background(),
		singleToolRequest(actionloop.Tool{Name: "telemetry.read", SafetyLevel: skills.SafetyReadOnly}))
	if err != nil {
		t.Fatalf("decide: %v", err)
	}
	if decision.Tool != "telemetry.read" {
		t.Fatalf("decision = %+v", decision)
	}
	if decision.Blocked != "" || decision.Done {
		t.Fatalf("decision = %+v", decision)
	}
}

// The second question is answered with "done". Answering it with a second call
// would make the loop call the same tool forever.
func TestSingleToolDeciderDoesNotCallTwice(t *testing.T) {
	decision, err := actionloop.SingleToolDecider{}.Decide(context.Background(), singleToolRequest(
		actionloop.Tool{Name: "telemetry.read", SafetyLevel: skills.SafetyReadOnly},
		actionloop.Round{Round: 1, Tool: "telemetry.read", Verdict: actionloop.VerdictSatisfied},
	))
	if err != nil {
		t.Fatalf("decide: %v", err)
	}
	if !decision.Done {
		t.Fatalf("decision = %+v: a satisfied call must end the loop, not repeat it", decision)
	}
}

// A call that did not achieve its effect is reported, not retried. This is the
// same prohibition the closed loop states for physical actions, applied to the
// only case where this decider could otherwise loop.
func TestSingleToolDeciderStopsAfterAnUnsatisfiedCall(t *testing.T) {
	decision, err := actionloop.SingleToolDecider{}.Decide(context.Background(), singleToolRequest(
		actionloop.Tool{Name: "telemetry.read", SafetyLevel: skills.SafetyReadOnly},
		actionloop.Round{
			Round: 1, Tool: "telemetry.read", Verdict: actionloop.VerdictUnsatisfied,
			Code: "RPC_UNAVAILABLE", Detail: "远端没有应答",
		},
	))
	if err != nil {
		t.Fatalf("decide: %v", err)
	}
	if decision.Tool != "" {
		t.Fatalf("decision = %+v: an unsatisfied call must not be repeated", decision)
	}
	if decision.Blocked == "" {
		t.Fatalf("decision = %+v: the failure must be stated", decision)
	}
	if !strings.Contains(decision.Blocked, "不会自动重试") {
		t.Fatalf("blocked = %q", decision.Blocked)
	}
}

// Inventing an argument value and sending it to a robot is worse than not calling.
func TestSingleToolDeciderRefusesAToolThatNeedsArguments(t *testing.T) {
	decision, err := actionloop.SingleToolDecider{}.Decide(context.Background(),
		singleToolRequest(actionloop.Tool{
			Name: "mapping.move", SafetyLevel: skills.SafetyReadOnly,
			Parameters: []string{"x", "y", "yaw"},
		}))
	if err != nil {
		t.Fatalf("decide: %v", err)
	}
	if decision.Tool != "" {
		t.Fatalf("decision = %+v: a call was made with no values for its arguments", decision)
	}
	if !strings.Contains(decision.Blocked, "x、y、yaw") {
		t.Fatalf("blocked = %q: the missing parameters must be named", decision.Blocked)
	}
}

// A multi-tool action pointed at this decider must fail closed rather than have
// the first tool picked for it.
func TestSingleToolDeciderRefusesToChooseAmongSeveral(t *testing.T) {
	decision, err := actionloop.SingleToolDecider{}.Decide(context.Background(), actionloop.Request{
		Goal: "重跑一遍测绘",
		Tools: []actionloop.Tool{
			{Name: "mapping.start"}, {Name: "mapping.move"}, {Name: "mapping.finish"},
		},
	})
	if err != nil {
		t.Fatalf("decide: %v", err)
	}
	if decision.Tool != "" {
		t.Fatalf("decision = %+v: one of three tools was picked arbitrarily", decision)
	}
	if !strings.Contains(decision.Blocked, "3 个工具") {
		t.Fatalf("blocked = %q", decision.Blocked)
	}
}

// The decider is only ever reached through the loop, so the property that matters
// is the one the loop enforces end to end: a single read-only tool is called
// exactly once and the run then completes.
func TestThroughTheLoopASingleReadToolIsCalledOnce(t *testing.T) {
	var calls int
	tool := actionloop.Tool{
		Name: "telemetry.read", SafetyLevel: skills.SafetyReadOnly,
		Call: func(context.Context, map[string]any) (actionloop.Result, error) {
			calls++
			return actionloop.Result{Success: true, Message: "已读取"}, nil
		},
	}
	outcome, err := actionloop.Loop{
		Tools: []actionloop.Tool{tool}, Decider: actionloop.SingleToolDecider{},
		Observe: func(context.Context) (actionloop.Observation, error) {
			return actionloop.Observation{Summary: "机器人状态未知"}, nil
		},
	}.Run(context.Background(), "先读一次遥测")
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	if calls != 1 {
		t.Fatalf("calls = %d, want exactly 1", calls)
	}
	if outcome.Calls != 1 {
		t.Fatalf("outcome.Calls = %d", outcome.Calls)
	}
	if !outcome.Completed {
		t.Fatalf("outcome = %+v", outcome)
	}
}
