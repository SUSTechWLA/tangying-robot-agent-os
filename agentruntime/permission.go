package agentruntime

import (
	"context"
	"errors"
	"fmt"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// The permission gate.
//
// Declaring "I am read-only" is documentation. This is the control: every
// request an agent makes that would change the world or a task goes through
// RequestMutation, which compares the request against what that agent declared
// and refuses a mismatch.
//
// The gate is deliberately not a second safety system. It does not decide
// whether an action is safe, whether a step may be retried, or whether approval
// exists in the operator's sense — those already have owners. It answers one
// narrow question: is the agent that is asking the kind of agent that may ask
// this at all?
//
// That narrowness is the point. A gate that tried to re-derive safety would
// eventually disagree with the closed-loop contract, and the disagreement would
// be resolved in whichever direction was implemented last.

// Refusal reasons. They are stable codes because they end up in the durable
// event stream and in operator guidance.
const (
	// RefusalReadOnly means a read-only agent asked to change something.
	RefusalReadOnly = "AGENT_READ_ONLY"
	// RefusalUndeclaredCapability means the agent asked for something it never
	// declared it could do.
	RefusalUndeclaredCapability = "CAPABILITY_NOT_DECLARED"
	// RefusalApprovalMissing means the agent's own declaration requires approval
	// and the request did not carry it.
	RefusalApprovalMissing = "AGENT_APPROVAL_REQUIRED"
	// RefusalUnknownAgent means the named agent is not registered.
	RefusalUnknownAgent = "AGENT_NOT_REGISTERED"
	// RefusalNoMutation means the request asked for nothing.
	RefusalNoMutation = "NO_MUTATION_REQUESTED"
)

// ErrPermissionDenied is the sentinel every refusal wraps, so a caller can test
// for "refused" without matching on a reason code.
var ErrPermissionDenied = agentcontract.ErrPermissionDenied

// MutationRequest is an agent asking to change something.
type MutationRequest struct {
	// Agent is the requesting agent's Name.
	Agent string
	// Capability is what it wants to do, as a capability identifier. It must be
	// one the agent declared.
	Capability string
	// TaskID scopes the request, when it concerns one task.
	TaskID string
	// MutatesWorld and MutatesTaskState say what the request would change.
	MutatesWorld     bool
	MutatesTaskState bool
	// ApprovalPresent says an operator has already approved this exact action
	// through the existing approval path. It is not a substitute for that path;
	// it only records that the path was followed.
	ApprovalPresent bool
	// Reason is a short human explanation, carried into the refusal record.
	Reason string
}

// MutationDecision is the gate's answer.
type MutationDecision struct {
	Allowed bool
	// ReasonCode is stable and set whenever Allowed is false.
	ReasonCode string
	// Detail is a short human explanation.
	Detail string
}

// RequestMutation asks the gate whether an agent may change something, and
// records a refusal when it may not.
//
// A refusal is published as an event rather than only returned, because a
// refusal that leaves no trace is indistinguishable from an agent that never
// asked. Observing what an agent tried to do is often more informative than
// observing what it did.
func (o *Orchestrator) RequestMutation(ctx context.Context, request MutationRequest) (MutationDecision, error) {
	decision := o.decide(request)
	if decision.Allowed {
		return decision, nil
	}
	o.publishRefusal(ctx, request, decision)
	return decision, fmt.Errorf("%w: %s: %s", ErrPermissionDenied, decision.ReasonCode, decision.Detail)
}

// decide is the pure decision, separated so it can be tested exhaustively
// without a running orchestrator.
func (o *Orchestrator) decide(request MutationRequest) MutationDecision {
	if !request.MutatesWorld && !request.MutatesTaskState {
		return MutationDecision{
			ReasonCode: RefusalNoMutation,
			Detail:     "the request does not ask to change anything, so there is nothing to authorise",
		}
	}
	agent, ok := o.registry.Discover(request.Agent)
	if !ok {
		return MutationDecision{
			ReasonCode: RefusalUnknownAgent,
			Detail:     "the requesting agent is not registered",
		}
	}
	permission := agent.Permissions()

	// Read-only first: an observer asking to change the world is the case this
	// gate exists for, and it must be refused on the declaration alone rather
	// than on any finer check that a future change might relax.
	if permission.ReadOnly {
		return MutationDecision{
			ReasonCode: RefusalReadOnly,
			Detail:     "the agent declared itself read-only, so it may observe and report but not change anything",
		}
	}
	if request.MutatesWorld && !permission.MutatesWorld {
		return MutationDecision{
			ReasonCode: RefusalReadOnly,
			Detail:     "the agent did not declare that it may change the physical world",
		}
	}
	if request.MutatesTaskState && !permission.MutatesTaskState {
		return MutationDecision{
			ReasonCode: RefusalReadOnly,
			Detail:     "the agent did not declare that it may change task state",
		}
	}
	if request.Capability != "" && !declaresCapability(agent, request.Capability) {
		return MutationDecision{
			ReasonCode: RefusalUndeclaredCapability,
			Detail:     "the agent did not declare the capability it is asking for",
		}
	}
	if permission.RequiresApproval && !request.ApprovalPresent {
		return MutationDecision{
			ReasonCode: RefusalApprovalMissing,
			Detail:     "this agent's requests must carry an operator approval, and none was present",
		}
	}
	return MutationDecision{Allowed: true}
}

// declaresCapability reports whether an agent declared a capability.
//
// An agent with no declared capabilities is treated as unable to do anything:
// the declaration is the only statement of authority the runtime has, and
// assuming authority that was never declared is the wrong direction to guess.
func declaresCapability(agent agentcontract.Agent, capability string) bool {
	for _, declared := range agent.Capabilities() {
		if string(declared) == capability {
			return true
		}
	}
	return false
}

// publishRefusal records a refused request.
func (o *Orchestrator) publishRefusal(ctx context.Context, request MutationRequest, decision MutationDecision) {
	o.runtime.Publish(ctx, agentcontract.Event{
		Topic: agentcontract.TopicAgentPermissionDenied, TaskID: request.TaskID,
		Agent: "orchestrator", Priority: agentcontract.PriorityHigh, OccurredAt: o.now().UTC(),
		CorrelationID: request.TaskID,
		Payload: agentcontract.PermissionDeniedPayload{
			Capability: request.Capability, ReasonCode: decision.ReasonCode,
			MutatesWorld: request.MutatesWorld, MutatesTaskState: request.MutatesTaskState,
			Detail: refusalDetail(request, decision),
		}.Encode(),
	})
}

func refusalDetail(request MutationRequest, decision MutationDecision) string {
	detail := fmt.Sprintf("agent %s was refused: %s", request.Agent, decision.Detail)
	if request.Reason != "" {
		detail += " (requested: " + request.Reason + ")"
	}
	return detail
}

// publishAgentError records that an agent failed to handle an event.
//
// It is recorded rather than propagated: an observer whose reaction failed is
// that observer's problem, and letting it become the task's problem would make
// observability part of the safety path.
func (o *Orchestrator) publishAgentError(ctx context.Context, agent agentcontract.Agent, event agentcontract.Event, err error) {
	o.runtime.Publish(ctx, agentcontract.Event{
		Topic: agentcontract.TopicAgentHealthChanged, TaskID: event.TaskID,
		Agent: agent.Name(), AgentVersion: agent.Version(),
		Priority: agentcontract.PriorityHigh, OccurredAt: o.now().UTC(),
		CausationID: event.ID, CorrelationID: event.CorrelationID,
		Payload: agentcontract.HealthChangedPayload{
			Previous: agentcontract.HealthUnknown, Current: agentcontract.HealthDegraded,
			ReasonCode: "EVENT_HANDLING_FAILED",
		}.Encode(),
	})
}

// ErrReadOnlyAgent is returned when a read-only agent attempts an execution.
var ErrReadOnlyAgent = errors.New("read-only agent cannot execute")
