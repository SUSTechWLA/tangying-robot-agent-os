package agentcontract

import (
	"strings"
	"time"
)

// Event is one thing that happened, as seen by the agent runtime.
//
// The same value travels two paths, and that is the point: it is published to
// subscribers for live reaction, and it is persisted to the durable task event
// stream so the existing task replay shows what the agents did. Persistence and
// routing share one representation, so a replay can never disagree with what a
// live subscriber saw.
type Event struct {
	// ID is the event identity. For events projected from the existing task
	// event stream this is derived from the task id and sequence, so a replayed
	// event keeps the identity it had when it was first recorded.
	ID string `json:"id"`
	// Topic is one of the Topic constants below.
	Topic string `json:"topic"`
	// TaskID is empty for runtime-wide events such as agent registration.
	// Events without a task id are broadcast and not persisted, because the
	// durable stream is per task.
	TaskID string `json:"taskId,omitempty"`
	// TaskRevision is the execution revision, when known.
	TaskRevision uint64 `json:"taskRevision,omitempty"`
	StepID       string `json:"stepId,omitempty"`
	// Agent is the publishing agent's Name.
	Agent string `json:"agent,omitempty"`
	// AgentVersion lets a behaviour change be correlated with the events it
	// produced.
	AgentVersion string `json:"agentVersion,omitempty"`
	// Priority orders delivery when several events are pending for one agent.
	Priority Priority `json:"priority,omitempty"`
	// CausationID is the ID of the event that directly caused this one.
	CausationID string `json:"causationId,omitempty"`
	// CorrelationID groups events belonging to one task attempt across agents.
	CorrelationID string `json:"correlationId,omitempty"`
	// OccurredAt is when the fact happened, not when it was published.
	OccurredAt time.Time      `json:"occurredAt"`
	Payload    map[string]any `json:"payload,omitempty"`
}

// Priority orders events when the runtime has to choose. It is deliberately
// small: a priority scheme with many levels invites arguments no one can settle.
type Priority int

const (
	// PriorityLow is for telemetry-shaped events that only enrich a record.
	PriorityLow Priority = iota
	// PriorityNormal is the default.
	PriorityNormal
	// PriorityHigh is for events about a failure or an unknown outcome.
	PriorityHigh
	// PriorityCritical is for events that may require stopping physical work.
	PriorityCritical
)

func (p Priority) String() string {
	switch p {
	case PriorityLow:
		return "low"
	case PriorityHigh:
		return "high"
	case PriorityCritical:
		return "critical"
	default:
		return "normal"
	}
}

// Event topics. The vocabulary is fixed here so a subscriber can name what it
// wants and a publisher cannot invent a topic that nobody listens to.
const (
	// TopicTaskStarted is published when a task begins executing.
	TopicTaskStarted = "task.started"
	// TopicTaskCompleted is published when a task reaches a successful
	// terminal state. It means the closed loop was satisfied, not that a tool
	// returned success.
	TopicTaskCompleted = "task.completed"
	// TopicTaskFailed is published when a task reaches an unsuccessful
	// terminal state, including a state that still permits a human-approved
	// reconciliation.
	TopicTaskFailed = "task.failed"
	// TopicActionExecuted is published for each tool dispatch and its
	// transition (SENDING, RUNNING, CONFIRMED, FAILED).
	TopicActionExecuted = "action.executed"
	// TopicEvidenceCollected is published when post-condition evidence is
	// archived for a step.
	TopicEvidenceCollected = "evidence.collected"
	// TopicStateTransition is published for every task lifecycle transition.
	TopicStateTransition = "state.transition"

	// TopicOpsAnomalyDetected is published by an observing agent that found
	// something wrong.
	TopicOpsAnomalyDetected = "ops.anomaly_detected"
	// TopicOpsRootCauseHypothesis is published by an observing agent that has a
	// structured, evidence-backed explanation.
	TopicOpsRootCauseHypothesis = "ops.root_cause_hypothesis"
	// TopicOpsRecoveryProposed is published by an observing agent that wants a
	// recovery action considered. It is a request, never an action.
	TopicOpsRecoveryProposed = "ops.recovery_proposed"
	// TopicOpsEscalationRequired is published when the situation needs a human.
	TopicOpsEscalationRequired = "ops.escalation_required"
	// TopicOpsRecoveryDeferred is published when the runtime refused to act on
	// a recovery proposal now, and says why. A deferred proposal is not a
	// rejected one; it is revisited at the next safe point.
	TopicOpsRecoveryDeferred = "ops.recovery_deferred"

	// TopicAgentRegistered is published once per agent at startup.
	TopicAgentRegistered = "agent.registered"
	// TopicAgentHealthChanged is published when an agent's health actually
	// changes, never on every check.
	TopicAgentHealthChanged = "agent.health_changed"
	// TopicAgentPermissionDenied is published when the runtime refused an
	// agent's request because it exceeded the agent's declared permissions.
	TopicAgentPermissionDenied = "agent.permission_denied"
)

// TopicOpsAll is the wildcard an observer subscribes to when it wants the whole
// ops namespace.
const TopicOpsAll = "ops.*"

// TopicAgentAll is the wildcard for runtime lifecycle events.
const TopicAgentAll = "agent.*"

// TopicTaskAll is the wildcard for task lifecycle events.
const TopicTaskAll = "task.*"

// TopicActionAll is the wildcard for tool dispatch transitions.
const TopicActionAll = "action.*"

// TopicEvidenceAll is the wildcard for archived-evidence events.
const TopicEvidenceAll = "evidence.*"

// TopicStateAll is the wildcard for task lifecycle state changes.
const TopicStateAll = "state.*"

// NamespacePrefixes are the namespaces a wildcard subscription may name, with
// the trailing dot included. Keeping the list here makes "every declared topic
// belongs to a namespace" a property this package can be held to by its own
// tests, rather than one that quietly stops holding when a topic is added in a
// new namespace.
var NamespacePrefixes = []string{"task.", "action.", "evidence.", "state.", "ops.", "agent."}

// Matches reports whether event topic `topic` is selected by subscription
// pattern `pattern`. A pattern is either an exact topic or a namespace wildcard
// such as "ops.*", which matches "ops.anomaly_detected" but not bare "ops" and
// not a different namespace that merely starts with the same letters.
//
// Matching is deliberately not full glob: a subscription language richer than
// this has never been needed here, and every extra form is a thing to get wrong.
func Matches(pattern, topic string) bool {
	if pattern == "" || topic == "" {
		return false
	}
	if pattern == topic {
		return true
	}
	prefix, ok := strings.CutSuffix(pattern, "*")
	if !ok {
		return false
	}
	// "ops.*" becomes prefix "ops.": the dot is kept so "opsx.y" cannot match.
	return strings.HasPrefix(topic, prefix) && len(topic) > len(prefix)
}

// MatchesAny reports whether any pattern in patterns selects topic.
func MatchesAny(patterns []string, topic string) bool {
	for _, pattern := range patterns {
		if Matches(pattern, topic) {
			return true
		}
	}
	return false
}

// KnownTopic reports whether topic is part of the fixed vocabulary. The runtime
// uses it to refuse a subscription to a topic nothing can publish, which turns
// a typo in an agent's Subscriptions into a startup error instead of silence.
func KnownTopic(topic string) bool {
	switch topic {
	case TopicTaskStarted, TopicTaskCompleted, TopicTaskFailed,
		TopicActionExecuted, TopicEvidenceCollected, TopicStateTransition,
		TopicOpsAnomalyDetected, TopicOpsRootCauseHypothesis,
		TopicOpsRecoveryProposed, TopicOpsEscalationRequired, TopicOpsRecoveryDeferred,
		TopicAgentRegistered, TopicAgentHealthChanged, TopicAgentPermissionDenied:
		return true
	default:
		return false
	}
}

// KnownPattern reports whether pattern is either a known topic or a wildcard
// over a known namespace.
func KnownPattern(pattern string) bool {
	if KnownTopic(pattern) {
		return true
	}
	prefix, ok := strings.CutSuffix(pattern, "*")
	if !ok {
		return false
	}
	for _, namespace := range NamespacePrefixes {
		if prefix == namespace {
			return true
		}
	}
	return false
}

// NamespaceOf returns the namespace prefix of a topic ("task." for
// "task.started"), or the empty string when the topic is not in the vocabulary.
// Every declared topic must have one: a topic in no namespace can only be
// subscribed to by its exact name, which means an observer written against the
// namespace silently never hears it.
func NamespaceOf(topic string) string {
	if !KnownTopic(topic) {
		return ""
	}
	for _, namespace := range NamespacePrefixes {
		if strings.HasPrefix(topic, namespace) {
			return namespace
		}
	}
	return ""
}
