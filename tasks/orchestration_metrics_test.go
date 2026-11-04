package tasks_test

import (
	"context"
	"errors"
	"testing"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agent/intent"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontext"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

type metricsGoal struct{ trace []capability.PlanningStep }

func (g metricsGoal) PlanGoal(context.Context, string) (orchestration.Bundle, bool, error) {
	return orchestration.Bundle{Source: orchestration.SourceLLM, Capabilities: &capability.Plan{RobotID: "robot-metrics", CatalogRevision: "catalog", Calls: []capability.Call{{Tool: "mapping.status"}, {Tool: "semantic.locations"}}, PlanningTrace: g.trace}}, true, nil
}
func TestCapabilityMetricsSeparateProposalsClientAttemptsAndLegacyCandidates(t *testing.T) {
	ctx := context.Background()
	s := tasks.NewService(tasks.NewMemoryStore(), intent.NewDeterministicParser())
	call := func(id string) *agentcontext.Projection {
		return &agentcontext.Projection{ModelCall: &agentcontext.ModelCall{CallID: id, UsageStatus: "missing"}}
	}
	s.SetGoalPlanner(metricsGoal{trace: []capability.PlanningStep{
		{Verdict: "MODEL_DECISION_REJECTED", Context: call("invalid")},
		{Tool: "mapping.status", Verdict: "READ_OK", Context: call("read")},
		{Tool: "propose_capability_plan", Verdict: "REJECTED", Context: call("rejected")},
		{Tool: "propose_capability_plan", Verdict: "FROZEN_FOR_APPROVAL", Context: call("accepted")},
	}})
	if _, err := s.Create(ctx, "检查地图和语义地点", "gazebo"); err != nil {
		t.Fatal(err)
	}
	s.SetGoalPlanner(metricsGoal{trace: []capability.PlanningStep{{Tool: "mapping.status", Verdict: "READ_OK"}, {Tool: "planning.recipe", Verdict: "RECIPE_HIT"}}})
	if _, err := s.Create(ctx, "检查地图和语义地点", "gazebo"); err != nil {
		t.Fatal(err)
	}
	m := s.OrchestrationMetrics(ctx)
	if !m.Available || m.Population != "persisted_tasks_only" || m.TotalTasks != 2 || m.SequenceTasks != 2 || m.GoalModelCallAttempts != 4 || m.GoalRecipeHits != 1 || m.GoalDecisionRejected != 1 {
		t.Fatalf("metrics=%+v", m)
	}
	if m.GoalProposalAttempts != 2 || m.GoalAcceptedProposals != 1 || m.GoalProposalAcceptanceRate == nil || *m.GoalProposalAcceptanceRate != .5 {
		t.Fatalf("proposal denominator=%+v", m)
	}
	if m.LegacyCandidateAttempts != 0 || m.LegacyAcceptedCandidates != 0 || m.LLMCandidateRate != 0 {
		t.Fatal("new trace changed legacy denominator")
	}
	empty := orchestration.CalculateMetrics(nil)
	if empty.GoalProposalAcceptanceRate != nil {
		t.Fatal("no proposals must be null, not 0%")
	}
}

type unreadableTaskStore struct{ tasks.Repository }

func (unreadableTaskStore) List(context.Context) ([]*tasks.Task, error) {
	return nil, errors.New("database unavailable")
}
func TestUnavailableTaskMetricsDoNotClaimAnEmptyPopulation(t *testing.T) {
	s := tasks.NewService(unreadableTaskStore{tasks.NewMemoryStore()}, intent.NewDeterministicParser())
	if s.OrchestrationMetrics(context.Background()).Available {
		t.Fatal("storage failure became zero tasks")
	}
}
