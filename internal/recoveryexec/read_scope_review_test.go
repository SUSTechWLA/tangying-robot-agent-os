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

// Exercise the model path rather than the parameterless diagnostic fast path:
// a recovery action needing arguments still needs the configured decider, but
// the original long-running task is context rather than its completion target.
func TestArgumentBearingRecoveryScopesModelGoalWithoutMutatingTaskContext(t *testing.T) {
	for _, mode := range []string{"legacy", "json", "factorial"} {
		t.Run(mode, func(t *testing.T) {
			t.Setenv("TANGYING_AGENT_CONTEXT", mode)
			chosen := action(t, "map.read-status")
			originalGoal := "Complete the entire multi-room robot task"
			// Spare slice capacity detects an append that modifies the observer's
			// shared backing array even if its original slice length is unchanged.
			backing := []string{"keep original guard", "untouched sentinel", "spare"}
			document := agentcontext.Document{
				SchemaVersion: agentcontext.Version, Stage: "recovery", Role: "recovery",
				Goal: originalGoal, Scope: agentcontext.Scope{TaskID: "task-scoped"},
				Constraints: backing[:1],
			}
			native := agentcontext.DecisionFrom(document)
			document.DecisionMetadata = &native
			var calls []string
			registry := recoveryexec.MapRegistry{
				"mapping.status": serviceTool("mapping.status", skills.SafetyReadOnly, false, &calls),
			}
			model := &scripted{decisions: []actionloop.Decision{
				{Tool: "mapping.status", Arguments: map[string]any{"parameters": "current"}},
				{Done: true, Reason: "The diagnostic read completed"},
			}}
			providerCalls := 0
			executor := recoveryexec.Executor{
				Registry: registry,
				Observer: observerFunc(func(context.Context, string) (actionloop.Observation, error) {
					return actionloop.Observation{Summary: originalGoal, Context: &document}, nil
				}),
				DeciderProvider: func() actionloop.Decider {
					providerCalls++
					return model
				},
				Verify: recoveryexec.VerifyReadOnly(registry),
			}
			result, err := executor.Execute(context.Background(), recoveryexec.Request{
				TaskID: "task-scoped", PlanID: "plan-scoped", Action: chosen,
			})
			if err != nil || !result.Verified || len(calls) != 2 {
				t.Fatalf("result=%+v err=%v calls=%v", result, err, calls)
			}
			if providerCalls != 1 || model.calls != 2 {
				t.Fatalf("argument-bearing action bypassed model: provider=%d model=%d", providerCalls, model.calls)
			}
			for _, request := range model.requests {
				if request.Goal != chosen.Summary || request.Observation.Context.Goal != chosen.Summary {
					t.Fatalf("original-task goal leaked into diagnostic goal: %+v", request)
				}
				if request.Observation.Context == &document {
					t.Fatal("observer context was not cloned")
				}
				if !strings.Contains(request.Observation.Summary, originalGoal) {
					t.Fatal("original-task background was lost")
				}
				if mode != "legacy" && (request.ContextSnapshot == nil || !strings.Contains(request.ContextSnapshot.Text, chosen.Summary)) {
					t.Fatal("rendered model context lost the selected recovery goal")
				}
			}
			if document.Goal != originalGoal || len(document.Constraints) != 1 || backing[1] != "untouched sentinel" {
				t.Fatalf("recovery mutated shared task context: document=%+v backing=%v", document, backing)
			}
			if native.Goal["request"] != originalGoal {
				t.Fatal("recovery mutated the original native goal metadata")
			}
		})
	}
}

func TestParameterlessDiagnosticDoesNotConsultDynamicModelProvider(t *testing.T) {
	calls := 0
	registry := recoveryexec.LocalTools{
		ReadHistory: func(context.Context, map[string]any) (actionloop.Result, error) {
			calls++
			return actionloop.Result{Success: true}, nil
		},
	}.Registry()
	executor := recoveryexec.Executor{
		Registry: registry, Observer: observer(), Verify: recoveryexec.VerifyReadOnly(registry),
		DeciderProvider: func() actionloop.Decider {
			t.Fatal("one parameterless diagnostic should not depend on model availability")
			return nil
		},
	}
	result, err := executor.Execute(context.Background(), recoveryexec.Request{
		TaskID: "task-owned", PlanID: "plan-owned", Action: action(t, "execution.read-history"),
	})
	if err != nil || !result.Executed || !result.Verified || calls != 2 {
		t.Fatalf("result=%+v err=%v calls=%d", result, err, calls)
	}
}
