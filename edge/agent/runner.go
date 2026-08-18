package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/cloud/orchestrator"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/compiler"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/guard"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/localstore"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
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

// SkillResult is an alias kept for compatibility with existing Agent code.
// The canonical type now lives in edge/runtime.
type SkillResult = runtime.SkillResult

type RunResult struct {
	TaskID         string
	CompletedSteps []string
}

type Runner struct {
	store *localstore.Store
	robot Robot
	// Telemetry is an optional observer sink. Failures are deliberately
	// non-fatal: observability must never change task execution.
	Telemetry func(context.Context, telemetry.Snapshot) error
}

func NewRunner(store *localstore.Store, robot Robot) *Runner {
	return &Runner{store: store, robot: robot}
}

func (r *Runner) Run(ctx context.Context, task *orchestrator.Task) (RunResult, error) {
	intents := task.Intent.Tasks()
	result := RunResult{TaskID: task.ID}
	for index, intent := range intents {
		grounded, err := r.robot.Ground(ctx, intent)
		if err != nil {
			return result, fmt.Errorf("ground subtask %d: %w", index+1, err)
		}
		grounded.TaskID = task.ID
		grounded.Action = intent.Action
		grounded.KeepUpright = intent.Constraints.KeepUpright
		r.publishTelemetry(ctx, task.ID, "grounded")
		if len(intents) > 1 {
			grounded.StepIDPrefix = fmt.Sprintf("task%02d-", index+1)
		}
		plan, err := r.planForIntent(task, index, grounded, intents)
		if err != nil {
			return result, fmt.Errorf("plan subtask %d: %w", index+1, err)
		}
		if err := guard.New(manipulation.Catalog()).Validate(plan); err != nil {
			return result, fmt.Errorf("validate subtask %d: %w", index+1, err)
		}
		graph, err := compiler.New().Compile(plan)
		if err != nil {
			return result, fmt.Errorf("compile subtask %d: %w", index+1, err)
		}
		if err := r.checkRuntimeCapabilities(ctx, plan); err != nil {
			return result, fmt.Errorf("subtask %d: %w", index+1, err)
		}
		if err := r.executePlan(ctx, task, graph, &result); err != nil {
			return result, fmt.Errorf("subtask %d: %w", index+1, err)
		}
	}
	return result, nil
}

func (r *Runner) executePlan(
	ctx context.Context,
	task *orchestrator.Task,
	graph compiler.ExecutionGraph,
	result *RunResult,
) error {
	for _, stepID := range graph.Order {
		step := graph.Nodes[stepID].Step
		status, err := r.store.Status(ctx, task.ID, step.ID)
		if err != nil {
			return err
		}
		if status == localstore.StatusCompleted {
			result.CompletedSteps = append(result.CompletedSteps, step.ID)
			continue
		}
		physical := step.SafetyLevel == string(skills.SafetyPhysical)
		if status == localstore.StatusStarted && physical {
			return fmt.Errorf("%w: %s", ErrPhysicalOutcomeUnknown, step.ID)
		}
		if physical && !task.Approved {
			return fmt.Errorf("%w: %s", ErrApprovalRequired, step.ID)
		}
		if err := r.store.MarkStarted(ctx, task.ID, step.ID, step.IdempotencyKey); err != nil {
			return err
		}
		skillResult, err := r.robot.Execute(ctx, task.ID, step)
		if err != nil {
			return err
		}
		if !skillResult.Success {
			return fmt.Errorf("skill %s failed: %s %s", step.Skill, skillResult.Code, skillResult.Message)
		}
		if (step.Skill == "verify_grasp" || step.Skill == "verify_placement") && skillResult.VerificationConfidence < 0.7 {
			return fmt.Errorf("%w: %s confidence %.2f", ErrVerificationFailed, step.ID, skillResult.VerificationConfidence)
		}
		if err := r.store.MarkCompleted(ctx, task.ID, step.ID, step.IdempotencyKey); err != nil {
			return err
		}
		result.CompletedSteps = append(result.CompletedSteps, step.ID)
		r.publishTelemetry(ctx, task.ID, step.ID)
	}
	return nil
}

type telemetryProvider interface {
	Telemetry(context.Context, string) (telemetry.Snapshot, error)
}

func (r *Runner) publishTelemetry(ctx context.Context, taskID, stepID string) {
	if r.Telemetry == nil {
		return
	}
	provider, ok := r.robot.(telemetryProvider)
	if !ok {
		return
	}
	snapshot, err := provider.Telemetry(ctx, taskID)
	if err != nil {
		return
	}
	snapshot.StepID = stepID
	_ = r.Telemetry(ctx, snapshot)
}

// planForIntent uses the cloud-orchestrated plan when it exists; otherwise the
// Local Agent deterministic domain plan remains the fallback. Physical safety
// controls are always re-created locally and never trusted from the LLM.
func (r *Runner) planForIntent(
	task *orchestrator.Task,
	index int,
	grounded manipulation.GroundedTask,
	intents []manipulation.Intent,
) (taskgraph.TaskPlan, error) {
	if task.Plan != nil && task.Plan.LLMGenerated() && len(task.Plan.Plans) == len(intents) {
		template := prefixPlanTemplate(task.Plan.Plans[index], grounded.StepIDPrefix)
		if plan, err := materializePlanTemplate(template, task.ID, grounded, time.Now().Add(time.Minute)); err == nil {
			return plan, nil
		}
	}
	return manipulation.Plan(grounded, time.Now().Add(time.Minute)), nil
}

func prefixPlanTemplate(plan taskgraph.TaskPlan, prefix string) taskgraph.TaskPlan {
	plan.Steps = append([]taskgraph.SkillStep(nil), plan.Steps...)
	for index := range plan.Steps {
		plan.Steps[index].ID = prefix + plan.Steps[index].ID
		plan.Steps[index].DependsOn = append([]string(nil), plan.Steps[index].DependsOn...)
		for dependencyIndex, dependency := range plan.Steps[index].DependsOn {
			plan.Steps[index].DependsOn[dependencyIndex] = prefix + dependency
		}
	}
	return plan
}

func materializePlanTemplate(
	template taskgraph.TaskPlan,
	taskID string,
	grounded manipulation.GroundedTask,
	deadline time.Time,
) (taskgraph.TaskPlan, error) {
	plan := template
	plan.ID = taskID
	plan.Domain = "manipulation"
	plan.StopPolicy.StopOnSafety = true
	catalog := make(map[string]skills.SkillManifest)
	for _, manifest := range manipulation.Catalog() {
		catalog[manifest.Name] = manifest
	}
	for index := range plan.Steps {
		step := &plan.Steps[index]
		manifest, ok := catalog[step.Skill]
		if !ok {
			return taskgraph.TaskPlan{}, fmt.Errorf("unknown skill %s", step.Skill)
		}
		step.Arguments = resolvePlanArguments(step.Arguments, grounded)
		if step.Skill == "resolve_targets" {
			if step.Arguments == nil {
				step.Arguments = map[string]any{}
			}
			step.Arguments["objectId"] = grounded.Object.ID
			step.Arguments["objectConfidence"] = grounded.Object.Confidence
			step.Arguments["destinationId"] = grounded.Destination.ID
			step.Arguments["destinationConfidence"] = grounded.Destination.Confidence
		}
		if manifest.SafetyLevel == skills.SafetyPhysical {
			step.SafetyLevel = string(manifest.SafetyLevel)
			step.ApprovalID = "approval:" + taskID + ":physical"
			step.DeadlineUnixMS = deadline.UnixMilli()
			step.LeaseMS = manifest.DefaultLeaseMS
			step.IdempotencyKey = fmt.Sprintf("%s-%s-1", taskID, step.ID)
		} else {
			step.SafetyLevel = string(manifest.SafetyLevel)
			step.ApprovalID = ""
			step.DeadlineUnixMS = 0
			step.LeaseMS = 0
			step.IdempotencyKey = ""
		}
	}
	return plan, nil
}

func resolvePlanArguments(arguments map[string]any, grounded manipulation.GroundedTask) map[string]any {
	if arguments == nil {
		return nil
	}
	resolved := make(map[string]any, len(arguments))
	for key, value := range arguments {
		switch typed := value.(type) {
		case string:
			switch typed {
			case "@object":
				resolved[key] = grounded.Object.ID
			case "@destination":
				resolved[key] = grounded.Destination.ID
			default:
				resolved[key] = typed
			}
		case map[string]any:
			resolved[key] = resolvePlanArguments(typed, grounded)
		default:
			resolved[key] = typed
		}
	}
	return resolved
}

// checkRuntimeCapabilities asks a Robot Runtime for its current capability
// snapshot before any step is executed. Robots that only implement the legacy
// Robot interface keep working; runtime-aware clients fail closed when the
// robot is not ready or a planned skill is not currently available.
func (r *Runner) checkRuntimeCapabilities(ctx context.Context, plan taskgraph.TaskPlan) error {
	provider, ok := r.robot.(runtime.CapabilityProvider)
	if !ok {
		return nil
	}
	snapshot, err := provider.Snapshot(ctx)
	if err != nil {
		return fmt.Errorf("fetch robot capabilities: %w", err)
	}
	hasPhysical := false
	for _, step := range plan.Steps {
		if step.SafetyLevel == string(skills.SafetyPhysical) {
			hasPhysical = true
		}
		if err := snapshot.CanExecute(step.Skill); err != nil {
			return err
		}
	}
	if hasPhysical && !snapshot.PhysicalReady() {
		return fmt.Errorf("%w: %s (%s)", runtime.ErrRobotNotReady, snapshot.RobotID, joinBlockers(snapshot.Blockers))
	}
	return nil
}

func joinBlockers(blockers []string) string {
	if len(blockers) == 0 {
		return "no blockers reported"
	}
	return strings.Join(blockers, ", ")
}
