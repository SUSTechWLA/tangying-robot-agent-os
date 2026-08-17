package agent

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/compiler"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/guard"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/localstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
)

var (
	ErrApprovalRequired       = errors.New("operator approval required")
	ErrPhysicalOutcomeUnknown = errors.New("physical step outcome requires reconciliation")
	ErrVerificationFailed     = errors.New("post-action verification failed")
)

type Robot interface {
	Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error)
	Execute(context.Context, string, taskgraph.SkillStep) (SkillResult, error)
}

type SkillResult struct {
	Success                bool
	Code                   string
	Message                string
	ObservationID          string
	VerificationConfidence float64
}

type RunResult struct {
	TaskID         string
	CompletedSteps []string
}

type Runner struct {
	store *localstore.Store
	robot Robot
}

func NewRunner(store *localstore.Store, robot Robot) *Runner {
	return &Runner{store: store, robot: robot}
}

func (r *Runner) Run(ctx context.Context, task *orchestrator.Task) (RunResult, error) {
	grounded, err := r.robot.Ground(ctx, task.Intent)
	if err != nil {
		return RunResult{}, err
	}
	grounded.TaskID = task.ID
	grounded.KeepUpright = task.Intent.Constraints.KeepUpright
	plan := manipulation.Plan(grounded, time.Now().Add(time.Minute))
	if err := guard.New(manipulation.Catalog()).Validate(plan); err != nil {
		return RunResult{}, err
	}
	graph, err := compiler.New().Compile(plan)
	if err != nil {
		return RunResult{}, err
	}
	result := RunResult{TaskID: task.ID}
	for _, stepID := range graph.Order {
		step := graph.Nodes[stepID].Step
		status, err := r.store.Status(ctx, task.ID, step.ID)
		if err != nil {
			return result, err
		}
		if status == localstore.StatusCompleted {
			result.CompletedSteps = append(result.CompletedSteps, step.ID)
			continue
		}
		physical := step.SafetyLevel == string(skills.SafetyPhysical)
		if status == localstore.StatusStarted && physical {
			return result, fmt.Errorf("%w: %s", ErrPhysicalOutcomeUnknown, step.ID)
		}
		if physical && !task.Approved {
			return result, fmt.Errorf("%w: %s", ErrApprovalRequired, step.ID)
		}
		if err := r.store.MarkStarted(ctx, task.ID, step.ID, step.IdempotencyKey); err != nil {
			return result, err
		}
		skillResult, err := r.robot.Execute(ctx, task.ID, step)
		if err != nil {
			return result, err
		}
		if !skillResult.Success {
			return result, fmt.Errorf("skill %s failed: %s %s", step.Skill, skillResult.Code, skillResult.Message)
		}
		if (step.Skill == "verify_grasp" || step.Skill == "verify_placement") && skillResult.VerificationConfidence < 0.7 {
			return result, fmt.Errorf("%w: %s confidence %.2f", ErrVerificationFailed, step.ID, skillResult.VerificationConfidence)
		}
		if err := r.store.MarkCompleted(ctx, task.ID, step.ID, step.IdempotencyKey); err != nil {
			return result, err
		}
		result.CompletedSteps = append(result.CompletedSteps, step.ID)
	}
	return result, nil
}
