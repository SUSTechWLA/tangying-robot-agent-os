package agentruntime

import (
	"context"
	"errors"
	"strings"
	"sync"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
)

// OpsAgentVersion identifies this agent's behaviour.
const OpsAgentVersion = "1"

// OpsAgent observes task execution and reports what is wrong. It diagnoses and
// warns; it does not fix.
//
// # Why it cannot fix
//
// The constraint is structural, not procedural. This type holds no execution
// port, no task service and no bus handle. There is no field through which it
// could dispatch an action, change a task state or publish a mutation request,
// so "the observer cannot touch the robot" is a property of the type rather than
// a rule someone has to remember to enforce. The runtime's permission gate is
// the second line, not the first.
//
// The one thing it does produce that could be mistaken for an action is
// ops.recovery_proposed, and that is a request for a decision. Every proposal it
// emits is marked advisory and approval-required, and there is a test asserting
// that, because the cheapest way to break this design later is to make one
// proposal that says otherwise.
//
// # Why its rules are deterministic
//
// Findings come from a fixed rule set over facts the system already established
// — the telemetry contract, the robot's own fault report, durable execution
// records, the closed-loop failure classifier. Nothing is inferred by a model
// and nothing is guessed: when the evidence is insufficient the agent says which
// evidence is missing instead of producing a plausible cause. An operator acting
// on a robot needs to know whether a statement was proven or suspected.
type OpsAgent struct {
	// Telemetry reads the latest robot observation. Optional: without it the
	// agent reports that it has no observation rather than reporting health.
	Telemetry func(ctx context.Context, taskID string) (telemetry.Snapshot, error)
	// Memory is the execution layer the agent reads. Only Execution is used;
	// Ledger and Beads exist for a future ExperienceAgent.
	Memory agentcontract.Memory
	// History, when set, is the durable record of tasks that already exist. It
	// is how the supervisor learns about failures that happened before this
	// process started: live events only describe the present.
	History agentcontract.TaskHistory
	// Publish emits an agent event. A nil sink means the agent observes and says
	// nothing, which is the behaviour when the runtime is not running.
	Publish func(ctx context.Context, event agentcontract.Event)
	// RunnerAlerts, when set, keeps the current robot-level findings so a console
	// can show them. Findings about a task go to the task ledger instead; the two
	// scopes have different lifetimes and different readers.
	RunnerAlerts *AlertStore

	// TelemetryMaxAge and StepLatencyBudget override the rule thresholds.
	TelemetryMaxAge   time.Duration
	StepLatencyBudget time.Duration

	// Now is the evaluation clock. Tests set it; production leaves it nil.
	Now func() time.Time

	mu sync.Mutex
	// health is the last reported health, and healthReason the code that
	// produced it. They are kept so that agent.health_changed is published on a
	// real change and not on every check: an agent that republished its health
	// every interval would fill the event stream with a status that never
	// changed, which is how an operator learns to ignore it.
	health       agentcontract.HealthStatus
	healthReason string
	// findings remembers the last anomaly identity emitted per component and
	// code, and when. A condition that persists is reported once, not once per
	// evaluation: the fault ledger in this repository already had to fix exactly
	// that failure mode, where a self-healing fault was escalated to a human
	// within seconds because every observation re-counted it.
	findings map[string]time.Time
	// failedActions accumulates failures seen since the last evaluation.
	failedActions map[string]FailedAction
	// tasks tracks the task ids the agent has been told about, so an evaluation
	// knows what to read execution records for.
	tasks map[string]struct{}
	// latestTask is the task most recently seen, used when an evaluation has no
	// better scope.
	latestTask string

	started bool
	stopped bool
	// recoverDone records that the startup sweep has run once. It runs once and
	// not on every evaluation: the durable record's changes arrive as live
	// events too, so re-reading it each tick would be a query to learn nothing.
	recoverDone bool
}

var _ agentcontract.Agent = (*OpsAgent)(nil)

// NewOpsAgent creates the observing agent.
func NewOpsAgent() *OpsAgent {
	return &OpsAgent{
		findings:      map[string]time.Time{},
		failedActions: map[string]FailedAction{},
		tasks:         map[string]struct{}{},
	}
}

// SetPublish installs the bus this agent publishes its findings on.
//
// It is called by the runtime during start, before any event is delivered, so
// the assignment never races a read. An observer that is never given a bus still
// observes and still records what it found — it simply cannot tell anyone. That
// is why the runtime hands it one rather than trusting a call site to remember.
func (a *OpsAgent) SetPublish(publish func(ctx context.Context, event agentcontract.Event)) {
	a.Publish = publish
}

// Name is the stable configuration and event-attribution identity.
func (a *OpsAgent) Name() string { return OpsAgentName }

// Version identifies this agent's behaviour.
func (a *OpsAgent) Version() string { return OpsAgentVersion }

// Capabilities declares observation and diagnosis. It deliberately does not
// declare completion.verification: that belongs to a future EvalAgent which may
// veto a completion claim, and this agent must not appear to have that power.
func (a *OpsAgent) Capabilities() []agentcontract.Capability {
	return []agentcontract.Capability{
		agentcontract.CapabilityObservation,
		agentcontract.CapabilityDiagnosis,
	}
}

// Subscriptions are the topics this agent observes: everything that describes
// what the task agent did, plus the ops namespace so it can see its own findings
// and the runtime's decisions about them.
func (a *OpsAgent) Subscriptions() []string {
	return []string{
		agentcontract.TopicTaskAll,
		agentcontract.TopicActionAll,
		agentcontract.TopicEvidenceAll,
		agentcontract.TopicStateAll,
		agentcontract.TopicOpsAll,
	}
}

// Permissions declares read-only, with every mutating field false.
func (a *OpsAgent) Permissions() agentcontract.Permission {
	return agentcontract.ReadOnlyPermission()
}

// Health reports whether the agent can observe.
//
// It reports DEGRADED rather than HEALTHY when it is running but has no
// observation to reason about: an observer that cannot see is not healthy, and
// saying so is the difference between "nothing is wrong" and "I cannot tell".
func (a *OpsAgent) Health(_ context.Context) agentcontract.Health {
	now := a.now().UTC()
	a.mu.Lock()
	stopped := a.stopped
	latestTask := a.latestTask
	a.mu.Unlock()

	if stopped {
		return agentcontract.Health{
			Status: agentcontract.HealthStopped, ReasonCode: "AGENT_SHUTDOWN", CheckedAt: now,
		}
	}
	if a.Telemetry == nil {
		return agentcontract.Health{
			Status: agentcontract.HealthDegraded, ReasonCode: "TELEMETRY_UNAVAILABLE",
			Detail: map[string]string{
				"reason": "no telemetry source is configured, so robot state cannot be observed",
			},
			CheckedAt: now,
		}
	}
	readContext, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	snapshot, err := a.Telemetry(readContext, latestTask)
	if err != nil {
		return agentcontract.Health{
			Status: agentcontract.HealthDegraded, ReasonCode: "TELEMETRY_READ_FAILED",
			Detail:    map[string]string{"error": truncateForHealth(err.Error())},
			CheckedAt: now,
		}
	}
	if snapshot.ObservedAt.IsZero() && snapshot.RobotID == "" {
		return agentcontract.Health{
			Status: agentcontract.HealthDegraded, ReasonCode: "TELEMETRY_EMPTY",
			Detail:    map[string]string{"reason": "the telemetry source returned no observation"},
			CheckedAt: now,
		}
	}
	return agentcontract.Health{Status: agentcontract.HealthHealthy, CheckedAt: now}
}

// Execute always refuses. An observer does not execute tasks, and returning a
// typed refusal is better than a silent success: a caller that asked the wrong
// agent gets told so instead of believing something happened.
func (a *OpsAgent) Execute(context.Context, agentcontract.ExecuteRequest) (agentcontract.ExecutionOutcome, error) {
	return agentcontract.ExecutionOutcome{}, agentcontract.ErrNotExecutable
}

// Shutdown stops the agent. It is idempotent and cancels nothing: this agent
// holds no work that a task depends on.
func (a *OpsAgent) Shutdown(context.Context) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.stopped = true
	return nil
}

// OnEvent consumes one event and updates what the agent knows.
//
// It must return promptly: the runtime delivers on a bounded queue, and an
// observer that does slow work here loses events. This method therefore only
// accumulates facts; the rules run on the evaluation tick.
func (a *OpsAgent) OnEvent(_ context.Context, event agentcontract.Event) error {
	a.mu.Lock()
	if a.stopped {
		a.mu.Unlock()
		return nil
	}
	a.started = true
	if event.TaskID != "" {
		a.tasks[event.TaskID] = struct{}{}
		a.latestTask = event.TaskID
	}
	// The lock is released before accumulating. recordFailedAction takes the lock
	// itself — it is shared with the startup sweep, which reaches it with no lock
	// held — so calling it from inside this critical section would deadlock. That
	// mistake was made and caught by the test suite hanging rather than failing.
	a.mu.Unlock()

	if event.Topic != agentcontract.TopicActionExecuted {
		return nil
	}
	status, _ := event.Payload["activityStatus"].(string)
	if status != "FAILED" {
		return nil
	}
	a.recordFailedAction(event.Payload)
	return nil
}

// recordFailedAction accumulates one failed dispatch.
//
// Failures are accumulated rather than evaluated at the point they arrive so that
// one evaluation sees the whole set: a rule that judged each failure in isolation
// could not tell a step that failed once from one that keeps failing, and the
// second is the one that needs a person.
//
// It is shared by the live path and the startup sweep on purpose. Two copies of
// this would be two definitions of "what counts as a failed action", and the
// whole point of the sweep is that a failure that outlived its process is the
// same failure.
func (a *OpsAgent) recordFailedAction(payload map[string]any) {
	if payload == nil {
		return
	}
	status, _ := payload["activityStatus"].(string)
	if status != "FAILED" {
		return
	}
	stepID, _ := payload["stepId"].(string)
	toolName, _ := payload["toolName"].(string)
	errorCode, _ := payload["error"].(string)
	key := stepID + "\x00" + toolName + "\x00" + errorCode
	a.mu.Lock()
	defer a.mu.Unlock()
	existing := a.failedActions[key]
	existing.StepID = stepID
	existing.ToolName = toolName
	existing.ErrorCode = extractErrorCode(errorCode)
	existing.Occurrences++
	a.failedActions[key] = existing
}

// Observe runs one evaluation and publishes what it found. It is the agent's
// real work and it is called on the runtime's tick, not from OnEvent.
func (a *OpsAgent) Observe(ctx context.Context) []Finding {
	a.mu.Lock()
	if a.stopped {
		a.mu.Unlock()
		return nil
	}
	a.started = true
	a.mu.Unlock()

	// Learn what already exists before reasoning about the present. This is the
	// restart path, and it is deliberately separate from the event path: one
	// covers what the agent witnessed, the other covers what outlived the
	// process that witnessed it.
	a.learnExistingTasks(ctx)

	a.mu.Lock()
	taskIDs := make([]string, 0, len(a.tasks))
	for taskID := range a.tasks {
		taskIDs = append(taskIDs, taskID)
	}
	failedActions := make([]FailedAction, 0, len(a.failedActions))
	for _, action := range a.failedActions {
		failedActions = append(failedActions, action)
	}
	a.failedActions = map[string]FailedAction{}
	latestTask := a.latestTask
	a.mu.Unlock()

	input := ObservationInput{
		TaskID:            latestTask,
		FailedActions:     failedActions,
		Now:               a.now().UTC(),
		TelemetryMaxAge:   a.TelemetryMaxAge,
		StepLatencyBudget: a.StepLatencyBudget,
	}
	input.UncertainSteps = a.uncertainSteps(ctx, taskIDs)
	input.AbnormalTasks = a.abnormalTasks(ctx)
	if a.Telemetry != nil {
		snapshot, err := a.Telemetry(ctx, latestTask)
		if err == nil {
			input.Snapshot = &snapshot
			input.SnapshotReceivedAt = a.now().UTC()
		}
	}

	findings := Evaluate(input)
	// Robot-level findings are recorded before publishing so a console that reads
	// the store never sees a finding that was already announced elsewhere and is
	// missing here.
	if a.RunnerAlerts != nil {
		a.RunnerAlerts.Record(findings)
	}
	a.publishHealth(ctx)
	for _, finding := range findings {
		// The finding's own task wins. The agent's "most recent task" is only a
		// fallback for findings that are not about one task (a robot-wide fault,
		// a stale observation). Attributing a task-specific finding to whatever
		// task happened to be seen last would file the diagnosis against the
		// wrong task, which is worse than filing it against none.
		taskID := finding.TaskID
		if taskID == "" {
			taskID = latestTask
		}
		a.publishFinding(ctx, finding, taskID)
	}
	return findings
}

// uncertainSteps reads the durable records of physical steps whose outcome is
// unknown. It reads per task and stops at the first error: a missing execution
// store means "cannot tell", which the rules already report, and inventing an
// empty list would read as "nothing is uncertain".
func (a *OpsAgent) uncertainSteps(ctx context.Context, taskIDs []string) []agentcontract.StepRecord {
	if a.Memory.Execution == nil {
		return nil
	}
	uncertain := make([]agentcontract.StepRecord, 0)
	for _, taskID := range taskIDs {
		steps, err := a.Memory.Execution.Uncertain(ctx, taskID)
		if err != nil {
			return nil
		}
		uncertain = append(uncertain, steps...)
	}
	return uncertain
}

// abnormalTasks reads the durable list of tasks that ended abnormally. It is
// reported every evaluation rather than once, because it describes a standing
// condition: while a task is unreviewed, an operator should keep being told.
func (a *OpsAgent) abnormalTasks(ctx context.Context) []string {
	a.mu.Lock()
	history := a.History
	a.mu.Unlock()
	if history == nil {
		return nil
	}
	ids, err := history.Abnormal(ctx)
	if err != nil {
		return nil
	}
	return ids
}

// publishHealth emits agent.health_changed only when the status actually
// changed.
func (a *OpsAgent) publishHealth(ctx context.Context) {
	if a.Publish == nil {
		return
	}
	health := a.Health(ctx)
	a.mu.Lock()
	previous := a.health
	changed := previous != health.Status
	a.health, a.healthReason = health.Status, health.ReasonCode
	a.mu.Unlock()
	if !changed {
		return
	}
	a.Publish(ctx, agentcontract.Event{
		Topic: agentcontract.TopicAgentHealthChanged, Agent: OpsAgentName, AgentVersion: OpsAgentVersion,
		Priority: agentcontract.PriorityLow, OccurredAt: a.now().UTC(),
		Payload: agentcontract.HealthChangedPayload{
			Previous: previous, Current: health.Status, ReasonCode: health.ReasonCode,
		}.Encode(),
	})
}

// anomalyCooldown is how long a resolved-then-recurring anomaly stays quiet
// after being reported. It bounds repetition without hiding a new occurrence:
// the identity is only suppressed while the condition is being re-observed.
const anomalyCooldown = 60 * time.Second

// publishFinding emits the events that describe one finding.
//
// A finding produces an anomaly, and then a hypothesis when the rules
// established a failure class; and, when the situation needs a person, an
// escalation. The recovery proposal is always advisory and always requires
// approval, because the only safe automation in this version is none.
func (a *OpsAgent) publishFinding(ctx context.Context, finding Finding, taskID string) {
	if a.Publish == nil {
		return
	}
	now := a.now().UTC()
	// One rule for "which finding is this", shared with the alert store, the
	// plan that answers it and the console that shows the two together.
	anomalyID := agentcontract.AnomalyReportID(finding.Code, finding.Component, countFact(finding))

	// The cooldown key adds what makes this report different from the last one:
	// the count, and the task it is filed against.
	//
	// The count, because a taskCount that moved from one to five inside the
	// window is not a repeat of the same report, and going quiet is exactly the
	// wrong response to a situation getting worse.
	//
	// The task, because a report that could not be attributed to any task goes
	// nowhere: the ledger is per task, so the bridge that persists agent events
	// drops it. Letting such a report consume the cooldown silences the same
	// finding for the whole window — including the tick where a task became known
	// and the report would finally have been recorded. That happened: an
	// evaluation running before the first event of any task reported a standing
	// fault into the void, and the task that then failed never saw it in its
	// replay.
	reportKey := taskID + "\x00" + anomalyID
	if !a.shouldReport(reportKey, now) {
		return
	}
	event := agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, TaskID: taskID,
		Agent: OpsAgentName, AgentVersion: OpsAgentVersion,
		Priority: priorityForSeverity(finding.Severity), OccurredAt: now,
		CorrelationID: taskID,
		Payload: agentcontract.AnomalyPayload{
			// The payload carries the finding's identity, not the cooldown key:
			// a reader asking "what is this about" must not receive a key with a
			// task id and a separator embedded in it.
			AnomalyID: anomalyID, Severity: finding.Severity, Component: finding.Component,
			Code: finding.Code, Message: finding.Message, Evidence: finding.Evidence,
			Facts: finding.Facts, DetectedAt: now,
		}.Encode(),
	}
	a.Publish(ctx, event)

	if finding.Category == "" {
		return
	}
	hypothesisID := "hyp-" + anomalyID
	a.Publish(ctx, agentcontract.Event{
		Topic: agentcontract.TopicOpsRootCauseHypothesis, TaskID: taskID, StepID: stepOf(finding),
		Agent: OpsAgentName, AgentVersion: OpsAgentVersion,
		Priority: priorityForSeverity(finding.Severity), OccurredAt: now,
		CorrelationID: taskID, CausationID: event.ID,
		Payload: agentcontract.HypothesisPayload{
			HypothesisID: hypothesisID, Category: finding.Category, Confidence: finding.Confidence,
			Summary: finding.Message, EvidenceChain: evidenceChain(finding),
			AffectedComponents: finding.AffectedComponents, RecommendedActions: finding.RecommendedActions,
			AutomaticRetryForbidden: finding.AutomaticRetryForbidden,
			MissingEvidence:         finding.MissingEvidence, ProposedAt: now,
		}.Encode(),
	})

	// Every recommendation this agent has is a person's job. Proposing it as
	// automation would misrepresent what is known, so the proposal is advisory
	// and approval-required by construction.
	if len(finding.RecommendedActions) == 0 {
		return
	}
	a.Publish(ctx, agentcontract.Event{
		Topic: agentcontract.TopicOpsRecoveryProposed, TaskID: taskID, StepID: stepOf(finding),
		Agent: OpsAgentName, AgentVersion: OpsAgentVersion,
		Priority: priorityForSeverity(finding.Severity), OccurredAt: now,
		CorrelationID: taskID, CausationID: event.ID,
		Payload: agentcontract.RecoveryProposalPayload{
			ProposalID: "prop-" + anomalyID, Action: strings.Join(finding.RecommendedActions, "；"),
			AutomationLevel: AutomationAdvisory, RequiresApproval: true,
			Rationale: finding.Message, HypothesisID: hypothesisID,
		}.Encode(),
	})

	if finding.Severity == SeverityCritical {
		a.Publish(ctx, agentcontract.Event{
			Topic: agentcontract.TopicOpsEscalationRequired, TaskID: taskID, StepID: stepOf(finding),
			Agent: OpsAgentName, AgentVersion: OpsAgentVersion,
			Priority: agentcontract.PriorityCritical, OccurredAt: now,
			CorrelationID: taskID, CausationID: event.ID,
			Payload: agentcontract.EscalationPayload{
				EscalationID: "esc-" + anomalyID, Reason: finding.Code,
				Urgency: "soon", RaisedAt: now,
				Context: map[string]any{
					"taskId": taskID, "component": finding.Component,
					"category": finding.Category, "summary": finding.Message,
					"automaticRetryForbidden": finding.AutomaticRetryForbidden,
				},
			}.Encode(),
		})
	}
}

// AutomationAdvisory is the only automation level this agent proposes: the
// recommendation is for a person to carry out.
const AutomationAdvisory = "advisory"

// countFact reads how many things a finding is about, when it is about a number.
//
// It returns nil for a finding that reports a condition rather than a quantity.
// A value of an unexpected type is also nil: guessing at it would put a wrong
// number into an identity, which files one condition under two names.
func countFact(finding Finding) *int {
	raw, ok := finding.Facts[countFactKey]
	if !ok {
		return nil
	}
	count, ok := raw.(int)
	if !ok {
		return nil
	}
	return &count
}

// countFactKey is the facts entry that says how many things a finding is about.
// It is what lets the report cooldown tell "still the same one" from "now there
// are five", so it is a named constant rather than a string literal repeated in
// the rule and the identity.
const countFactKey = "taskCount"

func (a *OpsAgent) shouldReport(anomalyID string, now time.Time) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	if last, ok := a.findings[anomalyID]; ok && now.Sub(last) < anomalyCooldown {
		return false
	}
	a.findings[anomalyID] = now
	return true
}

func (a *OpsAgent) now() time.Time {
	if a.Now != nil {
		return a.Now()
	}
	return time.Now()
}

func priorityForSeverity(severity string) agentcontract.Priority {
	switch severity {
	case SeverityCritical:
		return agentcontract.PriorityCritical
	case SeverityWarning:
		return agentcontract.PriorityHigh
	default:
		return agentcontract.PriorityLow
	}
}

func stepOf(finding Finding) string {
	if stepID, ok := finding.Facts["stepId"].(string); ok {
		return stepID
	}
	return ""
}

// evidenceChain is the ordered list of facts a hypothesis rests on. The rule
// codes and the evidence ids are both included: a conclusion is only as good as
// the rule that produced it and the observations it used.
func evidenceChain(finding Finding) []string {
	chain := make([]string, 0, len(finding.Evidence)+1)
	chain = append(chain, "rule:"+finding.Code)
	chain = append(chain, finding.Evidence...)
	if len(chain) == 0 {
		chain = append(chain, "rule:"+finding.Code)
	}
	return chain
}

// extractErrorCode pulls a stable code out of a runtime error message.
//
// The runners put a code after a colon ("skill manipulation.pick failed:
// OBJECT_NOT_FOUND ..."). The code is what classifies the failure, so it is
// worth extracting; when there is nothing recognisable the empty result is
// deliberate, because Classify treats an empty code as an unknown physical
// outcome, which is the conservative reading of a message nobody can parse.
func extractErrorCode(text string) string {
	text = strings.TrimSpace(text)
	if text == "" {
		return ""
	}
	if _, after, found := strings.Cut(text, ": "); found {
		text = strings.TrimSpace(after)
	}
	candidate := text
	if index := strings.IndexAny(candidate, " \t\n("); index >= 0 {
		candidate = candidate[:index]
	}
	candidate = strings.Trim(candidate, ".,;")
	if !looksLikeCode(candidate) {
		return ""
	}
	return candidate
}

// looksLikeCode accepts the upper-snake-case shape the runtime uses. It exists
// so a prose word is not mistaken for a failure code and classified as a
// transient retryable failure, which would be the dangerous direction to be
// wrong in.
func looksLikeCode(candidate string) bool {
	if len(candidate) < 3 || len(candidate) > 64 {
		return false
	}
	hasLetter := false
	for _, char := range candidate {
		switch {
		case char >= 'A' && char <= 'Z':
			hasLetter = true
		case char >= '0' && char <= '9', char == '_':
		default:
			return false
		}
	}
	return hasLetter
}

func truncateForHealth(text string) string {
	if len(text) <= 256 {
		return text
	}
	return text[:256]
}

// ErrOpsAgentNotObservable is returned when the agent was asked to observe
// without a source it can read.
var ErrOpsAgentNotObservable = errors.New("ops agent has no observable source")

// learnExistingTasks reads the durable record once, so the supervisor can see
// failures it did not witness.
//
// Without this the observer only knows about tasks it received an event for,
// which means a restart erases everything that happened before it: the robot may
// have been left mid-action and the supervisor reports a clean, quiet robot. That
// is the worst possible failure mode for a supervisor, because silence is read as
// health.
//
// It runs once. The durable record's changes arrive as live events too, so
// re-reading on every tick would be a query per tick to learn nothing.
func (a *OpsAgent) learnExistingTasks(ctx context.Context) {
	a.mu.Lock()
	alreadyDone := a.recoverDone
	a.mu.Unlock()
	if alreadyDone {
		return
	}

	a.mu.Lock()
	a.recoverDone = true
	history := a.History
	a.mu.Unlock()
	if history == nil {
		return
	}

	// Both lists are consulted. A task can be on record without being abnormal
	// (it is running, or it succeeded) and abnormal without the observer knowing
	// which step was left unconfirmed; taking the union means neither omission
	// hides a task.
	recovered := map[string]struct{}{}
	if ids, err := history.TaskIDs(ctx); err == nil {
		for _, id := range ids {
			if id != "" {
				recovered[id] = struct{}{}
			}
		}
	}
	if ids, err := history.Abnormal(ctx); err == nil {
		for _, id := range ids {
			if id != "" {
				recovered[id] = struct{}{}
			}
		}
	}
	if len(recovered) == 0 {
		return
	}

	a.mu.Lock()
	for id := range recovered {
		a.tasks[id] = struct{}{}
		// A task learned from the record is a candidate scope for findings that
		// are not about one task. Without this, a restarted observer would report
		// a robot-wide fault with no task attached and it would never reach the
		// ledger.
		a.latestTask = id
	}
	a.mu.Unlock()

	// Reading the failures out of the ledger, not just the task ids.
	//
	// A failed tool dispatch is a historical fact, and the action that failed is
	// what an operator needs: "this task ended abnormally" says something is
	// wrong, while "manipulation.pick failed with GRASP_NOT_REACHED" says what.
	// Accumulating only from live events meant a restarted supervisor could never
	// report the second, which is the useful one.
	//
	// The events are fed through the same accumulator the live path uses, so a
	// failure that outlived its process is counted exactly like one that did not.
	for id := range recovered {
		events, err := history.Events(ctx, id)
		if err != nil {
			continue
		}
		for _, event := range events {
			if event.Type != "action.executed" && event.Type != "TOOL_ACTIVITY" {
				continue
			}
			a.recordFailedAction(event.Payload)
		}
	}
}
