package agentruntime

import (
	"context"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// Only this registration-bound port is given to an agent. The composition root
// retains the trusted runtime port for ledger projections and verifier results.
func (o *Orchestrator) publisherFor(agent agentcontract.Agent) func(context.Context, agentcontract.Event) {
	name, version, permission := agent.Name(), agent.Version(), agent.Permissions()
	return func(ctx context.Context, event agentcontract.Event) {
		reason := ""
		if (event.Agent != "" && event.Agent != name) || (event.AgentVersion != "" && event.AgentVersion != version) {
			reason = "AGENT_PUBLISHER_IDENTITY_MISMATCH"
		} else if !agentMayPublish(event.Topic, permission) {
			reason = "AGENT_TOPIC_NOT_OWNED"
		}
		if reason != "" {
			o.runtime.Publish(ctx, agentcontract.Event{
				Topic: agentcontract.TopicAgentPermissionDenied, TaskID: event.TaskID,
				TaskRevision: event.TaskRevision, StepID: event.StepID, RobotID: event.RobotID, CommandID: event.CommandID,
				Agent: "orchestrator", CausationID: event.ID, Priority: agentcontract.PriorityHigh,
				Payload: map[string]any{"reasonCode": reason, "publisher": name, "requestedTopic": event.Topic},
			})
			return
		}
		event.Agent, event.AgentVersion = name, version
		o.runtime.Publish(ctx, event)
	}
}

func agentMayPublish(topic string, permission agentcontract.Permission) bool {
	switch topic {
	case agentcontract.TopicOpsAnomalyDetected, agentcontract.TopicOpsRootCauseHypothesis,
		agentcontract.TopicOpsRecoveryProposed, agentcontract.TopicOpsEscalationRequired,
		agentcontract.TopicOpsRecoveryPlan, agentcontract.TopicOpsAnomalyCleared,
		agentcontract.TopicAgentHealthChanged:
		return true // Advisory only; these events carry no execution authority.
	case agentcontract.TopicActionExecuted, agentcontract.TopicActionPreparationFailed,
		agentcontract.TopicEvidenceCollected, agentcontract.TopicStateTransition,
		agentcontract.TopicTaskStarted, agentcontract.TopicTaskCompleted, agentcontract.TopicTaskFailed:
		return permission.MayMutateTaskState()
	default:
		// recovery_executed belongs to the actual executor/verifier wiring;
		// registration and deferral belong to the hosting runtime. An agent
		// may report its own health through the registration-bound identity.
		return false
	}
}
