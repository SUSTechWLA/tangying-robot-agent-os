package tasks

import (
	"context"
	"fmt"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/capability"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/orchestration"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

// GoalPlanner extends task creation without owning a second queue or database.
type GoalPlanner interface {
	PlanGoal(context.Context, string) (orchestration.Bundle, bool, error)
}

func (s *Service) SetGoalPlanner(planner GoalPlanner) {
	s.plannerMu.Lock()
	defer s.plannerMu.Unlock()
	s.goalPlanner = planner
}
func (s *Service) createCapabilityTask(ctx context.Context, request, adapter string, bundle orchestration.Bundle) (*Task, error) {
	p := bundle.Capabilities
	if p == nil || p.RobotID == "" || len(p.Calls) == 0 {
		return nil, fmt.Errorf("invalid capability goal plan")
	}
	now := s.now().UTC()
	task := &Task{ID: newID("task"), Request: request, Adapter: NormalizeAdapter(adapter),
		Intent: manipulation.Intent{Action: "capability_goal", RobotID: p.RobotID}, Plan: &bundle,
		State: taskgraph.StateReady, CurrentRevision: 1, AggregateVersion: 1, RevisionState: RevisionActive, CreatedAt: now, UpdatedAt: now}
	task.Events = []TaskEvent{{Sequence: 1, Type: "TASK_CREATED", OccurredAt: now, Payload: map[string]any{"kind": "capability_goal", "catalogRevision": p.CatalogRevision}}}
	steps := capabilitySteps(p, 1)
	revision := &TaskRevision{TaskID: task.ID, Revision: 1, Request: request, Understanding: request, Intent: task.Intent, Plan: &bundle, Steps: steps, RiskClass: "capability", ApprovalRequired: true, Creator: "task/create", IdempotencyKey: task.ID + "/create", CreatedAt: now}
	if err := s.store.CreateWithRevision(ctx, task, revision); err != nil {
		return nil, err
	}
	return task, nil
}

func capabilitySteps(p *capability.Plan, revision uint64) []RevisionStep {
	steps := make([]RevisionStep, len(p.Calls))
	for i, call := range p.Calls {
		steps[i] = RevisionStep{StepID: fmt.Sprintf("rev-%d-cap-%02d", revision, i+1), IntroducedRevision: revision, IntentIndex: i, Action: call.Tool, RobotID: p.RobotID, ResourceID: p.RobotID, SemanticFingerprint: capability.Fingerprint(call), Status: StepPending, RequiredPostcondition: "provider completion contract"}
	}
	return steps
}
