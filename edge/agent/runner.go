package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/compiler"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/guard"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/taskgraph"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

var (
	ErrApprovalRequired       = errors.New("operator approval required")
	ErrPhysicalOutcomeUnknown = errors.New("physical step outcome requires reconciliation")
	ErrVerificationFailed     = errors.New("post-action verification failed")
)

type Grounder interface {
	Ground(context.Context, manipulation.Intent) (manipulation.GroundedTask, error)
}

// SkillResult is an alias kept for compatibility with existing Agent code.
// The canonical type now lives in edge/runtime.
type SkillResult = runtime.SkillResult

type RunResult struct {
	TaskID         string
	CompletedSteps []string
}

type Runner struct {
	store    middleware.ExecutionStore
	grounder Grounder
	invoker  runtime.Invoker
	// Telemetry is an optional observer sink. Failures are deliberately
	// non-fatal: observability must never change task execution.
	Telemetry func(context.Context, telemetry.Snapshot) error
	// TaskEvents receives structured local tool activity. Local Brain wires it
	// to the same durable task event stream consumed by task.experience.v1.
	TaskEvents func(context.Context, string, tasks.TaskEvent) error
}

func NewRunner(store middleware.ExecutionStore, grounder Grounder, invoker runtime.Invoker) *Runner {
	return &Runner{store: store, grounder: grounder, invoker: invoker}
}

func (r *Runner) Run(ctx context.Context, task *tasks.Task) (RunResult, error) {
	intents := task.Intent.Tasks()
	result := RunResult{TaskID: task.ID}
	for index, intent := range intents {
		grounded := manipulation.GroundedTask{TaskID: task.ID, RobotID: intent.RobotID, Action: intent.Action}
		if intent.Action != manipulation.ActionPrepareSimulation {
			var err error
			grounded, err = r.grounder.Ground(ctx, intent)
			if err != nil {
				return result, fmt.Errorf("ground subtask %d: %w", index+1, err)
			}
			grounded.TaskID = task.ID
			grounded.RobotID = intent.RobotID
			grounded.Action = intent.Action
			grounded.KeepUpright = intent.Constraints.KeepUpright
			r.publishTelemetry(ctx, task.ID, "grounded")
			if len(intents) > 1 {
				grounded.StepIDPrefix = fmt.Sprintf("task%02d-", index+1)
			}
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
		if err := r.checkRuntimeCapabilities(ctx, plan, task.Adapter); err != nil {
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
	task *tasks.Task,
	graph compiler.ExecutionGraph,
	result *RunResult,
) error {
	for _, stepID := range graph.Order {
		step := graph.Nodes[stepID].Step
		executionStepID := revisionExecutionStepID(task, step.ID)
		status, err := r.store.StepStatus(ctx, task.ID, executionStepID)
		if err != nil {
			return err
		}
		if status == middleware.StepCompleted {
			result.CompletedSteps = append(result.CompletedSteps, step.ID)
			continue
		}
		physical := step.SafetyLevel == string(skills.SafetyPhysical)
		if status == middleware.StepStarted && physical {
			return fmt.Errorf("%w: %s", ErrPhysicalOutcomeUnknown, step.ID)
		}
		if physical && !task.Approved {
			return fmt.Errorf("%w: %s", ErrApprovalRequired, step.ID)
		}
		record := middleware.StepRecord{TaskID: task.ID, StepID: executionStepID, IdempotencyKey: CommandForTaskStep(task, step).IdempotencyKey}
		if err := r.store.MarkStepStarted(ctx, record); err != nil {
			return err
		}
		command := CommandForTaskStep(task, step)
		r.publishToolActivity(ctx, task, command, "SENDING", nil, "")
		r.publishToolActivity(ctx, task, command, "RUNNING", nil, "")
		skillResult, err := r.invoker.Invoke(ctx, command)
		if err != nil {
			r.publishToolActivity(ctx, task, command, "FAILED", nil, err.Error())
			return err
		}
		if !skillResult.Success {
			r.publishToolActivity(ctx, task, command, "FAILED", nil, skillResult.Code)
			return fmt.Errorf("skill %s failed: %s %s", step.Skill, skillResult.Code, skillResult.Message)
		}
		if (step.Skill == "verify_grasp" || step.Skill == "verify_placement") && skillResult.VerificationConfidence < 0.7 {
			return fmt.Errorf("%w: %s confidence %.2f", ErrVerificationFailed, step.ID, skillResult.VerificationConfidence)
		}
		if err := r.store.MarkStepCompleted(ctx, record); err != nil {
			return err
		}
		result.CompletedSteps = append(result.CompletedSteps, step.ID)
		evidence := []string(nil)
		if skillResult.ObservationID != "" {
			evidence = []string{skillResult.ObservationID}
		}
		r.publishToolActivity(ctx, task, command, "AWAITING_EVIDENCE", evidence, "")
		r.publishTelemetry(ctx, task.ID, step.ID)
	}
	return nil
}

func (r *Runner) publishToolActivity(
	ctx context.Context,
	task *tasks.Task,
	command runtime.Command,
	status string,
	evidenceIDs []string,
	errorText string,
) {
	if r.TaskEvents == nil {
		return
	}
	payload := map[string]any{
		"toolName": string(command.Capability), "activityStatus": status, "robotId": command.RobotID,
		"taskRevision": command.TaskRevision, "aggregateVersion": command.AggregateVersion,
		"stepId": command.StepID, "commandId": command.CommandID, "fencingToken": command.FencingToken,
		"arguments": command.Parameters,
	}
	if len(evidenceIDs) > 0 {
		payload["evidenceIds"] = append([]string(nil), evidenceIDs...)
	}
	if errorText != "" {
		payload["error"] = errorText
	}
	_ = r.TaskEvents(ctx, task.ID, tasks.TaskEvent{Type: "TOOL_ACTIVITY", StepID: command.StepID, Payload: payload})
}

func revisionExecutionStepID(task *tasks.Task, stepID string) string {
	if task.CurrentRevision <= 1 {
		return stepID
	}
	return fmt.Sprintf("revision-%d/%s", task.CurrentRevision, stepID)
}

type telemetryProvider interface {
	Telemetry(context.Context, string) (telemetry.Snapshot, error)
}

func (r *Runner) publishTelemetry(ctx context.Context, taskID, stepID string) {
	if r.Telemetry == nil {
		return
	}
	provider, ok := r.grounder.(telemetryProvider)
	if !ok {
		return
	}
	snapshot, err := provider.Telemetry(ctx, taskID)
	if err != nil {
		return
	}
	snapshot.StepID = stepID
	if stepID == "grounded" {
		snapshot.Activity = "OBSERVING"
	} else if stepID != "" {
		snapshot.Activity = "EXECUTING"
	}
	_ = r.Telemetry(ctx, snapshot)
}

// planForIntent uses the locally orchestrated plan when it exists; otherwise
// the deterministic domain plan remains the fallback. Physical safety
// controls are always re-created locally and never trusted from the LLM.
func (r *Runner) planForIntent(
	task *tasks.Task,
	index int,
	grounded manipulation.GroundedTask,
	intents []manipulation.Intent,
) (taskgraph.TaskPlan, error) {
	if index >= 0 && index < len(intents) && intents[index].Action == manipulation.ActionPrepareSimulation {
		return manipulation.PrepareSimulationPlan(task.ID, grounded.RobotID, time.Now().Add(time.Minute)), nil
	}
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
		if step.RobotID == "" {
			step.RobotID = grounded.RobotID
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
		if step.Skill == "plan_grasp" {
			if step.Arguments == nil {
				step.Arguments = map[string]any{}
			}
			step.Arguments["objectId"] = grounded.Object.ID
			step.Arguments["destinationId"] = grounded.Destination.ID
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
// snapshot before any step is executed. Runtime-aware clients fail closed when
// the robot is not ready or a planned skill is not currently available.
func (r *Runner) checkRuntimeCapabilities(ctx context.Context, plan taskgraph.TaskPlan, requestedAdapter string) error {
	provider, ok := r.invoker.(runtime.InfoProvider)
	if !ok {
		provider, ok = r.grounder.(runtime.InfoProvider)
	}
	if !ok {
		return nil
	}
	snapshot, err := provider.Info(ctx)
	if err != nil {
		return fmt.Errorf("fetch robot capabilities: %w", err)
	}
	expected := tasks.NormalizeAdapter(requestedAdapter)
	if expected != "" && expected != "auto" && snapshot.Adapter != "" && snapshot.Adapter != expected {
		return fmt.Errorf("%w: requested=%s connected=%s", runtime.ErrAdapterMismatch, expected, snapshot.Adapter)
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

// CommandForStep materializes the runtime command for one planned step:
// deadline, lease, idempotency key and approval are always re-created here
// and never trusted from any remote plan source.
func CommandForStep(taskID string, step taskgraph.SkillStep) runtime.Command {
	deadline := time.UnixMilli(step.DeadlineUnixMS)
	if step.DeadlineUnixMS == 0 {
		deadline = time.Now().Add(30 * time.Second)
	}
	lease := time.Duration(step.LeaseMS) * time.Millisecond
	if lease <= 0 {
		lease = 5 * time.Second
	}
	idempotencyKey := step.IdempotencyKey
	if idempotencyKey == "" {
		idempotencyKey = taskID + ":read:" + step.ID
	}
	return runtime.Command{
		SchemaVersion: "robot.v1", CommandID: taskID + ":" + step.ID,
		TaskID: taskID, RobotID: step.RobotID, Capability: runtime.CapabilityName(step.Skill), TargetRef: targetReference(step.Skill, step.Arguments),
		Parameters: step.Arguments, Deadline: deadline, Lease: lease, IdempotencyKey: idempotencyKey,
		ApprovalID: step.ApprovalID,
	}
}

// CommandForTaskStep binds a local execution command to the same immutable
// revision coordinates used by Fleet workers. Local Brain therefore produces
// identical audit identity even though it has no cloud coordinator.
func CommandForTaskStep(task *tasks.Task, step taskgraph.SkillStep) runtime.Command {
	command := CommandForStep(task.ID, step)
	revision := task.CurrentRevision
	if revision == 0 {
		revision = 1
	}
	aggregateVersion := task.AggregateVersion
	if aggregateVersion == 0 {
		aggregateVersion = 1
	}
	command.TaskRevision = revision
	command.AggregateVersion = aggregateVersion
	command.StepID = step.ID
	command.CommandID = fmt.Sprintf("%s/revision/%d/step/%s", task.ID, revision, step.ID)
	command.IdempotencyKey = command.CommandID
	return command
}

func targetReference(capability string, arguments map[string]any) string {
	var keys []string
	switch capability {
	case "plan_grasp", "manipulation.pick", "verify_grasp":
		keys = []string{"objectId", "targetRef", "destinationId"}
	case "manipulation.place", "verify_placement":
		keys = []string{"destinationId", "targetRef", "objectId"}
	default:
		keys = []string{"targetRef", "objectId", "destinationId"}
	}
	for _, key := range keys {
		if value, ok := arguments[key].(string); ok && value != "" {
			return value
		}
	}
	return ""
}

func joinBlockers(blockers []string) string {
	if len(blockers) == 0 {
		return "no blockers reported"
	}
	return strings.Join(blockers, ", ")
}
