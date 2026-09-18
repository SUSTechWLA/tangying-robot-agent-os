package agentcontract

import (
	"time"
	"unicode/utf8"
)

// Payload schemas. These are declared as types rather than written as ad-hoc
// maps at each call site for the same reason the rest of this repository
// declares its contracts: a payload shape that exists only in the head of the
// publisher drifts, and a consumer that has to defend against every field being
// absent ends up ignoring the event entirely.
//
// Payload values are attached to Event.Payload through Encode. Encode produces
// map[string]any rather than a struct because the payload must survive the trip
// through the existing task event stream, which is JSON-keyed.

// AnomalyPayload is the body of ops.anomaly_detected.
//
// Severity is the observer's own scale, not the robot's fault severity: the
// robot's vocabulary (info/degraded/blocked/safety) is preserved verbatim in
// Component and Code, and this field says how much it should interrupt a human.
type AnomalyPayload struct {
	// AnomalyID is stable for a given (component, code) so a repeat report can
	// be recognised as the same problem rather than a new one. This matters:
	// an anomaly re-reported every telemetry tick would otherwise page someone
	// forever, which is the failure mode the fault ledger already had to fix.
	AnomalyID string `json:"anomalyId"`
	// Severity is one of info, warning, critical.
	Severity string `json:"severity"`
	// Component names what is wrong: a module id, a task id, a step id or a
	// capability name.
	Component string `json:"component"`
	// Code is the stable machine-readable code for this anomaly.
	Code string `json:"code"`
	// Message is a short human sentence. It must not be the only carrier of
	// machine-relevant facts.
	Message string `json:"message,omitempty"`
	// Evidence lists observation ids and event ids supporting the finding.
	Evidence []string `json:"evidence,omitempty"`
	// Facts carries the bounded supporting numbers, such as age in
	// milliseconds. Values must be JSON scalars.
	Facts map[string]any `json:"facts,omitempty"`
	// DetectedAt is when the observer decided this, not when the underlying
	// condition started.
	DetectedAt time.Time `json:"detectedAt"`
}

// HypothesisPayload is the body of ops.root_cause_hypothesis.
//
// Category is always one of the closed-loop failure classes. Reusing that
// vocabulary is deliberate: the repository already decided that a failure has
// exactly one safe recovery action, and an observer that invented a second
// taxonomy would make the two disagree.
type HypothesisPayload struct {
	HypothesisID string `json:"hypothesisId"`
	// Category is a closedloop.Class value. Declared as a string here so this
	// package stays free of that dependency; the producer is responsible for
	// using the real vocabulary.
	Category string `json:"category"`
	// Confidence is in [0,1]. A consumer must be able to treat low confidence
	// as "ask a human", so it is never omitted.
	Confidence float64 `json:"confidence"`
	// Summary is one human sentence.
	Summary string `json:"summary,omitempty"`
	// EvidenceChain is the ordered list of facts this rests on. An empty chain
	// is not allowed: an explanation with no evidence is a guess.
	EvidenceChain []string `json:"evidenceChain"`
	// AffectedComponents lists what this would explain.
	AffectedComponents []string `json:"affectedComponents,omitempty"`
	// RecommendedActions are human actions, in the order they should be tried.
	RecommendedActions []string `json:"recommendedActions,omitempty"`
	// AutomaticRetryForbidden is true when the underlying failure class is an
	// unknown physical outcome. It is carried explicitly because it is the one
	// piece of advice that must never be softened by a downstream consumer.
	AutomaticRetryForbidden bool `json:"automaticRetryForbidden"`
	// MissingEvidence names what could not be established. When an observer
	// cannot classify something, saying what evidence is missing is the honest
	// answer and the instruction for what to collect next.
	MissingEvidence []string `json:"missingEvidence,omitempty"`
	// ProposedAt is when the hypothesis was formed.
	ProposedAt time.Time `json:"proposedAt"`
}

// RecoveryProposalPayload is the body of ops.recovery_proposed.
//
// A proposal is a request for a decision. It is not an action, and nothing in
// this system executes a proposal directly: the existing permission system,
// approval path and reconciliation rules still govern whatever is eventually
// done.
type RecoveryProposalPayload struct {
	ProposalID string `json:"proposalId"`
	// Action is the proposed action, named in the operator's vocabulary.
	Action string `json:"action"`
	// AutomationLevel is how much automation is being asked for. The first
	// version of every observing agent uses "advisory" only, which means
	// "tell a human what to do" and nothing else.
	AutomationLevel string `json:"automationLevel"`
	// RequiresApproval must be true for anything that changes the world or task
	// state. It is a field rather than an assumption so that a proposal which
	// claims otherwise is visible in the record and can be refused.
	RequiresApproval bool `json:"requiresApproval"`
	// Rationale is why, in one sentence.
	Rationale string `json:"rationale,omitempty"`
	// HypothesisID links back to the explanation that motivated this.
	HypothesisID string `json:"hypothesisId,omitempty"`
	// DeferredReason is set by the runtime, not the proposer, when the proposal
	// was not routed for a decision now. It names the concrete blocker.
	DeferredReason string `json:"deferredReason,omitempty"`
}

// EscalationPayload is the body of ops.escalation_required.
type EscalationPayload struct {
	EscalationID string `json:"escalationId"`
	// Reason is the stable machine-readable reason code.
	Reason string `json:"reason"`
	// Context carries the facts a human needs to act: task id, step id,
	// evidence ids, and what has already been tried.
	Context map[string]any `json:"context,omitempty"`
	// Urgency is one of routine, soon, immediate.
	Urgency string `json:"urgency,omitempty"`
	// RaisedAt is when the escalation was raised.
	RaisedAt time.Time `json:"raisedAt"`
}

// RegisteredPayload is the body of agent.registered.
type RegisteredPayload struct {
	Version      string       `json:"version"`
	Capabilities []Capability `json:"capabilities,omitempty"`
	Permissions  Permission   `json:"permissions"`
	// EnabledAgents is the whole enabled set at startup, so a replay can tell
	// which agents were running when a task was executed.
	EnabledAgents []string `json:"enabledAgents,omitempty"`
}

// HealthChangedPayload is the body of agent.health_changed.
type HealthChangedPayload struct {
	Previous   HealthStatus `json:"previous"`
	Current    HealthStatus `json:"current"`
	ReasonCode string       `json:"reasonCode,omitempty"`
}

// PermissionDeniedPayload is the body of agent.permission_denied.
//
// This event exists because a refusal that leaves no trace is indistinguishable
// from an agent that never asked. Refusals are the most interesting thing an
// observing agent does, so they are recorded.
type PermissionDeniedPayload struct {
	// Capability is the capability that was requested.
	Capability string `json:"capability"`
	// ReasonCode is a stable code such as AGENT_READ_ONLY.
	ReasonCode string `json:"reasonCode"`
	// MutatesWorld and MutatesTaskState describe what was asked for.
	MutatesWorld     bool `json:"mutatesWorld"`
	MutatesTaskState bool `json:"mutatesTaskState"`
	// Detail is a short human explanation.
	Detail string `json:"detail,omitempty"`
}

// ActionPayload is the body of action.executed. It mirrors the existing
// TOOL_ACTIVITY payload rather than introducing a second description of the
// same fact, so a replay built from either path agrees.
type ActionPayload struct {
	ToolName         string         `json:"toolName"`
	ActivityStatus   string         `json:"activityStatus"`
	RobotID          string         `json:"robotId,omitempty"`
	StepID           string         `json:"stepId,omitempty"`
	CommandID        string         `json:"commandId,omitempty"`
	SafetyLevel      string         `json:"safetyLevel,omitempty"`
	FencingToken     uint64         `json:"fencingToken,omitempty"`
	TaskRevision     uint64         `json:"taskRevision,omitempty"`
	AggregateVersion uint64         `json:"aggregateVersion,omitempty"`
	Arguments        map[string]any `json:"arguments,omitempty"`
	EvidenceIDs      []string       `json:"evidenceIds,omitempty"`
	Error            string         `json:"error,omitempty"`
}

// EvidencePayload is the body of evidence.collected.
type EvidencePayload struct {
	StepID   string `json:"stepId,omitempty"`
	Source   string `json:"source,omitempty"`
	ToolName string `json:"toolName,omitempty"`
	// EvidenceIDs are archived captures. An empty list is meaningful: it says
	// the observation the runtime named was not persisted, which is exactly the
	// case a human must review rather than accept.
	EvidenceIDs []string `json:"evidenceIds,omitempty"`
	// ReceiptObservationID is the runtime's own id for the observation it took
	// after acting.
	ReceiptObservationID string `json:"receiptObservationId,omitempty"`
}

// StateTransitionPayload is the body of state.transition.
type StateTransitionPayload struct {
	From   string `json:"from,omitempty"`
	To     string `json:"to"`
	Reason string `json:"reason,omitempty"`
}

// TaskLifecyclePayload is the body of task.started, task.completed and
// task.failed.
type TaskLifecyclePayload struct {
	State   string   `json:"state"`
	Reason  string   `json:"reason,omitempty"`
	Steps   []string `json:"completedSteps,omitempty"`
	Attempt string   `json:"observationAttempt,omitempty"`
}

// Encode renders any payload struct as the JSON-keyed map the event stream
// carries. It uses explicit field mapping rather than reflection so that adding
// a struct field cannot silently change what is published.
func (p AnomalyPayload) Encode() map[string]any {
	payload := map[string]any{
		"anomalyId": p.AnomalyID, "severity": p.Severity, "component": p.Component,
		"code": p.Code, "detectedAt": p.DetectedAt,
	}
	putString(payload, "message", p.Message)
	putStrings(payload, "evidence", p.Evidence)
	putFacts(payload, p.Facts)
	return payload
}

func (p HypothesisPayload) Encode() map[string]any {
	payload := map[string]any{
		"hypothesisId": p.HypothesisID, "category": p.Category,
		"confidence": p.Confidence, "evidenceChain": append([]string{}, p.EvidenceChain...),
		"automaticRetryForbidden": p.AutomaticRetryForbidden, "proposedAt": p.ProposedAt,
	}
	putString(payload, "summary", p.Summary)
	putStrings(payload, "affectedComponents", p.AffectedComponents)
	putStrings(payload, "recommendedActions", p.RecommendedActions)
	putStrings(payload, "missingEvidence", p.MissingEvidence)
	return payload
}

func (p RecoveryProposalPayload) Encode() map[string]any {
	payload := map[string]any{
		"proposalId": p.ProposalID, "action": p.Action,
		"automationLevel": p.AutomationLevel, "requiresApproval": p.RequiresApproval,
	}
	putString(payload, "rationale", p.Rationale)
	putString(payload, "hypothesisId", p.HypothesisID)
	putString(payload, "deferredReason", p.DeferredReason)
	return payload
}

func (p EscalationPayload) Encode() map[string]any {
	payload := map[string]any{"escalationId": p.EscalationID, "reason": p.Reason, "raisedAt": p.RaisedAt}
	putString(payload, "urgency", p.Urgency)
	putFacts(payload, p.Context)
	return payload
}

func (p RegisteredPayload) Encode() map[string]any {
	payload := map[string]any{
		"version": p.Version, "permissions": encodePermission(p.Permissions),
	}
	if len(p.Capabilities) > 0 {
		capabilities := make([]any, 0, len(p.Capabilities))
		for _, capability := range p.Capabilities {
			capabilities = append(capabilities, string(capability))
		}
		payload["capabilities"] = capabilities
	}
	putStrings(payload, "enabledAgents", p.EnabledAgents)
	return payload
}

func (p HealthChangedPayload) Encode() map[string]any {
	payload := map[string]any{"previous": string(p.Previous), "current": string(p.Current)}
	putString(payload, "reasonCode", p.ReasonCode)
	return payload
}

func (p PermissionDeniedPayload) Encode() map[string]any {
	payload := map[string]any{
		"capability": p.Capability, "reasonCode": p.ReasonCode,
		"mutatesWorld": p.MutatesWorld, "mutatesTaskState": p.MutatesTaskState,
	}
	putString(payload, "detail", p.Detail)
	return payload
}

func (p ActionPayload) Encode() map[string]any {
	payload := map[string]any{
		"toolName": p.ToolName, "activityStatus": p.ActivityStatus,
	}
	putString(payload, "robotId", p.RobotID)
	putString(payload, "stepId", p.StepID)
	putString(payload, "commandId", p.CommandID)
	putString(payload, "safetyLevel", p.SafetyLevel)
	putString(payload, "error", p.Error)
	putUint(payload, "fencingToken", p.FencingToken)
	putUint(payload, "taskRevision", p.TaskRevision)
	putUint(payload, "aggregateVersion", p.AggregateVersion)
	if len(p.Arguments) > 0 {
		payload["arguments"] = cloneFacts(p.Arguments)
	}
	putStrings(payload, "evidenceIds", p.EvidenceIDs)
	return payload
}

func (p EvidencePayload) Encode() map[string]any {
	payload := map[string]any{}
	putString(payload, "stepId", p.StepID)
	putString(payload, "source", p.Source)
	putString(payload, "toolName", p.ToolName)
	putStrings(payload, "evidenceIds", p.EvidenceIDs)
	putString(payload, "receiptObservationId", p.ReceiptObservationID)
	return payload
}

func (p StateTransitionPayload) Encode() map[string]any {
	payload := map[string]any{"to": p.To}
	putString(payload, "from", p.From)
	putString(payload, "reason", p.Reason)
	return payload
}

func (p TaskLifecyclePayload) Encode() map[string]any {
	payload := map[string]any{"state": p.State}
	putString(payload, "reason", p.Reason)
	putStrings(payload, "completedSteps", p.Steps)
	putString(payload, "attempt", p.Attempt)
	return payload
}

// MaxPayloadString bounds one published string. An event is a record of a fact,
// not a channel for a stack trace: an unbounded field would bloat the durable
// stream and the console response that reads it.
const MaxPayloadString = 2048

// MaxFactEntries bounds a facts map.
const MaxFactEntries = 64

func encodePermission(permission Permission) map[string]any {
	return map[string]any{
		"readOnly": permission.ReadOnly, "mutatesWorld": permission.MutatesWorld,
		"mutatesTaskState": permission.MutatesTaskState, "requiresApproval": permission.RequiresApproval,
	}
}

func putString(payload map[string]any, key, value string) {
	if value == "" {
		return
	}
	payload[key] = truncate(value)
}

func putUint(payload map[string]any, key string, value uint64) {
	if value == 0 {
		return
	}
	payload[key] = value
}

func putStrings(payload map[string]any, key string, values []string) {
	if len(values) == 0 {
		return
	}
	copied := make([]any, 0, len(values))
	for _, value := range values {
		if value == "" {
			continue
		}
		copied = append(copied, truncate(value))
	}
	if len(copied) > 0 {
		payload[key] = copied
	}
}

func putFacts(payload map[string]any, facts map[string]any) {
	if cloned := cloneFacts(facts); cloned != nil {
		payload["facts"] = cloned
	}
}

// cloneFacts copies a facts map and keeps only bounded scalars. A nested
// structure is dropped rather than flattened: an event payload that can hold
// arbitrary depth is an event payload nobody can validate.
func cloneFacts(facts map[string]any) map[string]any {
	if len(facts) == 0 {
		return nil
	}
	cloned := make(map[string]any, len(facts))
	for key, value := range facts {
		if len(cloned) >= MaxFactEntries {
			break
		}
		switch typed := value.(type) {
		case nil:
			continue
		case string:
			cloned[key] = truncate(typed)
		case bool, float64, float32, int, int64, uint64, uint32, uint16, uint8, int32, int16, int8:
			cloned[key] = typed
		default:
			// Unsupported shapes are omitted; the publisher can flatten what it
			// actually needs into scalars.
			continue
		}
	}
	if len(cloned) == 0 {
		return nil
	}
	return cloned
}

// truncate bounds a string at a rune boundary so a multi-byte character is
// never cut in half and rendered as a replacement character in a replay.
func truncate(value string) string {
	if len(value) <= MaxPayloadString {
		return value
	}
	cut := MaxPayloadString
	for cut > 0 && !utf8.RuneStart(value[cut]) {
		cut--
	}
	return value[:cut]
}

// RecoveryVerdict is what a recovery agent concluded. It is carried explicitly
// because "I cannot fix this" is a first-class answer, not a failure: a system
// that only knows how to try things will try forever.
type RecoveryVerdict string

const (
	// VerdictPlan means a plan was produced. It may be empty of write actions.
	VerdictPlan RecoveryVerdict = "PLAN"
	// VerdictEscalate means recovery was considered and rejected as the wrong
	// move. RequiresReason is what the operator reads.
	VerdictEscalate RecoveryVerdict = "ESCALATE"
)

// RecoveryStep is one action in a recovery plan, as proposed.
type RecoveryStep struct {
	// Order is the position in the plan, starting at 1.
	Order int `json:"order"`
	// Action is the catalog id being proposed.
	Action string `json:"action"`
	// Summary is the catalog's one-line description, copied so the plan is
	// readable without a lookup.
	Summary string `json:"summary,omitempty"`
	// Risk is the catalog's risk class for the action.
	Risk string `json:"risk,omitempty"`
	// RequiresApproval is carried on the step rather than inferred by the reader:
	// a plan that needs consent should say so where it is read.
	RequiresApproval bool `json:"requiresApproval"`
	// Why is the proposer's reason for choosing this step.
	Why string `json:"why,omitempty"`
}

// RecoveryPlanPayload is the body of ops.recovery_plan.
//
// It carries the whole investigation, not just the conclusion. The requirement
// is that an operator can see how a judgement was reached: which stores were
// read, which tables, what came back, which rules ran, and where a model was
// consulted. A conclusion alone cannot be reviewed, so the trail travels with it.
type RecoveryPlanPayload struct {
	PlanID string `json:"planId"`
	// TaskID is the task this plan is about.
	//
	// It is carried because a plan is only meaningful against the world it was
	// formed from, and an executor needs to know which world that is. A
	// robot-level finding that named no task reads the task from its facts; when
	// even that is absent the plan is robot-level and this stays empty.
	TaskID  string          `json:"taskId,omitempty"`
	Verdict RecoveryVerdict `json:"verdict"`
	// Trigger names what started this, normally the anomaly code.
	Trigger string `json:"trigger,omitempty"`
	// Diagnosis is the one-line statement of what is believed wrong.
	Diagnosis string `json:"diagnosis,omitempty"`
	// Confidence in [0,1]; it is never omitted, because a reader must be able to
	// treat a low-confidence plan differently.
	Confidence float64 `json:"confidence"`
	// Steps are the proposed actions in order.
	Steps []RecoveryStep `json:"steps"`
	// EscalateReason is set when the verdict is ESCALATE: what a person has to do.
	EscalateReason string `json:"escalateReason,omitempty"`
	// Trail is the investigation, step by step.
	Trail map[string]any `json:"trail,omitempty"`
	// Catalog lists what the proposer was allowed to choose from, so a plan can
	// be reviewed against the options that existed when it was made.
	Catalog map[string]any `json:"catalog,omitempty"`
	// Source names the proposer: "deterministic" or the model identity. A reader
	// must be able to tell a table lookup from a model's reasoning.
	Source string `json:"source"`
	// ProposedAt is when this was formed.
	ProposedAt time.Time `json:"proposedAt"`
}

// Encode renders the plan for an event payload.
func (p RecoveryPlanPayload) Encode() map[string]any {
	steps := make([]any, 0, len(p.Steps))
	for _, step := range p.Steps {
		entry := map[string]any{
			"order": step.Order, "action": step.Action,
			"requiresApproval": step.RequiresApproval,
		}
		putString(entry, "summary", step.Summary)
		putString(entry, "risk", step.Risk)
		putString(entry, "why", step.Why)
		steps = append(steps, entry)
	}
	payload := map[string]any{
		"planId": p.PlanID, "verdict": string(p.Verdict),
		"confidence": p.Confidence, "steps": steps, "source": p.Source,
		"proposedAt": p.ProposedAt,
		// stepCount is separate so a console can show "3 actions" without walking
		// the array, and so a test can assert the number a person would see.
		"stepCount": len(p.Steps),
	}
	putString(payload, "taskId", p.TaskID)
	putString(payload, "trigger", p.Trigger)
	putString(payload, "diagnosis", p.Diagnosis)
	putString(payload, "escalateReason", p.EscalateReason)
	if len(p.Trail) > 0 {
		payload["trail"] = p.Trail
	}
	if len(p.Catalog) > 0 {
		payload["catalog"] = p.Catalog
	}
	return payload
}

// RecoveryExecutedPayload is the body of ops.recovery_executed.
//
// It records one attempt at an approved recovery action, and its most important
// fields are the pair (Executed, Verified). They are separate booleans rather
// than one status, because "we ran it and could not tell" is the state this
// system exists to never paper over: an operator must be able to see that
// something ran without a confirmed effect, since that is exactly when an
// automatic retry is forbidden and a person has to go and look.
type RecoveryExecutedPayload struct {
	PlanID   string `json:"planId,omitempty"`
	ActionID string `json:"actionId"`
	// Executed is true only when a tool call was actually dispatched. It is not
	// "the execution was attempted": an attempt refused by the catalog, blocked
	// by a missing tool, or stopped because no decider was configured all report
	// false, and the reason says which.
	Executed bool `json:"executed"`
	// Verified is true only when a verifier confirmed the effect afterwards. It
	// is never inferred from Executed.
	Verified bool `json:"verified"`
	// Summary is the sentence a replay shows.
	Summary string `json:"summary"`
	// Verification is the verifier's finding, or why there was none.
	Verification string `json:"verification,omitempty"`
	// Tools are the tools this action was allowed to call, so a reader can see
	// the scope the approval covered without consulting the catalog.
	Tools []string `json:"tools,omitempty"`
	// Trail names the checkpoints the attempt passed, in order. It is the same
	// road the console showed, kept so the replay does not have to be believed on
	// trust.
	Trail []string `json:"trail,omitempty"`
	// OperatorApproved says a person initiated this, distinguishing their decision
	// from one made by an automatic path.
	OperatorApproved bool `json:"operatorApproved,omitempty"`
	// ApprovalEvidence names the session the approval came from, so the boolean
	// above is not the only thing a later reader has to go on.
	ApprovalEvidence string `json:"approvalEvidence,omitempty"`
	// Failed is set when the attempt itself broke, as opposed to being refused.
	// A refusal is a decision and has Executed=false with a reason; a failure is
	// the machinery not working, and the two must not read the same.
	Failed string `json:"failed,omitempty"`
	// OccurredAt is when the attempt finished.
	OccurredAt time.Time `json:"occurredAt"`
}

// Encode renders the attempt for an event payload.
func (p RecoveryExecutedPayload) Encode() map[string]any {
	payload := map[string]any{
		"actionId": p.ActionID, "executed": p.Executed, "verified": p.Verified,
		"summary": p.Summary, "occurredAt": p.OccurredAt,
	}
	putString(payload, "planId", p.PlanID)
	putString(payload, "verification", p.Verification)
	putString(payload, "failed", p.Failed)
	if len(p.Tools) > 0 {
		payload["tools"] = p.Tools
	}
	if len(p.Trail) > 0 {
		payload["trail"] = p.Trail
	}
	if p.OperatorApproved {
		payload["operatorApproved"] = true
	}
	putString(payload, "approvalEvidence", p.ApprovalEvidence)
	return payload
}

// AnomalyClearedPayload is the body of ops.anomaly_cleared.
//
// It closes a condition a finding opened. The fields name the condition the same
// way the opening event did — code and component are the identity — so a reader
// pairing the two does not have to match on prose.
type AnomalyClearedPayload struct {
	// AnomalyID is the identity of the opening report, count suffix included, so
	// the pair (opened, cleared) is exact.
	AnomalyID string `json:"anomalyId"`
	// Identity is the condition without its count suffix: the same value whatever
	// the count was when it opened.
	Identity  string `json:"identity"`
	Code      string `json:"code,omitempty"`
	Component string `json:"component,omitempty"`
	// Detail says how it was established that the condition is gone, in one line.
	Detail    string    `json:"detail,omitempty"`
	ClearedAt time.Time `json:"clearedAt"`
}

// Encode renders the closing edge for an event payload.
func (p AnomalyClearedPayload) Encode() map[string]any {
	payload := map[string]any{
		"anomalyId": p.AnomalyID, "identity": p.Identity, "clearedAt": p.ClearedAt,
	}
	putString(payload, "code", p.Code)
	putString(payload, "component", p.Component)
	putString(payload, "detail", p.Detail)
	return payload
}
