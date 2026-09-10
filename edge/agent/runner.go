package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
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
	ErrPauseRequested         = errors.New("pause requested at a completed tool boundary")
	ErrRecoveryUnavailable    = errors.New("durable execution history unavailable")
	ErrResumeBindingChanged   = errors.New("resume target differs from durable physical execution")
	// ErrUnverifiedWorldMutation means a tool changed, or may have changed, the
	// physical world and no fresh post-command observation confirms the result.
	// The step is deliberately left STARTED so recovery reconciles the world
	// instead of repeating a physical action whose outcome is unknown.
	ErrUnverifiedWorldMutation = errors.New("world mutation lacks fresh post-condition evidence")
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
	return r.RunControlled(ctx, task, RunControl{})
}

type RunControl struct {
	BeforeStep func(context.Context) error
	// A new read identity prevents runtime idempotency caches from returning
	// pre-interruption verification evidence during an explicit resume.
	ObservationAttempt string
}

func (r *Runner) ExecutionHistory(ctx context.Context, taskID string) ([]middleware.StepRun, error) {
	reader, ok := r.store.(middleware.ExecutionReader)
	if !ok {
		return nil, ErrRecoveryUnavailable
	}
	return reader.ListStepRuns(ctx, taskID)
}

func (r *Runner) CheckRecovery(ctx context.Context, taskID string) error {
	runs, err := r.ExecutionHistory(ctx, taskID)
	if err != nil {
		return err
	}
	for _, run := range runs {
		if run.Status == middleware.StepStarted && !IsReadOnlyCapability(run.Capability) {
			return fmt.Errorf("%w: %s", ErrPhysicalOutcomeUnknown, run.StepID)
		}
	}
	return nil
}

// IsReadOnlyCapability consults the trusted local tool catalog. Persisted
// SafetyLevel strings are diagnostic metadata, never retry authorization.
// Missing/unknown capability identities remain conservative legacy records.
func IsReadOnlyCapability(name string) bool {
	level, known := canonicalSafetyLevel(name)
	return known && level == skills.SafetyReadOnly
}

func canonicalSafetyLevel(name string) (skills.SafetyLevel, bool) {
	for _, manifest := range manipulation.Catalog() {
		if manifest.Name == name {
			return manifest.SafetyLevel, true
		}
	}
	return "", false
}

func (r *Runner) RunControlled(ctx context.Context, task *tasks.Task, control RunControl) (RunResult, error) {
	intents := task.Intent.Tasks()
	result := RunResult{TaskID: task.ID}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	if _, ok := r.store.(middleware.ExecutionReader); ok {
		if err := r.CheckRecovery(ctx, task.ID); err != nil {
			return result, err
		}
	}
	closure := r.loadClosureContext(ctx, task)
	for index, intent := range intents {
		grounded, err := r.grounder.Ground(ctx, intent)
		if err != nil {
			return result, fmt.Errorf("ground subtask %d: %w", index+1, err)
		}
		grounded.TaskID = task.ID
		grounded.RobotID = intent.RobotID
		grounded.Action = intent.Action
		grounded.KeepUpright = intent.Constraints.KeepUpright
		r.publishTelemetry(ctx, task, "grounded")
		if len(intents) > 1 {
			grounded.StepIDPrefix = fmt.Sprintf("task%02d-", index+1)
		}
		plan, err := r.planForIntent(task, index, grounded, intents)
		if err != nil {
			return result, fmt.Errorf("plan subtask %d: %w", index+1, err)
		}
		for index := range plan.Steps {
			level, known := canonicalSafetyLevel(plan.Steps[index].Skill)
			if !known {
				return result, fmt.Errorf("unknown skill %s", plan.Steps[index].Skill)
			}
			plan.Steps[index].SafetyLevel = string(level)
		}
		if err := guard.New(manipulation.Catalog()).Validate(plan); err != nil {
			return result, fmt.Errorf("validate subtask %d: %w", index+1, err)
		}
		graph, err := compiler.New().Compile(plan)
		if err != nil {
			return result, fmt.Errorf("compile subtask %d: %w", index+1, err)
		}
		if control.ObservationAttempt != "" {
			if err := r.validateResumeBindings(ctx, task, graph); err != nil {
				return result, err
			}
		}
		if err := r.checkRuntimeCapabilities(ctx, plan, task.Adapter); err != nil {
			return result, fmt.Errorf("subtask %d: %w", index+1, err)
		}
		if err := r.executePlan(ctx, task, graph, &result, control, closure); err != nil {
			return result, fmt.Errorf("subtask %d: %w", index+1, err)
		}
	}
	if control.BeforeStep != nil {
		if err := control.BeforeStep(ctx); err != nil {
			return result, err
		}
	}
	return result, nil
}

// A completed pick can only be reused for the same grounded object and
// parameters. Fresh grounding must not silently attach it to another entity.
func (r *Runner) validateResumeBindings(ctx context.Context, task *tasks.Task, graph compiler.ExecutionGraph) error {
	for _, stepID := range graph.Order {
		step := graph.Nodes[stepID].Step
		if step.SafetyLevel != string(skills.SafetyPhysical) {
			continue
		}
		status, err := r.store.StepStatus(ctx, task.ID, revisionExecutionStepID(task, stepID))
		if err != nil {
			return err
		}
		if status != middleware.StepCompleted {
			continue
		}
		command := CommandForTaskStep(task, step)
		expected, err := json.Marshal(command.Parameters)
		if err != nil {
			return err
		}
		matched := false
		for index := len(task.Events) - 1; index >= 0; index-- {
			event := task.Events[index]
			if event.Type != "TOOL_ACTIVITY" || event.Payload["commandId"] != command.CommandID {
				continue
			}
			status, _ := event.Payload["activityStatus"].(string)
			if status != "CONFIRMED" && status != "AWAITING_EVIDENCE" {
				continue
			}
			actual, err := json.Marshal(event.Payload["arguments"])
			if err != nil {
				return err
			}
			matched = bytes.Equal(expected, actual)
			break
		}
		if !matched {
			return fmt.Errorf("%w: %s", ErrResumeBindingChanged, stepID)
		}
	}
	return nil
}

func (r *Runner) executePlan(
	ctx context.Context,
	task *tasks.Task,
	graph compiler.ExecutionGraph,
	result *RunResult,
	control RunControl,
	closure *closureContext,
) error {
	for index, stepID := range graph.Order {
		if err := ctx.Err(); err != nil {
			return err
		}
		if control.BeforeStep != nil {
			if err := control.BeforeStep(ctx); err != nil {
				return err
			}
		}
		step := graph.Nodes[stepID].Step
		physical := step.SafetyLevel == string(skills.SafetyPhysical)
		executionStepID := revisionExecutionStepID(task, step.ID)
		status, err := r.store.StepStatus(ctx, task.ID, executionStepID)
		if err != nil {
			return err
		}
		refresh := control.ObservationAttempt != "" && !physical
		if refresh && step.Skill == "verify_grasp" {
			// Once a later physical step released the object, replaying grasp
			// verification would incorrectly reject an already completed place.
			for _, laterID := range graph.Order[index+1:] {
				later := graph.Nodes[laterID].Step
				if later.SafetyLevel != string(skills.SafetyPhysical) {
					continue
				}
				laterStatus, err := r.store.StepStatus(ctx, task.ID, revisionExecutionStepID(task, later.ID))
				if err != nil {
					return err
				}
				if laterStatus == middleware.StepCompleted {
					refresh = false
					break
				}
			}
		}
		if status == middleware.StepCompleted && !refresh {
			result.CompletedSteps = append(result.CompletedSteps, step.ID)
			continue
		}
		if status == middleware.StepStarted && physical {
			return fmt.Errorf("%w: %s", ErrPhysicalOutcomeUnknown, step.ID)
		}
		if physical && !task.Approved {
			return fmt.Errorf("%w: %s", ErrApprovalRequired, step.ID)
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		command := CommandForTaskStep(task, step)
		// The dispatch instant is the freshness floor for this attempt: only an
		// observation taken after it can confirm that this command changed the
		// world, no matter how fresh an earlier capture still looks.
		dispatchedAt := time.Now()
		command = commandAtDispatch(ctx, command, dispatchedAt)
		if refresh {
			command.CommandID += "/resume-read/" + control.ObservationAttempt
			command.IdempotencyKey = command.CommandID
		}
		record := middleware.StepRecord{TaskID: task.ID, StepID: executionStepID, IdempotencyKey: command.IdempotencyKey, Capability: step.Skill, SafetyLevel: step.SafetyLevel}
		if err := r.store.MarkStepStarted(ctx, record); err != nil {
			return err
		}
		r.publishToolActivity(ctx, task, command, "SENDING", nil, "")
		r.publishToolActivity(ctx, task, command, "RUNNING", nil, "")
		skillResult, err := r.invoker.Invoke(ctx, command)
		if err != nil {
			r.publishToolActivity(ctx, task, command, "FAILED", nil, err.Error())
			return err
		}
		if !skillResult.Success {
			if preflightFailure(runtime.CapabilityName(step.Skill), skillResult.Code) {
				// The navigation client performs this check before dispatching a
				// Nav2 goal. Persisting FAILED keeps the step retryable without
				// misclassifying a never-started motor command as unknown motion.
				persistContext, persistCancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
				if markErr := r.store.MarkStepFailed(persistContext, record); markErr != nil {
					persistCancel()
					return markErr
				}
				persistCancel()
			}
			r.publishFailedResult(ctx, task, command, skillResult, skillResult.Code)
			return fmt.Errorf("skill %s failed: %s %s", step.Skill, skillResult.Code, skillResult.Message)
		}
		if (step.Skill == "verify_grasp" || step.Skill == "verify_placement" || step.Skill == "verify_arrival") && skillResult.VerificationConfidence < 0.7 {
			r.publishFailedResult(ctx, task, command, skillResult, "verification confidence below threshold")
			return fmt.Errorf("%w: %s confidence %.2f", ErrVerificationFailed, step.ID, skillResult.VerificationConfidence)
		}
		// A returned success is evidence even if shutdown cancelled the request
		// concurrently. Persist it before considering another tool; an error or
		// missing receipt deliberately leaves the physical step STARTED.
		persistContext, persistCancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		// Archive the observation offered as post-condition evidence before the
		// completion gate inspects it, so a gate refusal leaves a reviewable
		// record instead of a bare claim. Persisting never completes the step.
		closureEvidence, savedCaptureID := r.closureEvidence(persistContext, task, step.ID, skillResult, skillResult.ObservationID)
		evidence := []string(nil)
		if savedCaptureID != "" {
			evidence = []string{savedCaptureID}
		}
		declaration := closure.declaration(step.Skill, step)
		decision := closedloop.Gate(declaration, dispatchedAt, closureEvidence)
		if err := decision.Require(); err != nil {
			// A write whose success cannot be confirmed is not a completed step
			// and not a known failure. Leave it STARTED so recovery reconciles
			// the real world before anything else touches the hardware.
			r.publishToolActivity(persistContext, task, command, "FAILED", evidence, decision.Message, skillResult.ObservationID)
			persistCancel()
			return fmt.Errorf("%w: %s %s: %s", ErrUnverifiedWorldMutation, step.ID, decision.Reason, decision.Message)
		}
		err = r.store.MarkStepCompleted(persistContext, record)
		if err != nil {
			persistCancel()
			return err
		}
		result.CompletedSteps = append(result.CompletedSteps, step.ID)
		persistCancel()
		eventContext, eventCancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		r.publishToolActivity(eventContext, task, command, "CONFIRMED", evidence, "", skillResult.ObservationID)
		eventCancel()
	}
	return nil
}

// preflightFailure is deliberately narrow. A failed physical command normally
// remains STARTED because the hardware outcome may be unknown; only the
// navigation readiness gate has a contractually guaranteed no-dispatch path.
func preflightFailure(skill runtime.CapabilityName, code string) bool {
	return skill == runtime.CapabilityNavigate && code == "NAV_MAP_NOT_READY"
}

// A failed verification is often the most useful camera record for diagnosis.
// Saving it never completes or replays the physical step.
func (r *Runner) publishFailedResult(ctx context.Context, task *tasks.Task, command runtime.Command, result runtime.Result, reason string) {
	persistContext, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer cancel()
	var evidence []string
	if result.Evidence != nil {
		if id := r.persistObservation(persistContext, task, command.StepID, *result.Evidence); id != "" {
			evidence = []string{id}
		}
	}
	r.publishToolActivity(persistContext, task, command, "FAILED", evidence, reason, result.ObservationID)
}

func (r *Runner) publishToolActivity(
	ctx context.Context,
	task *tasks.Task,
	command runtime.Command,
	status string,
	evidenceIDs []string,
	errorText string,
	receiptObservationIDs ...string,
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
	if len(receiptObservationIDs) > 0 && receiptObservationIDs[0] != "" {
		payload["receiptObservationId"] = receiptObservationIDs[0]
	}
	if len(evidenceIDs) > 0 {
		payload["evidenceSource"] = "post_tool_observation"
		if len(receiptObservationIDs) > 0 && evidenceIDs[0] == receiptObservationIDs[0] {
			payload["evidenceSource"] = "command_observation"
		}
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

// runtimeInfoProvider is implemented by robot clients that can report the
// connected runtime's capability view. The closure context uses it to learn
// about adapter-specific write tools the local catalog has never seen.
type runtimeInfoProvider interface {
	Info(context.Context) (runtime.Snapshot, error)
}

func (r *Runner) publishTelemetry(ctx context.Context, task *tasks.Task, stepID string) string {
	if r.Telemetry == nil {
		return ""
	}
	provider, ok := r.grounder.(telemetryProvider)
	if !ok {
		return ""
	}
	snapshot, err := provider.Telemetry(ctx, task.ID)
	if err != nil {
		return ""
	}
	return r.persistObservation(ctx, task, stepID, snapshot)
}

func (r *Runner) persistObservation(ctx context.Context, task *tasks.Task, stepID string, snapshot telemetry.Snapshot) string {
	if r.Telemetry == nil {
		return ""
	}
	snapshot.TaskID = task.ID
	snapshot.TaskRevision = task.CurrentRevision
	if snapshot.TaskRevision == 0 {
		snapshot.TaskRevision = 1
	}
	snapshot.StepID = revisionExecutionStepID(task, stepID)
	if stepID == "grounded" {
		snapshot.Activity = "OBSERVING"
	} else if stepID != "" {
		snapshot.Activity = "EXECUTING"
	}
	// The configured sink persists task-associated captures before returning.
	// A runtime receipt ID alone is not proof that historical image bytes exist.
	if err := r.Telemetry(ctx, snapshot); err != nil {
		return ""
	}
	if snapshot.Reconstruction != nil {
		return snapshot.Reconstruction.ObservationID
	}
	return ""
}

// closureContext carries the world-mutation declarations needed by the
// completion gate. Both the Agent's local catalog and the connected runtime's
// own capability declaration are consulted, because either may know a write
// tool the other has never seen.
type closureContext struct {
	local    map[string]bool
	remote   map[string]bool
	resolved bool
}

func newClosureContext() *closureContext {
	return &closureContext{local: map[string]bool{}, remote: map[string]bool{}}
}

func (c *closureContext) declaration(skill string, step taskgraph.SkillStep) closedloop.Declaration {
	if c == nil {
		// Without a profile the local catalog is still authoritative: a step
		// marked as a world mutation keeps its gate.
		return closedloop.Declaration{Manifest: mutatesWorldSkill(skill)}
	}
	_, known := c.remote[skill]
	return closedloop.Declaration{
		Manifest:               c.local[skill] || mutatesWorldSkill(skill),
		RuntimeMutatesWorld:    c.remote[skill],
		RuntimeCapabilityKnown: known,
	}
}

// mutatesWorldSkill consults the trusted local catalog. The catalog is a
// constant, so it cannot be influenced by a connected adapter or by task data.
func mutatesWorldSkill(skill string) bool {
	for _, manifest := range manipulation.Catalog() {
		if manifest.Name == skill {
			return manifest.MutatesWorld
		}
	}
	return false
}

// loadClosureContext reads the runtime capability view once per task. A runtime
// that cannot be queried leaves the remote half empty; the local catalog then
// decides, which is the conservative direction for the reference tools.
func (r *Runner) loadClosureContext(ctx context.Context, task *tasks.Task) *closureContext {
	context := newClosureContext()
	for _, manifest := range manipulation.Catalog() {
		context.local[manifest.Name] = manifest.MutatesWorld
	}
	if provider, ok := r.grounder.(runtimeInfoProvider); ok {
		if snapshot, err := provider.Info(ctx); err == nil {
			for _, capability := range snapshot.Capabilities {
				context.remote[capability.Name] = capability.MutatesWorld
			}
			context.resolved = true
		}
	}
	_ = task
	return context
}

// closureEvidence returns the fresh observation offered as proof that a write
// changed the world, together with its saved registry id. A tool that reports
// its own post-action capture is preferred; otherwise a telemetry read taken
// now is used. Both must post-date the dispatch to be accepted by the gate.
//
// receiptID is the runtime's own observation id from the command result. It is
// the fallback identity when neither path yields a persisted capture, so that
// the refusal message can name the observation an operator must inspect.
func (r *Runner) closureEvidence(
	ctx context.Context,
	task *tasks.Task,
	stepID string,
	result runtime.Result,
	receiptID string,
) (*closedloop.Evidence, string) {
	if result.Evidence != nil {
		snapshot := *result.Evidence
		savedID := r.persistObservation(ctx, task, stepID, snapshot)
		return evidenceFromSnapshot(snapshot, savedID, receiptID), savedID
	}
	provider, ok := r.grounder.(telemetryProvider)
	if !ok {
		return nil, ""
	}
	snapshot, err := provider.Telemetry(ctx, task.ID)
	if err != nil {
		return nil, ""
	}
	savedID := r.persistObservation(ctx, task, stepID, snapshot)
	return evidenceFromSnapshot(snapshot, savedID, receiptID), savedID
}

func evidenceFromSnapshot(snapshot telemetry.Snapshot, savedID, receiptID string) *closedloop.Evidence {
	evidence := &closedloop.Evidence{
		ObservationID: savedID,
		ObservedAt:    snapshot.ObservedAt.UTC(),
		SourceID:      snapshot.RobotID,
		Freshness:     "FRESH",
	}
	if snapshot.Reconstruction != nil {
		if snapshot.Reconstruction.ObservationID != "" {
			evidence.ObservationID = snapshot.Reconstruction.ObservationID
		}
		if snapshot.Reconstruction.ObservedAtUnixMS > 0 {
			evidence.ObservedAt = time.UnixMilli(snapshot.Reconstruction.ObservedAtUnixMS).UTC()
		}
		evidence.SourceID = snapshot.Reconstruction.SourceID
	}
	if evidence.ObservationID == "" {
		// No capture was persisted, but the runtime still identified the
		// observation it took after acting. Naming it keeps the refusal
		// reviewable; freshness is unaffected and still comes from the time.
		evidence.ObservationID = receiptID
	}
	if snapshot.EmergencyStopped {
		// An emergency stop during the action means the tool physically
		// confirmed nothing; treat the observation as unusable for closure.
		evidence.Freshness = "UNKNOWN"
	}
	for _, anomaly := range snapshot.Anomalies {
		if anomaly == "EMERGENCY_STOP_LATCHED" {
			evidence.Freshness = "UNKNOWN"
		}
	}
	return evidence
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
	// Mobile adapters require the local navigation/re-observation ordering;
	// legacy LLM templates must not silently omit that physical boundary.
	if len(grounded.NavigationGoal) == 0 && task.Plan != nil && task.Plan.LLMGenerated() && len(task.Plan.Plans) == len(intents) {
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

// Planning and safe pauses must not consume a future tool's execution budget.
// Only a not-yet-started dispatch receives a fresh bounded deadline; completed
// and uncertain physical steps have already been handled by the journal above.
func commandAtDispatch(ctx context.Context, command runtime.Command, now time.Time) runtime.Command {
	command.Deadline = now.Add(command.Lease)
	if parentDeadline, ok := ctx.Deadline(); ok && parentDeadline.Before(command.Deadline) {
		command.Deadline = parentDeadline
	}
	return command
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
