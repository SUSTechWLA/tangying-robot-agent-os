package recoveryexec_test

import (
	"context"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/recoveryexec"
)

type contextReadVerificationDecider func(actionloop.Request) (actionloop.Decision, error)

func (f contextReadVerificationDecider) Decide(_ context.Context, request actionloop.Request) (actionloop.Decision, error) {
	return f(request)
}

func TestIndependentReadVerificationAfterManagedArchiveRetrieval(t *testing.T) {
	for _, secondReadFails := range []bool{false, true} {
		name := "fresh reread succeeds"
		if secondReadFails {
			name = "fresh reread fails"
		}
		t.Run(name, func(t *testing.T) {
			t.Setenv("TANGYING_CONTEXT_MAX_BYTES", "8192")
			t.Setenv("TANGYING_MODEL_REQUEST_MAX_BYTES", "16384")
			calls := 0
			registry := recoveryexec.MapRegistry{
				"execution.read-history": {Name: "execution.read-history", Parameters: []string{"selector"}, SafetyLevel: skills.SafetyReadOnly,
					Call: func(ctx context.Context, arguments map[string]any) (actionloop.Result, error) {
						calls++
						if recoveryexec.TaskIDFrom(ctx) != "task-owned" || arguments["selector"] != "all" {
							t.Fatal("independent reread lost original task or arguments")
						}
						return actionloop.Result{Success: !(secondReadFails && calls == 2), Detail: map[string]any{"ledger": strings.Repeat("durable-command-history;", 1000)}}, nil
					}},
			}
			decider := contextReadVerificationDecider(func(request actionloop.Request) (actionloop.Decision, error) {
				switch request.Round {
				case 1:
					return actionloop.Decision{Tool: "execution.read-history", Arguments: map[string]any{"selector": "all"}}, nil
				case 2:
					for _, artifact := range request.ContextSnapshot.Artifacts {
						if artifact.Kind == "attempts" {
							return actionloop.Decision{Tool: actionloop.ContextReadTool, Arguments: map[string]any{"sha256": artifact.SHA256, "item_id": "round:1", "limit": 512}}, nil
						}
					}
					t.Fatal("large read did not produce retrievable context archive")
				case 3:
					last := request.History[len(request.History)-1]
					if last.Tool != actionloop.ContextReadTool || last.Verdict != actionloop.VerdictSatisfied {
						t.Fatalf("archive retrieval failed: %+v", last)
					}
					return actionloop.Decision{Done: true}, nil
				}
				return actionloop.Decision{Blocked: "unexpected round"}, nil
			})
			executor := recoveryexec.Executor{Registry: registry, Decider: decider, Verify: recoveryexec.VerifyReadOnly(registry),
				Observer: observerFunc(func(context.Context, string) (actionloop.Observation, error) {
					d := agentcontext.Document{SchemaVersion: agentcontext.Version, Role: "recovery", Goal: "读取执行历史", Scope: agentcontext.Scope{TaskID: "task-owned", PlanRevision: 1}}
					return actionloop.Observation{Context: &d, Summary: "诊断读取"}, nil
				}),
			}
			result, err := executor.Execute(context.Background(), recoveryexec.Request{TaskID: "task-owned", PlanID: "plan", Action: action(t, "execution.read-history")})
			if err != nil || calls != 2 || !result.Executed || result.Verified == secondReadFails || len(result.Rounds) != 3 {
				t.Fatalf("read/archive/finish failed independent verification: calls=%d result=%+v err=%v", calls, result, err)
			}
		})
	}
}

func TestArchiveReadsAloneCannotCountAsIndependentVerification(t *testing.T) {
	calls := 0
	registry := recoveryexec.LocalTools{ReadHistory: func(context.Context, map[string]any) (actionloop.Result, error) {
		calls++
		return actionloop.Result{Success: true}, nil
	}}.Registry()
	verdict, err := recoveryexec.VerifyReadOnly(registry)(context.Background(), action(t, "execution.read-history"), actionloop.Outcome{
		Completed: true, Rounds: []actionloop.Round{{Tool: actionloop.ContextReadTool, Verdict: actionloop.VerdictSatisfied}},
	})
	if err != nil || verdict.Verified || calls != 0 {
		t.Fatalf("stored archive was mistaken for a fresh diagnostic read: %+v %v", verdict, err)
	}
}
