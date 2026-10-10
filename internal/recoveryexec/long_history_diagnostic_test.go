package recoveryexec_test

import (
	"context"
	"slices"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
)

// run-001 (2026-10-10), seq 35/37/39, offered the diagnostic plus context_read
// before the first round. The deterministic decider refused both, even though
// the catalog declared exactly one parameterless business read.
func TestLongHistoryDiagnosticReadsOnceThenIndependentlyVerifies(t *testing.T) {
	for _, tc := range []struct {
		name                  string
		failedRead, wantCalls int
		verified              bool
	}{
		{"fresh reread succeeds", 0, 2, true},
		{"fresh reread fails", 2, 2, false},
		{"first read fails without automatic retry", 1, 1, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("TANGYING_CONTEXT_MAX_BYTES", "8192")
			t.Setenv("TANGYING_MODEL_REQUEST_MAX_BYTES", "16384")
			calls := 0
			registry := recoveryexec.LocalTools{ReadHistory: func(ctx context.Context, arguments map[string]any) (actionloop.Result, error) {
				calls++
				if recoveryexec.TaskIDFrom(ctx) != "long-task" || len(arguments) != 0 {
					t.Fatal("diagnostic lost task identity or invented arguments")
				}
				return actionloop.Result{Success: calls != tc.failedRead, Code: "RPC_UNAVAILABLE",
					Detail: map[string]any{"ledger": strings.Repeat("完整持久命令历史；", 4000)}}, nil
			}}.Registry()
			executor := recoveryexec.Executor{
				Registry: registry, Verify: recoveryexec.VerifyReadOnly(registry),
				DeciderProvider: func() actionloop.Decider { t.Fatal("parameterless diagnostic consulted a model"); return nil },
				Observer: observerFunc(func(context.Context, string) (actionloop.Observation, error) {
					d := agentcontext.Document{SchemaVersion: agentcontext.Version, Role: "recovery", Goal: "整个家庭任务尚未完成",
						Scope:       agentcontext.Scope{TaskID: "long-task", RobotID: "robot", PlanRevision: 1},
						Constraints: []string{"未知物理结果不得重试"},
						Records:     []agentcontext.Record{{ID: "long-ledger", Kind: "system", Statement: strings.Repeat("原始任务历史与约束；", 4000)}},
					}
					return actionloop.Observation{Context: &d, Summary: "读取已有执行记录"}, nil
				}),
			}
			result, err := executor.Execute(context.Background(), recoveryexec.Request{
				TaskID: "long-task", PlanID: "bound-plan", Action: action(t, "execution.read-history"),
			})
			if err != nil || calls != tc.wantCalls || !result.Executed || result.Verified != tc.verified || len(result.Rounds) != 2 {
				t.Fatalf("long context blocked read/finish/independent verification: calls=%d executed=%t verified=%t rounds=%d reason=%s err=%v", calls, result.Executed, result.Verified, len(result.Rounds), result.Reason, err)
			}
			for _, round := range result.Rounds {
				if round.Context == nil || len(round.Context.Artifacts) == 0 {
					t.Fatal("fixture did not exercise an initially archived context and injected read tool")
				}
			}
			if !slices.Contains(result.Rounds[0].Candidates, actionloop.ContextReadTool) {
				t.Fatal("initial context did not offer the reserved archive reader")
			}
			if !slices.ContainsFunc(result.Rounds[1].Context.Artifacts, func(a agentcontext.Artifact) bool { return a.Kind == "attempts" }) {
				t.Fatal("large diagnostic result did not produce the second-round archive")
			}
			if result.Rounds[0].Tool != "execution.read-history" || result.Rounds[1].Tool != "" {
				t.Fatalf("unexpected business dispatch: %q -> %q", result.Rounds[0].Tool, result.Rounds[1].Tool)
			}
		})
	}
}
