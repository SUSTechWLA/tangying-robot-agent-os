package agent

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/skills"
	"github.com/SUSTechWLA/tangying-robot-agent-os/edge/runtime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/skills/manipulation"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// This file makes *Runner a native agentcontract.Agent.
//
// The execution logic above is unchanged and does not depend on this file: the
// agent methods are a second way in, not a new way through. Run and
// RunControlled keep their existing signatures because the local execution
// lifecycle, the pause/resume path and every existing test depend on them, and
// an interface migration is not a reason to rewrite a working boundary.
//
// The one thing worth stating plainly: implementing an interface does not grant
// permission. The runtime refuses mutating requests from read-only agents, and
// this agent's declared permissions are what the runtime checks. Its real
// authority still comes from the existing approval, lease, fencing and
// closed-loop gates, none of which this file touches.

var _ agentcontract.Agent = (*Runner)(nil)

// Name returns the stable configuration and event-attribution identity.
func (r *Runner) Name() string { return TaskAgentName }

// Version identifies this agent's behaviour, not the release.
func (r *Runner) Version() string { return TaskAgentVersion }

// Capabilities reports the tool catalog this agent can actually execute. It is
// read from the catalog rather than hand-listed so it cannot drift away from
// what the guard and the runtime capability check will accept.
func (r *Runner) Capabilities() []agentcontract.Capability {
	names := make([]string, 0, len(manipulation.Catalog()))
	for _, manifest := range manipulation.Catalog() {
		names = append(names, manifest.Name)
	}
	sort.Strings(names)
	capabilities := make([]agentcontract.Capability, 0, len(names)+1)
	capabilities = append(capabilities, agentcontract.CapabilityTaskExecution)
	for _, name := range names {
		capabilities = append(capabilities, agentcontract.Capability(name))
	}
	return capabilities
}

// Subscriptions is empty on purpose. This agent executes tasks when asked; it
// does not react to events, and pretending it subscribes would put it in a
// delivery path where a slow executor could lose events it never needed.
func (r *Runner) Subscriptions() []string { return nil }

// Permissions declares that this agent may change the world and task state.
//
// RequiresApproval is false here even though physical steps need approval,
// because approval for physical work is already enforced per step inside the
// execution path (Task.Approved, then the dispatch gate). Duplicating it at the
// agent level would either be redundant or, worse, become the thing someone
// later relaxes. The runtime's job is to refuse what contradicts this
// declaration, not to re-implement the safety path.
func (r *Runner) Permissions() agentcontract.Permission {
	return agentcontract.Permission{
		ReadOnly:         false,
		MutatesWorld:     true,
		MutatesTaskState: true,
		RequiresApproval: false,
	}
}

// Health reports whether this agent can do its job. It checks prerequisites it
// can actually establish, and returns UNKNOWN rather than HEALTHY for anything
// it cannot: an agent that reports health it has not verified is worse than one
// that admits ignorance.
func (r *Runner) Health(_ context.Context) agentcontract.Health {
	now := r.now().UTC()
	r.mu.Lock()
	stopped := r.stopped
	r.mu.Unlock()
	if stopped {
		return agentcontract.Health{
			Status: agentcontract.HealthStopped, ReasonCode: "AGENT_SHUTDOWN", CheckedAt: now,
		}
	}
	if r.store == nil {
		return agentcontract.Health{
			Status: agentcontract.HealthUnhealthy, ReasonCode: "EXECUTION_STORE_UNAVAILABLE",
			Detail: map[string]string{
				"reason": "without durable step records a world mutation cannot be reconciled",
			},
			CheckedAt: now,
		}
	}
	if r.grounder == nil || r.invoker == nil {
		return agentcontract.Health{
			Status: agentcontract.HealthDegraded, ReasonCode: "EXECUTION_PORTS_MISSING",
			Detail:    map[string]string{"reason": "grounding or runtime invocation is unavailable"},
			CheckedAt: now,
		}
	}
	return agentcontract.Health{Status: agentcontract.HealthHealthy, CheckedAt: now}
}

// OnEvent is a no-op. This agent publishes facts rather than reacting to them,
// and Subscriptions is empty, so the runtime should never deliver anything. It
// returns nil instead of an error so that a future runtime change which does
// deliver an event cannot turn a delivered event into a task failure.
func (r *Runner) OnEvent(context.Context, agentcontract.Event) error { return nil }

// Execute runs one task request and reports the state it left the task in.
//
// It delegates to RunControlled, so every closed-loop rule applies unchanged:
// a returned success is still only a trigger to look at the world, a mutation
// without fresh evidence still leaves the step STARTED, and an unknown physical
// outcome is still never retried automatically.
//
// Callers that already hold the task (the local execution lifecycle and the
// fleet worker) keep using Run, which has the signature it always had.
func (r *Runner) Execute(ctx context.Context, request agentcontract.ExecuteRequest) (agentcontract.ExecutionOutcome, error) {
	outcome := agentcontract.ExecutionOutcome{TaskID: request.TaskID}
	if r.isStopped() {
		return outcome, agentcontract.ErrShutdown
	}
	if request.TaskID == "" {
		return outcome, errors.New("task agent requires a task id")
	}
	if r.Tasks == nil {
		return outcome, errors.New("task agent requires a task reader")
	}
	task, err := r.Tasks.Get(ctx, request.TaskID)
	if err != nil {
		return outcome, fmt.Errorf("read task %s: %w", request.TaskID, err)
	}
	if task == nil {
		return outcome, fmt.Errorf("read task %s: %w", request.TaskID, tasks.ErrTaskNotFound)
	}
	if request.TaskRevision != 0 && task.CurrentRevision != request.TaskRevision {
		return outcome, fmt.Errorf("%w: requested revision %d, task is at %d",
			ErrResumeBindingChanged, request.TaskRevision, task.CurrentRevision)
	}
	result, err := r.RunControlled(ctx, task, RunControl{
		ObservationAttempt: request.ObservationAttempt,
		BeforeStep:         request.BeforeStep,
	})
	outcome.CompletedSteps = result.CompletedSteps
	outcome.ReasonCode, outcome.State = r.outcomeOf(ctx, request.TaskID, err)
	return outcome, err
}

// outcomeOf reports the state the task is actually in, read back from the
// authority rather than inferred from the error.
//
// This distinction matters. The runner does not own task lifecycle state — the
// task service does — so an outcome guessed from an error string would be a
// second opinion that can disagree with the record. When the state cannot be
// read, or when the error means the task deliberately remains non-terminal
// (an unverified world mutation stays STARTED so it can be reconciled), the
// state is reported as unknown instead of invented.
func (r *Runner) outcomeOf(ctx context.Context, taskID string, runErr error) (string, string) {
	reason := reasonCodeFor(runErr)
	if r.Tasks == nil {
		return reason, ""
	}
	readContext, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer cancel()
	task, err := r.Tasks.Get(readContext, taskID)
	if err != nil || task == nil {
		return reason, ""
	}
	return reason, string(task.State)
}

// reasonCodeFor maps an execution error to a stable reason code.
//
// The mapping is deliberately explicit rather than a string match on the
// message: these codes end up in the durable event stream and in operator
// guidance, so they must not change when an error message is reworded.
func reasonCodeFor(runErr error) string {
	switch {
	case runErr == nil:
		return "TASK_EXECUTION_COMPLETED"
	case errors.Is(runErr, context.Canceled):
		return "TASK_EXECUTION_CANCELLED"
	case errors.Is(runErr, context.DeadlineExceeded):
		return "TASK_EXECUTION_DEADLINE_EXCEEDED"
	case errors.Is(runErr, ErrPauseRequested):
		return "TASK_EXECUTION_PAUSED"
	case errors.Is(runErr, ErrApprovalRequired):
		return "APPROVAL_REQUIRED"
	case errors.Is(runErr, ErrPhysicalOutcomeUnknown):
		return "PHYSICAL_OUTCOME_UNKNOWN"
	case errors.Is(runErr, ErrUnverifiedWorldMutation):
		return "UNVERIFIED_WORLD_MUTATION"
	case errors.Is(runErr, ErrVerificationFailed):
		return "VERIFICATION_FAILED"
	case errors.Is(runErr, ErrResumeBindingChanged):
		return "RESUME_BINDING_CHANGED"
	case errors.Is(runErr, ErrRecoveryUnavailable):
		return "RUNTIME_JOURNAL_UNAVAILABLE"
	default:
		return "TASK_EXECUTION_FAILED"
	}
}

// Shutdown marks the agent stopped.
//
// It deliberately does not cancel an in-flight task. Cancelling running physical
// work is the host's decision because only the host knows whether the task can
// be resumed and where the safe point is. An agent that cancelled work on its
// own shutdown would be deciding that on the host's behalf.
func (r *Runner) Shutdown(context.Context) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.stopped = true
	return nil
}

func (r *Runner) isStopped() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.stopped
}

// publish sends one agent event when a sink is installed. A nil sink is the
// pre-runtime behaviour and stays silent.
func (r *Runner) publish(ctx context.Context, event agentcontract.Event) {
	if r.Events == nil {
		return
	}
	if event.Agent == "" {
		event.Agent = TaskAgentName
		event.AgentVersion = TaskAgentVersion
	}
	if event.OccurredAt.IsZero() {
		event.OccurredAt = r.now().UTC()
	}
	r.Events(ctx, event)
}

// publishAction broadcasts one tool dispatch transition to the event bus in
// addition to the durable task event stream.
//
// Both paths carry the same facts. The durable stream is what the task replay
// reads; the bus is what a live observer subscribes to. Publishing is additive
// and failure-free by construction — the sink returns nothing — so no
// subscriber can make a step fail.
func (r *Runner) publishAction(ctx context.Context, task *tasks.Task, command runtime.Command, status string, evidenceIDs []string, errorText, receiptID string) {
	if r.Events == nil {
		return
	}
	payload := agentcontract.ActionPayload{
		ToolName: string(command.Capability), ActivityStatus: status, RobotID: command.RobotID,
		StepID: command.StepID, CommandID: command.CommandID, SafetyLevel: safetyLevelFor(command.Capability),
		FencingToken: command.FencingToken, TaskRevision: command.TaskRevision,
		AggregateVersion: command.AggregateVersion, Arguments: command.Parameters,
		EvidenceIDs: evidenceIDs, Error: errorText,
	}
	event := agentcontract.Event{
		Topic: agentcontract.TopicActionExecuted, TaskID: task.ID,
		TaskRevision: command.TaskRevision, StepID: command.StepID,
		Priority: agentcontract.PriorityNormal, CorrelationID: task.ID,
		Payload: payload.Encode(),
	}
	if status == "FAILED" {
		event.Priority = agentcontract.PriorityHigh
	}
	r.publish(ctx, event)
	if len(evidenceIDs) == 0 && receiptID == "" {
		return
	}
	source := "post_tool_observation"
	if len(evidenceIDs) > 0 && evidenceIDs[0] == receiptID {
		source = "command_observation"
	}
	r.publish(ctx, agentcontract.Event{
		Topic: agentcontract.TopicEvidenceCollected, TaskID: task.ID,
		TaskRevision: command.TaskRevision, StepID: command.StepID,
		Priority: agentcontract.PriorityNormal, CorrelationID: task.ID,
		CausationID: event.ID,
		Payload: agentcontract.EvidencePayload{
			StepID: command.StepID, Source: source, ToolName: string(command.Capability),
			EvidenceIDs: evidenceIDs, ReceiptObservationID: receiptID,
		}.Encode(),
	})
}

// safetyLevelFor reads the trusted catalog for a capability's safety level.
// An unknown capability reports empty rather than a guessed level: the
// catalog is the only authority for what a tool does to the world.
func safetyLevelFor(capability runtime.CapabilityName) string {
	level, known := canonicalSafetyLevel(string(capability))
	if !known {
		return ""
	}
	return string(level)
}

// mutatesWorldCapability reports whether the trusted catalog marks a capability
// as a world mutation. Used when reporting a state transition, so an observer
// can tell a physical step boundary from a read.
func mutatesWorldCapability(capability runtime.CapabilityName) bool {
	for _, manifest := range manipulation.Catalog() {
		if manifest.Name == string(capability) {
			return manifest.MutatesWorld && manifest.SafetyLevel == skills.SafetyPhysical
		}
	}
	return false
}

// RunRequest executes an already-loaded task under an explicit control,
// reporting the state it left the task in.
//
// It is the entry point for a host that runs in the same process as the agent
// and therefore already holds the task, and that must be able to express an
// explicit resume and a step-boundary hook. It is deliberately not called Run:
// Run(ctx, task) is the long-standing two-argument entry point that the local
// execution lifecycle, the fleet worker and a dozen tests already call, and
// widening an interface migration into a call-site rewrite would buy nothing.
// Both reach RunControlled, so there is one implementation of "how a task runs"
// and not two.
func (r *Runner) RunRequest(ctx context.Context, task *tasks.Task, control agentcontract.ExecuteRequest) (agentcontract.ExecutionOutcome, error) {
	outcome := agentcontract.ExecutionOutcome{TaskID: taskIDOf(task)}
	if r.isStopped() {
		return outcome, agentcontract.ErrShutdown
	}
	if task == nil {
		return outcome, errors.New("task agent requires a task")
	}
	result, err := r.RunControlled(ctx, task, RunControl{
		ObservationAttempt: control.ObservationAttempt,
		BeforeStep:         control.BeforeStep,
	})
	outcome.CompletedSteps = result.CompletedSteps
	outcome.ReasonCode, outcome.State = r.outcomeOf(ctx, task.ID, err)
	return outcome, err
}

func taskIDOf(task *tasks.Task) string {
	if task == nil {
		return ""
	}
	return task.ID
}
