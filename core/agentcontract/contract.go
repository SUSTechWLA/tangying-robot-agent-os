// Package agentcontract defines what an Agent is, independent of who runs it.
//
// The interface lives in core/ rather than in the runtime that hosts agents for
// one deliberate reason: a TaskAgent must be usable as an Agent without the
// core execution path importing the layer that also knows about registries,
// event buses and orchestration. An agent is a participant, not a plugin of one
// particular host, so both sides depend only on this package.
//
// The package is values and interfaces only. It has no I/O, no infrastructure
// and no imports outside the standard library, which is what lets the
// architecture test keep treating it as core.
//
// # Why permissions are declared and also enforced
//
// Declaring "I am read-only" is documentation, not a control. Every agent states
// its permissions here so the runtime can refuse a mismatched request, and the
// runtime keeps the enforcement point. An agent that declares read-only and then
// asks to change the world is refused at the call, not trusted because of the
// declaration.
package agentcontract

import (
	"context"
	"errors"
	"time"
)

// Errors an agent or the runtime returns. They are declared here so a host can
// tell "this agent does not do that" apart from "this agent failed", which are
// different situations for an operator.
var (
	// ErrNotExecutable means the agent does not execute requests. An observing
	// agent returns this rather than pretending to work, so a mistaken call is
	// a visible refusal instead of a silent no-op.
	ErrNotExecutable = errors.New("agent does not execute requests")
	// ErrPermissionDenied means the runtime refused a request because it
	// exceeded the agent's declared permissions.
	ErrPermissionDenied = errors.New("agent permission denied")
	// ErrShutdown means the runtime or agent is no longer accepting work.
	ErrShutdown = errors.New("agent runtime is shut down")
)

// Capability is a coarse, declarative label for what an agent is for. It exists
// so a runtime can reason about a set of agents without type-switching on
// concrete implementations, and so an operator-facing view can say what is
// running. It is deliberately not a fine-grained tool list: physical tool
// authorization lives in the existing skill catalog and approval path.
type Capability string

const (
	// CapabilityTaskExecution means the agent executes tasks, including
	// physical ones, through the existing permission system.
	CapabilityTaskExecution Capability = "task.execution"
	// CapabilityObservation means the agent observes task and robot state and
	// changes nothing.
	CapabilityObservation Capability = "observation"
	// CapabilityDiagnosis means the agent produces root cause hypotheses.
	CapabilityDiagnosis Capability = "diagnosis"
	// CapabilityCompletionVerification is reserved for an EvalAgent that may
	// veto a completion claim.
	CapabilityCompletionVerification Capability = "completion.verification"
	// CapabilityExperienceRecording is reserved for an ExperienceAgent that
	// consolidates skills and memory.
	CapabilityExperienceRecording Capability = "experience.recording"
	// CapabilityEscalation is reserved for an EscalationAgent that owns the
	// human approval queue.
	CapabilityEscalation Capability = "escalation"
)

// Permission declares what an agent is allowed to do. The zero value is the
// safest reading of an unknown agent: read-only, no world mutation, approval
// required for anything else.
type Permission struct {
	// ReadOnly means the agent may not change task state or the world.
	ReadOnly bool
	// MutatesWorld means the agent may ask to run tools that change physical
	// state. It is only meaningful together with MutatesTaskState: an observer
	// that cannot change a task cannot legitimately drive hardware either.
	MutatesWorld bool
	// MutatesTaskState means the agent may change task lifecycle state.
	MutatesTaskState bool
	// RequiresApproval means every mutating request from this agent must carry
	// an operator approval before it is allowed through.
	RequiresApproval bool
}

// ReadOnlyPermission is the permission of an observer: it may look, and that is
// all. OpsAgent uses this, and the runtime enforces it.
func ReadOnlyPermission() Permission {
	return Permission{ReadOnly: true, RequiresApproval: true}
}

// MayMutateWorld reports whether the declaration in principle permits a world
// mutation. Enforcement is still the runtime's job; this only answers whether
// the request contradicts the agent's own declaration.
func (p Permission) MayMutateWorld() bool {
	return !p.ReadOnly && p.MutatesWorld
}

// MayMutateTaskState reports whether the declaration in principle permits a
// task state change.
func (p Permission) MayMutateTaskState() bool {
	return !p.ReadOnly && p.MutatesTaskState
}

// HealthStatus is the coarse health of one agent.
type HealthStatus string

const (
	// HealthUnknown means the agent has not reported yet. It is the zero value
	// so an agent that never implements health reading is not mistaken for
	// healthy.
	HealthUnknown HealthStatus = "UNKNOWN"
	// HealthHealthy means the agent is working normally.
	HealthHealthy HealthStatus = "HEALTHY"
	// HealthDegraded means the agent works but something is wrong.
	HealthDegraded HealthStatus = "DEGRADED"
	// HealthUnhealthy means the agent cannot do its job.
	HealthUnhealthy HealthStatus = "UNHEALTHY"
	// HealthStopped means the agent has been shut down.
	HealthStopped HealthStatus = "STOPPED"
)

// Health is a point-in-time self report. It carries a reason code rather than
// free prose so it can be asserted on and so a UI does not have to parse text.
type Health struct {
	Status HealthStatus `json:"status"`
	// ReasonCode is a stable machine-readable code such as
	// TELEMETRY_UNAVAILABLE. Free text belongs in Detail.
	ReasonCode string `json:"reasonCode,omitempty"`
	// Detail carries supporting facts. Bounded and non-secret.
	Detail map[string]string `json:"detail,omitempty"`
	// CheckedAt is when the report was produced, not when it was read.
	CheckedAt time.Time `json:"checkedAt"`
}

// Observation is one piece of evidence an agent is given to reason about. It is
// intentionally a value type: an agent must not be able to reach back into the
// runtime through it.
type Observation struct {
	// TaskID is the task the observation belongs to, empty for robot-wide
	// observations that are not about one task.
	TaskID string
	// TaskRevision is the execution revision the observation belongs to.
	TaskRevision uint64
	// StepID is the step the observation belongs to, when it is step-scoped.
	StepID string
	// RobotID is the robot the observation describes.
	RobotID string
	// Kind is a stable discriminator such as task.state or robot.telemetry.
	Kind string
	// SourceID identifies the producer, so an agent can tell a robot's own
	// report from a derived projection.
	SourceID string
	// ObservedAt is the sensor or producer clock, not the receipt time.
	// Freshness decisions must use this field.
	ObservedAt time.Time
	// Payload is the structured body. Treat it as read-only.
	Payload map[string]any
}

// ExecuteRequest is what an agent is asked to do. TaskID is the only required
// field for task execution; revision 0 means "whatever the task currently is",
// which is what a fresh run wants and what a resume deliberately overrides.
type ExecuteRequest struct {
	TaskID string
	// TaskRevision pins the execution to one immutable revision. Zero means the
	// task's current revision.
	TaskRevision uint64
	// ObservationAttempt marks an explicit resume. It must be empty for a fresh
	// run: it changes read identities so a cached pre-interruption observation
	// cannot be replayed as fresh evidence.
	ObservationAttempt string
	// BeforeStep, when set, is called at each step boundary. Returning an error
	// stops the run at that boundary. It exists so a host can implement pause
	// without the agent knowing what a pause is.
	BeforeStep func(context.Context) error
}

// ExecutionOutcome is what the agent reports about one execute call.
//
// State is the task state the agent left the task in, as it observed it. It is
// a report, not an instruction: the state authority remains the task service,
// and a runtime must not treat this field as the source of truth for anything
// it enforces.
type ExecutionOutcome struct {
	TaskID         string   `json:"taskId"`
	State          string   `json:"state"`
	ReasonCode     string   `json:"reasonCode,omitempty"`
	CompletedSteps []string `json:"completedSteps,omitempty"`
}

// Agent is the single interface every participant implements.
//
// The methods split into a static half (identity, subscriptions, permissions)
// that a runtime reads before starting anything, and a dynamic half (health,
// events, execution, shutdown) used while running. Implementations must be safe
// for the static half to be called concurrently with the dynamic half.
type Agent interface {
	// Name is the stable identity used in config, events and logs. It must not
	// change between restarts: it is how a durable event stream is attributed.
	Name() string
	// Version is the agent's own version, for correlating behaviour changes
	// with the events they produced.
	Version() string
	// Capabilities declares what the agent is for.
	Capabilities() []Capability
	// Subscriptions returns the event topics the agent wants delivered to
	// OnEvent. Patterns may use a trailing ".*" wildcard. An agent that only
	// publishes returns nil.
	Subscriptions() []string
	// Permissions declares what the agent may do. The runtime enforces it.
	Permissions() Permission
	// Health reports the agent's current condition. It must be cheap and must
	// not panic; it is called on a timer and on change.
	Health(ctx context.Context) Health
	// OnEvent receives one subscribed event. It must return promptly: the
	// runtime delivers on a bounded queue, and a slow agent loses events rather
	// than blocking the publisher.
	OnEvent(ctx context.Context, event Event) error
	// Execute performs the agent's work for one request. An agent that only
	// observes returns ErrNotExecutable rather than pretending to work.
	Execute(ctx context.Context, request ExecuteRequest) (ExecutionOutcome, error)
	// Shutdown releases resources and stops background work. It must be safe to
	// call more than once and must not block indefinitely: the runtime bounds
	// it. Shutdown must never cancel work that a task still depends on
	// completing; that is the host's decision, not the agent's.
	Shutdown(ctx context.Context) error
}

// Static half of an agent, kept for adapters that need to describe an agent
// without holding the full interface.
type Descriptor struct {
	Name          string       `json:"name"`
	Version       string       `json:"version"`
	Capabilities  []Capability `json:"capabilities,omitempty"`
	Subscriptions []string     `json:"subscriptions,omitempty"`
	Permissions   Permission   `json:"permissions"`
}

// Describe reads the static half of an agent.
func Describe(agent Agent) Descriptor {
	if agent == nil {
		return Descriptor{}
	}
	return Descriptor{
		Name:          agent.Name(),
		Version:       agent.Version(),
		Capabilities:  agent.Capabilities(),
		Subscriptions: agent.Subscriptions(),
		Permissions:   agent.Permissions(),
	}
}
