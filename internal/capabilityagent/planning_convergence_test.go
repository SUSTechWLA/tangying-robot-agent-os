package capabilityagent

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/internal/actionloop"
)

func planningBudgetGuard(t *testing.T, request actionloop.Request) map[string]any {
	t.Helper()
	if request.ContextSnapshot == nil || request.ContextSnapshot.Scope != (agentcontext.Scope{RobotID: "planning-robot"}) {
		t.Fatal("planning budget lost or invented its scope")
	}
	var budget map[string]any
	for _, record := range request.Observation.Context.Records {
		if record.ID == "planning:budget" {
			if record.Kind != "guard" || record.Scope != request.ContextSnapshot.Scope || json.Unmarshal([]byte(record.Statement), &budget) != nil {
				t.Fatal("planning budget is not an exact scoped guard")
			}
		}
	}
	if budget == nil || budget["remaining_decisions"] != float64(11-request.Round) ||
		budget["proposal_only"] != (request.Round >= 9) || !strings.Contains(request.ContextSnapshot.Text, "goal-planning-budget.v1") {
		t.Fatalf("missing current budget: %#v", budget)
	}
	return budget
}

func planningToolOffered(request actionloop.Request, name string) bool {
	for _, tool := range request.Tools {
		if tool.Name == name {
			return true
		}
	}
	return false
}

func TestPlanningReadBudgetLeavesArchiveThenTwoProposalDecisions(t *testing.T) {
	provider := &largePlanningProvider{fakeProvider: &fakeProvider{}}
	planner := &Planner{Provider: provider}
	var archiveSHA string
	planner.Decider = contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
		budget := planningBudgetGuard(t, request)
		if budget["remaining_provider_reads"] != float64(6-provider.reads) ||
			planningToolOffered(request, "semantic.read") != (request.Round <= 6) {
			t.Fatalf("provider budget/tool mismatch in round %d: %#v", request.Round, budget)
		}
		switch {
		case request.Round <= 6:
			return actionloop.Decision{Tool: "semantic.read", Arguments: map[string]any{}}, nil
		case request.Round <= 8:
			if !planningToolOffered(request, actionloop.ContextReadTool) || budget["provider_reads_available"] != false ||
				!strings.Contains(request.Observation.Summary, "Provider查询预算已耗尽") {
				t.Fatal("six reads must leave only proposal and existing archive access before round nine")
			}
			if archiveSHA == "" {
				for _, artifact := range request.ContextSnapshot.Artifacts {
					if artifact.Kind == "attempts" {
						archiveSHA = artifact.SHA256
					}
				}
			}
			offset := 0.
			if request.Round == 8 {
				offset, _ = request.History[6].ResultDetail["next_offset"].(float64)
				if offset <= 0 {
					t.Fatal("first archive page omitted its continuation offset")
				}
			}
			return actionloop.Decision{Tool: actionloop.ContextReadTool, Arguments: map[string]any{
				"sha256": archiveSHA, "item_id": "round:1", "offset": offset, "limit": 128,
			}}, nil
		default:
			if len(request.Tools) != 1 || request.Tools[0].Name != "propose_capability_plan" || !strings.Contains(request.Observation.Summary, "不能继续查询或历史回查") {
				t.Fatalf("late round still offers reads: %+v", request.Tools)
			}
			if request.Round == 9 {
				return planningProposal("unregistered.service"), nil
			}
			if budget["remaining_proposals"] != float64(2) {
				t.Fatalf("rejected proposal did not consume its budget: %#v", budget)
			}
			return planningProposal("semantic.read"), nil
		}
	})
	calls, trace, err := planner.modelPlan(context.Background(), "先读语义证据，再提交完整计划", "planning-robot", semanticPlanningEntries())
	if err != nil || len(calls) != 1 || len(trace) != 10 || provider.reads != 6 {
		t.Fatalf("calls=%d rounds=%d reads=%d err=%v", len(calls), len(trace), provider.reads, err)
	}
	if trace[6].Verdict != "CONTEXT_READ_OK" || trace[7].Verdict != "CONTEXT_READ_OK" || trace[8].Verdict != "REJECTED" || trace[9].Verdict != "FROZEN_FOR_APPROVAL" {
		t.Fatalf("unexpected late verdicts: %s %s (%s) %s %s", trace[6].Verdict, trace[7].Verdict, trace[7].Detail, trace[8].Verdict, trace[9].Verdict)
	}
}

func TestPlanningLateUnOfferedReadCannotDispatchEvenWithReadBudget(t *testing.T) {
	for _, forbidden := range []string{"semantic.read", actionloop.ContextReadTool, "robot.task"} {
		t.Run(forbidden, func(t *testing.T) {
			provider := &largePlanningProvider{fakeProvider: &fakeProvider{}}
			planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
				budget := planningBudgetGuard(t, request)
				if budget["remaining_provider_reads"] != float64(6) {
					t.Fatal("a rejected selection consumed a provider dispatch")
				}
				if request.Round < 9 {
					return actionloop.Decision{Tool: "not.offered"}, nil
				}
				if len(request.Tools) != 1 || planningToolOffered(request, forbidden) {
					t.Fatal("late read was offered")
				}
				if request.Round == 9 {
					return actionloop.Decision{Tool: forbidden, Arguments: map[string]any{}}, nil
				}
				return planningProposal("semantic.read"), nil
			})}
			calls, trace, err := planner.modelPlan(context.Background(), "只读计划", "planning-robot", semanticPlanningEntries())
			if err != nil || len(calls) != 1 || len(trace) != 10 || provider.reads != 0 || trace[8].Verdict != "REJECTED" {
				t.Fatalf("late choice bypassed allowlist: calls=%d rounds=%d reads=%d err=%v", len(calls), len(trace), provider.reads, err)
			}
		})
	}
}

func TestPlanningLateBlockedAndCustomDoneRemainNonExecutable(t *testing.T) {
	for _, done := range []bool{false, true} {
		provider := &largePlanningProvider{fakeProvider: &fakeProvider{}}
		planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
			if request.Round < 9 {
				return actionloop.Decision{Tool: "not.offered"}, nil
			}
			if len(request.Tools) != 1 {
				t.Fatal("late round is not limited to a proposal")
			}
			if done {
				return actionloop.Decision{Done: true}, nil
			}
			return actionloop.Decision{Blocked: "现有证据不足，不能补造现场状态"}, nil
		})}
		calls, trace, err := planner.modelPlan(context.Background(), "只读计划", "planning-robot", semanticPlanningEntries())
		if !errors.Is(err, intent.ErrClarificationRequired) || len(calls) != 0 || len(trace) != 9 || provider.reads != 0 {
			t.Fatalf("invalid completion: done=%v calls=%d rounds=%d reads=%d err=%v", done, len(calls), len(trace), provider.reads, err)
		}
	}
}

func TestPlanningOnlyActualDispatchConsumesReadBudget(t *testing.T) {
	provider := &largePlanningProvider{fakeProvider: &fakeProvider{}}
	planner := &Planner{Provider: provider, Decider: contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
		budget := planningBudgetGuard(t, request)
		want := 6
		if request.Round > 2 {
			want = 5
		}
		if budget["remaining_provider_reads"] != float64(want) {
			t.Fatalf("argument validation consumed dispatch budget: %#v", budget)
		}
		switch request.Round {
		case 1:
			return actionloop.Decision{Tool: "semantic.read", Arguments: map[string]any{"undeclared": true}}, nil
		case 2:
			return actionloop.Decision{Tool: "semantic.read", Arguments: map[string]any{}}, nil
		default:
			return planningProposal("semantic.read"), nil
		}
	})}
	calls, trace, err := planner.modelPlan(context.Background(), "只读计划", "planning-robot", semanticPlanningEntries())
	if err != nil || len(calls) != 1 || len(trace) != 3 || provider.reads != 1 || trace[0].Verdict != "REJECTED" {
		t.Fatalf("calls=%d rounds=%d reads=%d err=%v", len(calls), len(trace), provider.reads, err)
	}
}

func TestPlanningThreeProposalAttemptsRemainHardBound(t *testing.T) {
	planner := &Planner{Provider: &fakeProvider{}, Decider: contextPlanningDecider(func(request actionloop.Request) (actionloop.Decision, error) {
		budget := planningBudgetGuard(t, request)
		if budget["remaining_proposals"] != float64(4-request.Round) {
			t.Fatalf("proposal budget not updated: %#v", budget)
		}
		return planningProposal("not.registered"), nil
	})}
	calls, trace, err := planner.modelPlan(context.Background(), "只读计划", "planning-robot", semanticPlanningEntries())
	if !errors.Is(err, intent.ErrClarificationRequired) || len(calls) != 0 || len(trace) != 3 {
		t.Fatalf("proposal budget changed: calls=%d rounds=%d err=%v", len(calls), len(trace), err)
	}
}
