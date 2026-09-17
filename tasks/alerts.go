package tasks

import (
	"sort"
	"strings"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
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

	// Recovery is the plan proposed for this alert, when one exists.
	//
	// It is attached to the alert rather than kept in a separate list so an
	// operator sees the problem and the proposal together. A plan presented apart
	// from the finding it answers makes the reader do the joining.
	Recovery *RecoveryView `json:"recovery,omitempty"`
	// Investigation is the recorded road to the plan: what was read, from which
	// store and table, what came back, which rules ran, whether a model was asked.
	//
	// It is attached here because a conclusion whose road is not shown cannot be
	// reviewed, and an operator being asked to approve an action is entitled to
	// the reasoning behind it.
	Investigation *InvestigationView `json:"investigation,omitempty"`
}

// RecoveryView is a proposed recovery plan, as a console shows it.
type RecoveryView struct {
	PlanID  string `json:"planId"`
	Verdict string `json:"verdict"`
	// Diagnosis is what the proposer believes is wrong.
	Diagnosis string `json:"diagnosis,omitempty"`
	// Confidence in [0,1]; never omitted, because a low-confidence plan should be
	// read differently from a certain one.
	Confidence float64 `json:"confidence"`
	// Source says who proposed it: "deterministic" or "deterministic+model". A
	// reader must be able to tell a table lookup from a model's reasoning.
	Source string `json:"source"`
	// Steps are the proposed actions in order.
	Steps []RecoveryStepView `json:"steps"`
	// EscalateReason is set when the verdict is ESCALATE: what a person must do.
	EscalateReason string `json:"escalateReason,omitempty"`
	// Refused lists actions the catalog forbids, with the catalog's own reason.
	Refused []RefusalView `json:"refused,omitempty"`
}

// RecoveryStepView is one proposed action.
type RecoveryStepView struct {
	Order            int    `json:"order"`
	Action           string `json:"action"`
	Summary          string `json:"summary,omitempty"`
	Risk             string `json:"risk,omitempty"`
	RequiresApproval bool   `json:"requiresApproval"`
	Why              string `json:"why,omitempty"`
}

// RefusalView is one action the system will not perform, and why.
type RefusalView struct {
	Action  string `json:"action"`
	Summary string `json:"summary,omitempty"`
	Reason  string `json:"reason"`
}

// InvestigationView is the investigation behind a plan.
type InvestigationView struct {
	TrailID   string                  `json:"trailId,omitempty"`
	Trigger   string                  `json:"trigger,omitempty"`
	StepCount int                     `json:"stepCount"`
	Steps     []InvestigationStepView `json:"steps"`
	// CatalogSize and NeverAutomatic describe what the proposer was allowed to
	// choose from, so a plan can be reviewed against the options that existed.
	CatalogSize    int `json:"catalogSize,omitempty"`
	NeverAutomatic int `json:"neverAutomatic,omitempty"`
}

// InvestigationStepView is one recorded step.
type InvestigationStepView struct {
	Sequence int    `json:"sequence"`
	Kind     string `json:"kind"`
	Name     string `json:"name"`
	Summary  string `json:"summary"`
	// Source is the readable rendering, for example "sqlite:step_runs (task_id = X)".
	Source string `json:"source,omitempty"`
	// SourceDetail lets a console show the parts separately: an operator asked
	// "which database, which table" should not have to parse a string.
	SourceDetail map[string]any `json:"sourceDetail,omitempty"`
	Findings     map[string]any `json:"findings,omitempty"`
	Rows         int            `json:"rows,omitempty"`
	Error        string         `json:"error,omitempty"`
	At           time.Time      `json:"at"`
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
	// One row per finding, not one per report of it.
	//
	// A standing condition is reported again on every evaluation — that is what
	// "standing" means, and the report cooldown bounds how often. The banner is a
	// projection of what is true now, so the newest report of a finding replaces
	// the earlier ones instead of being listed beside them. Without this the same
	// component failure appeared three times in one task, and the badge counted
	// reports rather than problems, which is how a warning list stops being read.
	byIdentity := map[string]int{}
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
			// The task is part of the key: the same component fails the same way
			// in every task, and those are separate findings about separate work.
			key := task.ID + "\x00" + alert.ID
			if previous, seen := byIdentity[key]; seen {
				// The ledger is append-only, so the reports arrive in order and
				// the later one is the newer evidence. Replacing in place keeps
				// the projection's order stable.
				alerts[previous] = alert
				continue
			}
			byIdentity[key] = len(alerts)
			alerts = append(alerts, alert)
		}
	}
	attachRecovery(alerts, input.Tasks)
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

// attachRecovery links each alert to the plan proposed for it and to the
// investigation behind that plan.
//
// The link is made here, in the projection, rather than by the publisher: a
// console should not need to correlate two event streams to show one problem
// with its answer, and a reader should not have to work out which plan belongs
// to which finding.
//
// A plan is matched to an alert by the identity of the finding it answers, and
// within the task it was recorded against.
//
// Both halves matter. Matching by the triggering code alone put one component's
// plan under every other component's finding, and ignoring the task put a plan
// made about one task under an alert about another: the plan ids in the console
// named a task the alert was not about. A plan is an answer to one finding on one
// task, so anything broader than that is a plan shown against a problem it was
// never about.
func attachRecovery(alerts []AgentAlert, tasks []*Task) {
	type planEntry struct {
		view          RecoveryView
		investigation InvestigationView
		at            time.Time
	}
	byTask := map[string]map[string]planEntry{}
	for _, task := range tasks {
		if task == nil {
			continue
		}
		for index := range task.Events {
			event := &task.Events[index]
			if event.Type != "ops.recovery_plan" || event.Payload == nil {
				continue
			}
			view, investigation, ok := recoveryFromPayload(event.Payload)
			if !ok {
				continue
			}
			plans := byTask[task.ID]
			if plans == nil {
				plans = map[string]planEntry{}
				byTask[task.ID] = plans
			}
			trigger, _ := event.Payload["trigger"].(string)
			if existing, seen := plans[trigger]; seen && existing.at.After(event.OccurredAt) {
				continue
			}
			plans[trigger] = planEntry{view: view, investigation: investigation, at: event.OccurredAt}
		}
	}
	if len(byTask) == 0 {
		return
	}
	for index := range alerts {
		plans := byTask[alerts[index].TaskID]
		if len(plans) == 0 {
			continue
		}
		entry, ok := planForAlert(plans, alerts[index])
		if !ok {
			continue
		}
		view := entry.view
		investigation := entry.investigation
		alerts[index].Recovery = &view
		alerts[index].Investigation = &investigation
	}
}

// planForAlert finds the plan filed for one alert.
//
// It walks the stored plans rather than indexing them, because the plan's
// identity and the alert's id can differ by the count suffix. The list is bounded
// by the number of distinct findings in one task's replay, so the scan costs
// nothing worth a second index that could disagree with the first.
//
// A plan whose trigger is not an identity at all is deliberately not matched.
// Plans written before identities carried the component are still in the ledger —
// it is an append-only record, so they cannot be removed — and matching one by its
// bare code is exactly the confusion that put one component's plan under another
// component's finding. An alert with no plan of its own shows none, which is true,
// rather than borrowing a plan that was made about something else.
func planForAlert[T any](latest map[string]T, alert AgentAlert) (T, bool) {
	for identity, entry := range latest {
		if agentcontract.SameAnomaly(alert.ID, identity) {
			return entry, true
		}
	}
	var zero T
	return zero, false
}

// recoveryFromPayload reads a plan event into the shapes a console renders.
//
// The payload arrives as JSON-keyed maps, so every field is read defensively: a
// malformed payload should degrade to a partial rendering, never to a panic and
// never to a silently empty panel that reads like "no plan was made".
func recoveryFromPayload(payload map[string]any) (RecoveryView, InvestigationView, bool) {
	verdict, _ := payload["verdict"].(string)
	if verdict == "" {
		return RecoveryView{}, InvestigationView{}, false
	}
	view := RecoveryView{
		PlanID:         stringValue(payload, "planId"),
		Verdict:        verdict,
		Diagnosis:      stringValue(payload, "diagnosis"),
		Confidence:     floatValue(payload, "confidence"),
		Source:         stringValue(payload, "source"),
		EscalateReason: stringValue(payload, "escalateReason"),
	}
	for _, raw := range mapList(payload["steps"]) {
		view.Steps = append(view.Steps, RecoveryStepView{
			Order:            int(floatValue(raw, "order")),
			Action:           stringValue(raw, "action"),
			Summary:          stringValue(raw, "summary"),
			Risk:             stringValue(raw, "risk"),
			RequiresApproval: boolValue(raw, "requiresApproval"),
			Why:              stringValue(raw, "why"),
		})
	}

	investigation := InvestigationView{StepCount: int(floatValue(payload, "stepCount"))}
	if trail, ok := payload["trail"].(map[string]any); ok {
		investigation.TrailID = stringValue(trail, "trailId")
		investigation.Trigger = stringValue(trail, "trigger")
		if count, ok := trail["stepCount"].(float64); ok {
			investigation.StepCount = int(count)
		}
		for _, raw := range mapList(trail["steps"]) {
			step := InvestigationStepView{
				Sequence: int(floatValue(raw, "sequence")),
				Kind:     stringValue(raw, "kind"),
				Name:     stringValue(raw, "name"),
				Summary:  stringValue(raw, "summary"),
				Source:   stringValue(raw, "source"),
				Rows:     int(floatValue(raw, "rows")),
				Error:    stringValue(raw, "error"),
			}
			if detail, ok := raw["sourceDetail"].(map[string]any); ok {
				step.SourceDetail = detail
			}
			if findings, ok := raw["findings"].(map[string]any); ok {
				step.Findings = findings
			}
			if at, ok := raw["at"].(time.Time); ok {
				step.At = at
			}
			investigation.Steps = append(investigation.Steps, step)
		}
	}

	// The refused actions are what makes the boundary visible: a reader should see
	// that a never-automatic action exists and why it was refused, rather than
	// having to infer the limit from the absence of a step.
	if catalog, ok := payload["catalog"].(map[string]any); ok {
		actions := mapList(catalog["actions"])
		investigation.CatalogSize = len(actions)
		for _, action := range actions {
			reason := stringValue(action, "refusal")
			if reason == "" {
				continue
			}
			investigation.NeverAutomatic++
			view.Refused = append(view.Refused, RefusalView{
				Action:  stringValue(action, "id"),
				Summary: stringValue(action, "summary"),
				Reason:  reason,
			})
		}
	}
	return view, investigation, true
}

func mapList(value any) []map[string]any {
	items, ok := value.([]any)
	if !ok {
		return nil
	}
	result := make([]map[string]any, 0, len(items))
	for _, item := range items {
		if entry, ok := item.(map[string]any); ok {
			result = append(result, entry)
		}
	}
	return result
}

func stringValue(values map[string]any, key string) string {
	text, _ := values[key].(string)
	return text
}

func floatValue(values map[string]any, key string) float64 {
	switch typed := values[key].(type) {
	case float64:
		return typed
	case int:
		return float64(typed)
	case int64:
		return float64(typed)
	default:
		return 0
	}
}

func boolValue(values map[string]any, key string) bool {
	value, _ := values[key].(bool)
	return value
}
