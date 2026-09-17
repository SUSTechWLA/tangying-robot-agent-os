package tasks

import (
	"sort"
	"strings"
	"time"
)

// Agent alert projection.
//
// The console needs one thing the raw event log cannot give it: which findings
// are still true right now. An event log only says what was reported; a reader
// looking at a banner needs "the robot is still stopped", not "something was
// reported forty minutes ago".
//
// So alerts are derived from the current state of the tasks, not accumulated from
// the event stream. A finding whose condition has cleared disappears on its own —
// there is no separate "dismiss" that someone has to remember to send, and no
// possibility of a banner that outlives the problem it describes.
//
// This is why an alert has an `Active` flag rather than being present-or-absent:
// the console can show "resolved" briefly, which is how an operator learns that
// the thing they saw is now fixed instead of wondering whether the banner was
// ever real.

// AlertSeverity ranks an alert. It mirrors what the publisher puts in the event,
// and is kept as strings here so this package does not depend on the agent layer.
const (
	AlertSeverityCritical = "critical"
	AlertSeverityWarning  = "warning"
	AlertSeverityInfo     = "info"
)

// AgentAlert is one finding, as the console needs it.
type AgentAlert struct {
	// ID is stable for a given condition, so the console can tell a repeat of
	// the same problem from a new one and keep an operator's acknowledgement.
	ID string `json:"id"`
	// TaskID is the task the alert is about.
	TaskID string `json:"taskId,omitempty"`
	// Agent is the agent that reported it.
	Agent    string `json:"agent,omitempty"`
	Topic    string `json:"topic"`
	Code     string `json:"code,omitempty"`
	Severity string `json:"severity,omitempty"`
	Message  string `json:"message,omitempty"`
	StepID   string `json:"stepId,omitempty"`
	// DetectedAt is when the condition was reported.
	DetectedAt time.Time `json:"detectedAt"`
	// Active is false once the condition it describes has cleared. A resolved
	// alert is kept briefly rather than deleted, because "it is fixed now" is
	// information and a silent disappearance is not.
	Active                  bool     `json:"active"`
	RecommendedActions      []string `json:"recommendedActions,omitempty"`
	AutomaticRetryForbidden bool     `json:"automaticRetryForbidden,omitempty"`
	MissingEvidence         []string `json:"missingEvidence,omitempty"`
	EvidenceIDs             []string `json:"evidenceIds,omitempty"`
}

// AlertInput is what the projection needs to decide whether a finding is still
// true.
type AlertInput struct {
	// Tasks carries the durable ledger and the current state of each task.
	Tasks []*Task
	// Reconciliation, keyed by task id, says whether that task still has a
	// physical step whose outcome is unknown. It is the one condition that cannot
	// be read off the task row: it lives in the execution records.
	Reconciliation map[string]bool
}

// ProjectAgentAlerts returns the current agent findings, newest first.
//
// A finding is active while its task still shows the condition:
//
//   - an unknown-outcome finding is active while the task still requires
//     reconciliation, and resolves when the world has been reconciled;
//   - every other finding is active while the task it was reported against has
//     not reached a successful terminal state, because the failure it names has
//     not been shown to be over.
//
// That second rule is deliberately conservative. A rule cannot always tell
// whether an operator-assist fault has been physically fixed, and claiming
// "resolved" without evidence is the same mistake as claiming a task complete
// without evidence. Resolving only on a recorded success is the honest version.
func ProjectAgentAlerts(input AlertInput) []AgentAlert {
	alerts := make([]AgentAlert, 0)
	for _, task := range input.Tasks {
		if task == nil {
			continue
		}
		successful := task.State == "SUCCEEDED"
		// Three states, not two. A map lookup collapses "reconciliation is
		// known to be unnecessary" and "nothing could tell us" into the same
		// false, and the second must not resolve a physical unknown: claiming a
		// world state we could not read is the same mistake as claiming a task
		// complete without evidence.
		reconciling, reconciliationKnown := input.Reconciliation[task.ID]
		for index := range task.Events {
			event := &task.Events[index]
			alert, ok := alertFromEvent(task.ID, event)
			if !ok {
				continue
			}
			alert.Active = alertActive(alert.Code, successful, reconciling, reconciliationKnown)
			alerts = append(alerts, alert)
		}
	}
	sort.SliceStable(alerts, func(i, j int) bool {
		// Active first, then severity, then newest: the banner leads with what
		// still needs attention.
		if alerts[i].Active != alerts[j].Active {
			return alerts[i].Active
		}
		left, right := alertSeverityRank(alerts[i].Severity), alertSeverityRank(alerts[j].Severity)
		if left != right {
			return left > right
		}
		return alerts[i].DetectedAt.After(alerts[j].DetectedAt)
	})
	return alerts
}

// alertActive decides whether a finding still describes the world.
func alertActive(code string, successful, reconciling, reconciliationKnown bool) bool {
	switch code {
	case "ANOMALY_UNVERIFIED_MUTATION":
		// The precise question this finding asks is "does anything still need
		// reconciling", and the execution records answer it exactly — when they
		// can be read. When they cannot, the alert stays up: an unreadable world
		// is not a resolved one.
		if !reconciliationKnown {
			return true
		}
		return reconciling
	default:
		return !successful
	}
}

// alertFromEvent lifts an alert out of one ledger entry.
//
// Only agent-authored findings become alerts. A tool activity or a state change
// is execution, and the console already renders those elsewhere; surfacing them
// as alerts would train an operator to dismiss the banner.
func alertFromEvent(taskID string, event *TaskEvent) (AgentAlert, bool) {
	if event == nil || event.Payload == nil {
		return AgentAlert{}, false
	}
	agent, _ := event.Payload["agent"].(string)
	if agent == "" {
		return AgentAlert{}, false
	}
	severity, _ := event.Payload["severity"].(string)
	// A health or lifecycle event is not a finding. It has no severity, and
	// treating it as an alert would put "an agent started" in a warning banner.
	if severity == "" && !isFindingTopic(event.Type) {
		return AgentAlert{}, false
	}
	code, _ := event.Payload["code"].(string)
	message, _ := event.Payload["message"].(string)
	if message == "" {
		message, _ = event.Payload["summary"].(string)
	}
	id, _ := event.Payload["anomalyId"].(string)
	if id == "" {
		id, _ = event.Payload["hypothesisId"].(string)
	}
	if id == "" {
		id = event.Type + "@" + code
	}
	alert := AgentAlert{
		ID: id, TaskID: taskID, Agent: agent, Topic: event.Type,
		Code: code, Severity: severity, Message: message, StepID: event.StepID,
		DetectedAt:              event.OccurredAt,
		RecommendedActions:      eventStrings(event.Payload["recommendedActions"]),
		AutomaticRetryForbidden: payloadBool(event.Payload["automaticRetryForbidden"]),
		MissingEvidence:         eventStrings(event.Payload["missingEvidence"]),
		EvidenceIDs:             eventStrings(event.Payload["evidence"]),
	}
	if alert.Code == "" {
		alert.Code, _ = event.Payload["category"].(string)
	}
	return alert, true
}

// isFindingTopic reports whether a topic is an agent making a claim about the
// system, as opposed to reporting its own state.
func isFindingTopic(topic string) bool {
	switch topic {
	case "ops.anomaly_detected", "ops.root_cause_hypothesis", "ops.escalation_required":
		return true
	default:
		return false
	}
}

func payloadBool(value any) bool {
	switch typed := value.(type) {
	case bool:
		return typed
	case string:
		return typed == "true"
	default:
		return false
	}
}

func alertSeverityRank(severity string) int {
	switch strings.ToLower(severity) {
	case AlertSeverityCritical:
		return 3
	case AlertSeverityWarning:
		return 2
	default:
		return 1
	}
}

// SupervisionStatus says whether anything is watching the robot.
//
// It exists because supervision can be switched off by configuration, and a
// deployment with the observer disabled looks exactly like a deployment where
// nothing is wrong: no alerts, no errors, no difference on screen. Reporting the
// enabled set lets the console say "nothing is watching" out loud, which is the
// only way an operator can tell the two apart.
type SupervisionStatus struct {
	// Enabled is false when the agent runtime was never started, or was started
	// with no agents.
	Enabled bool `json:"enabled"`
	// Agents are the agents currently running, by name.
	Agents []string `json:"agents,omitempty"`
	// Observing is true when at least one read-only observing agent is running.
	// It is the flag that decides whether silence means "healthy" or "unwatched".
	Observing bool `json:"observing"`
	// Reason explains an absent or partial supervision, for the operator.
	Reason string `json:"reason,omitempty"`
}

// SupervisionOpaque reports a status for a deployment that did not start the
// agent runtime.
func SupervisionOpaque() SupervisionStatus {
	return SupervisionStatus{
		Enabled: false,
		Reason:  "agent runtime is not running in this deployment",
	}
}
