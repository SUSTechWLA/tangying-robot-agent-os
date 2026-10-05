package recoveryexec_test

import (
	"context"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
)

func TestIndependentReadVerificationRetainsTaskAndRejectsFailedReread(t *testing.T) {
	for _, failSecond := range []bool{false, true} {
		calls := 0
		registry := recoveryexec.LocalTools{ReadHistory: func(ctx context.Context, _ map[string]any) (actionloop.Result, error) {
			calls++
			if recoveryexec.TaskIDFrom(ctx) != "task-owned" {
				t.Fatal("verification lost task identity")
			}
			return actionloop.Result{Success: !failSecond || calls < 2}, nil
		}}.Registry()
		// A configured model that confuses original-task and diagnostic goals
		// must not veto the one parameterless diagnostic read.
		model := &scripted{decisions: []actionloop.Decision{{Blocked: "original robot task is not complete"}}}
		executor := recoveryexec.Executor{Registry: registry, Observer: observer(), Verify: recoveryexec.VerifyReadOnly(registry), Decider: model}
		result, err := executor.Execute(context.Background(), recoveryexec.Request{TaskID: "task-owned", PlanID: "p1", Action: action(t, "execution.read-history")})
		if err != nil || calls != 2 || result.Verified == failSecond {
			t.Fatalf("calls=%d result=%+v err=%v", calls, result, err)
		}
		if model.calls != 0 {
			t.Fatal("parameterless diagnostic consulted model")
		}
	}
}
