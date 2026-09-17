package agentruntime

import (
	"strconv"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/tasks"
)

// This file is the bridge between the durable task event stream and the agent
// event stream.
//
// The repository already has one durable, ordered, per-task event ledger with a
// replay projection built on it. Adding a second event store for agents would
// mean two records of the same task that can disagree, and a replay that shows
// one of them. So the agent runtime projects the existing events into its
// vocabulary instead: task.started, action.executed and the rest are the same
// facts the ledger already holds, named in the agent vocabulary.
//
// The direction that matters for correctness is the other one: an event an
// agent *publishes* is persisted into that same ledger, which is what makes an
// OpsAgent's findings visible in the ordinary task replay without teaching the
// replay about agents.

// ProjectTaskEvent projects one recorded task event into the agent vocabulary.
//
// It is exported because the composition root is what connects the durable
// ledger to the bus, and connecting them is wiring rather than runtime policy.
func ProjectTaskEvent(taskID string, event tasks.TaskEvent) (agentcontract.Event, bool) {
	return taskEventToEvent(taskID, event)
}

// TaskEventFromEvent renders an agent event as a durable ledger entry. It is the
// other half of the bridge ProjectTaskEvent opens.
func TaskEventFromEvent(event agentcontract.Event) (tasks.TaskEvent, bool) {
	return taskEventFromEvent(event)
}

// taskEventToEvent projects one recorded task event into the agent vocabulary.
//
// It returns false for event types that are not part of the agent vocabulary.
// Dropping them is deliberate rather than an oversight: an unrecognised event
// type is a fact the ledger keeps and the agent layer has nothing to say about,
// and inventing a topic for it would put a name in the stream that no
// subscriber declared.
func taskEventToEvent(taskID string, event tasks.TaskEvent) (agentcontract.Event, bool) {
	if taskID == "" || event.Type == "" {
		return agentcontract.Event{}, false
	}
	projected := agentcontract.Event{
		ID:         projectionID(taskID, event),
		TaskID:     taskID,
		StepID:     event.StepID,
		OccurredAt: event.OccurredAt,
		Priority:   agentcontract.PriorityNormal,
		// The task identifies the correlation: every event about one task
		// belongs to the same story, whichever agent produced it.
		CorrelationID: taskID,
	}
	payload := clonePayload(event.Payload)
	switch event.Type {
	case "TOOL_ACTIVITY":
		projected.Topic = agentcontract.TopicActionExecuted
		projected.Agent = "task"
		if status, _ := payload["activityStatus"].(string); status == "FAILED" {
			projected.Priority = agentcontract.PriorityHigh
		}
		if revision, ok := payloadUint(payload["taskRevision"]); ok {
			projected.TaskRevision = revision
		}
	case "STATE_CHANGED":
		projected.Topic = agentcontract.TopicStateTransition
		projected.Agent = "task"
		if destination, _ := payload["state"].(string); destination != "" {
			projected.Priority = transitionPriority(destination)
		}
	case "GROUNDING_EVIDENCE":
		projected.Topic = agentcontract.TopicEvidenceCollected
		projected.Agent = "task"
		payload["source"] = "grounding"
	case "LOCAL_RUN_SUCCEEDED":
		projected.Topic = agentcontract.TopicTaskCompleted
		projected.Agent = "task"
		projected.Priority = agentcontract.PriorityHigh
	case "RECOVERY_ACTIVITY":
		// Public recovery guidance is authored by the server for the operator.
		// It is an ops-shaped fact, so it is projected into the ops namespace
		// where an observer can subscribe to it as one thing rather than
		// special-casing a task event type.
		projected.Topic = agentcontract.TopicOpsRecoveryProposed
		projected.Agent = "task"
	case "SAFETY_STOPPED":
		projected.Topic = agentcontract.TopicOpsEscalationRequired
		projected.Agent = "task"
		projected.Priority = agentcontract.PriorityCritical
	default:
		// A topic-shaped type is an event an agent already published and the
		// ledger already holds verbatim. Republishing it would duplicate every
		// agent event, so it is left alone here.
		if agentcontract.KnownTopic(event.Type) {
			return agentcontract.Event{}, false
		}
		return agentcontract.Event{}, false
	}
	if len(payload) > 0 {
		projected.Payload = payload
	}
	if projected.OccurredAt.IsZero() {
		projected.OccurredAt = time.Now().UTC()
	}
	return projected, true
}

// transitionPriority maps a task state to how much it should interrupt. A
// transition into a state a human must act on is urgent; a transition through
// normal progress is not. Treating them alike is how a console becomes noise
// nobody reads.
func transitionPriority(state string) agentcontract.Priority {
	switch taskgraphState(state) {
	case "SUCCEEDED", "VERIFYING":
		return agentcontract.PriorityHigh
	case "RECOVERABLE_FAILURE", "FAILED", "FAILED_SAFE", "SAFETY_STOPPED",
		"WAITING_USER", "BLOCKED", "CANCELLED":
		return agentcontract.PriorityHigh
	case "EXECUTING", "OBSERVING", "PLANNING":
		return agentcontract.PriorityNormal
	default:
		return agentcontract.PriorityLow
	}
}

// taskgraphState normalizes a state string for comparison. It exists so this
// file does not have to import the task graph package for a string compare.
func taskgraphState(state string) string {
	return strings.ToUpper(strings.TrimSpace(state))
}

// projectionID gives a projected event the identity of the record it came from.
//
// It is derived from the task and the ledger sequence rather than generated, so
// replaying the same ledger produces the same identities. An event identity that
// changed between the live stream and a replay would make the two impossible to
// reconcile, which is the whole point of keeping one ledger.
func projectionID(taskID string, event tasks.TaskEvent) string {
	if event.Sequence == 0 {
		return taskID + "#" + event.Type
	}
	return taskID + "#" + strconv.FormatUint(event.Sequence, 10)
}

// taskEventFromEvent renders an agent event as a durable task event.
//
// The topic becomes the event type verbatim. That is what makes an agent's
// findings readable in the existing replay without the replay knowing anything
// about agents: it is one more typed entry in the same ordered ledger.
func taskEventFromEvent(event agentcontract.Event) (tasks.TaskEvent, bool) {
	if event.TaskID == "" || event.Topic == "" {
		// Runtime-wide events such as agent.registered have no task to attach
		// to. The ledger is per task, so they are broadcast and not persisted
		// rather than being filed under a task they do not describe.
		return tasks.TaskEvent{}, false
	}
	payload := clonePayload(event.Payload)
	if len(payload) == 0 {
		payload = nil
	}
	if payload != nil {
		payload["agent"] = event.Agent
		if event.AgentVersion != "" {
			payload["agentVersion"] = event.AgentVersion
		}
		if event.CausationID != "" {
			payload["causationId"] = event.CausationID
		}
		if event.CorrelationID != "" {
			payload["correlationId"] = event.CorrelationID
		}
	}
	return tasks.TaskEvent{
		Type: event.Topic, StepID: event.StepID, Payload: payload, OccurredAt: event.OccurredAt,
	}, true
}

func clonePayload(payload map[string]any) map[string]any {
	if len(payload) == 0 {
		return map[string]any{}
	}
	cloned := make(map[string]any, len(payload))
	for key, value := range payload {
		cloned[key] = value
	}
	return cloned
}

func payloadUint(value any) (uint64, bool) {
	switch typed := value.(type) {
	case uint64:
		return typed, true
	case int:
		if typed >= 0 {
			return uint64(typed), true
		}
	case int64:
		if typed >= 0 {
			return uint64(typed), true
		}
	case float64:
		if typed >= 0 {
			return uint64(typed), true
		}
	}
	return 0, false
}

func formatUint(value uint64) string {
	return strconv.FormatUint(value, 10)
}
