package agentruntime_test

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

// The fault matrix.
//
// The question this file answers is not "does the observer have rules" — the
// unit tests in opsagent_test.go cover that — but "when a real task goes wrong,
// does the supervisor actually say something useful". So every case here drives
// the real OpsAgent with a simulated fault and asserts three separate things:
//
//   1. the fault is detected at all,
//   2. it is classified the way the rest of the system classifies it,
//   3. it comes with an action a person could carry out.
//
// The third is the one that has never been tested. A finding with no usable
// recommendation is a log line: it tells an operator that something is wrong,
// which they can already see, and not what to do about it.
//
// These scenarios are simulated from the documented failure vocabulary rather
// than captured from a live robot, so they are reproducible and can run in CI.
// Where a scenario's shape comes from a real record, the record is named.

type faultScenario struct {
	name string
	// why records what makes this scenario worth testing, in one line.
	why string

	// The fault, exactly one of these shapes.
	actionError  string
	occurrences  int
	faults       *robotcontract.FaultReport
	emergency    bool
	stuckSteps   []agentcontract.StepRecord
	latency      []agentruntime.LatencyFact
	noTelemetry  bool
	staleSeconds int

	// What the supervisor must say about it.
	wantCode     string
	wantCategory string
	wantSeverity string
	// wantRetryForbidden is asserted explicitly on every case rather than only
	// where it is true: the dangerous regression is a failure whose retry
	// prohibition quietly disappears.
	wantRetryForbidden bool
	// wantAdviceContains are substrings the operator-facing action must contain.
	// They are the concrete content of the recommendation, not its presence.
	wantAdviceContains []string
}

func scenarioObservation(scenario faultScenario, now time.Time) agentruntime.ObservationInput {
	input := agentruntime.ObservationInput{
		TaskID: "task-fault",
		Now:    now,
		FailedActions: []agentruntime.FailedAction{{
			StepID: "step-1", ToolName: "manipulation.pick",
			ErrorCode: scenario.actionError, Occurrences: max(scenario.occurrences, 1),
		}},
		Latency:        scenario.latency,
		UncertainSteps: scenario.stuckSteps,
	}
	if scenario.actionError == "" {
		input.FailedActions = nil
	}
	if !scenario.noTelemetry {
		observedAt := now
		if scenario.staleSeconds > 0 {
			observedAt = now.Add(-time.Duration(scenario.staleSeconds) * time.Second)
		}
		input.Snapshot = &telemetry.Snapshot{
			RobotID: "robot-1", Adapter: "local-runtime", SchemaVersion: "robot.v1",
			ObservedAt: observedAt, EmergencyStopped: scenario.emergency,
			Faults: scenario.faults,
		}
		if scenario.emergency {
			input.Snapshot.Anomalies = []string{"EMERGENCY_STOP_LATCHED"}
		}
	}
	return input
}

// The seven failure classes from core/closedloop, each with the recovery the
// repository already decided is the only safe one for it. The supervisor must
// reuse that classification rather than forming its own opinion: two taxonomies
// would eventually disagree about whether a retry is allowed.
func TestFaultMatrixCoversEveryFailureClass(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	scenarios := []faultScenario{
		{
			name: "TRANSIENT: the bridge is down, a retry is safe",
			why:  "the infrastructure will come back, so this is the one class where repeating the command is correct",
			// Real code from the navigation bridge.
			actionError: "NAV_BRIDGE_UNAVAILABLE",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.Transient),
			wantSeverity:       agentruntime.SeverityWarning,
			wantAdviceContains: []string{"可重试"},
		},
		{
			name: "PERCEPTION: the object is not where it was",
			why:  "repeating the same command cannot help; a new observation must come first",
			// Real code from a live miss.
			actionError: "OBJECT_NOT_FOUND",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.Perception),
			wantSeverity:       agentruntime.SeverityWarning,
			wantAdviceContains: []string{"重新观测", "搜索"},
		},
		{
			name:        "PLANNING: the destination is unreachable",
			why:         "the plan is wrong, so the advice must be a new plan and never a replay",
			actionError: "TARGET_UNREACHABLE",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.Planning),
			wantSeverity:       agentruntime.SeverityWarning,
			wantAdviceContains: []string{"新的目标", "路径"},
		},
		{
			// Navigation obstruction, as the mobile runtime actually reports it:
			// the goal lies outside the commissioned world, or the base ran out
			// of bounded control steps getting there.
			//
			// These are the obstruction codes the runtime emits, and they are
			// named because this is the scenario a robot in a home hits most
			// often — furniture moved, a doorway blocked, a map gone stale.
			name:        "PLANNING: the base cannot reach the goal (workspace limit)",
			why:         "the goal is outside the commissioned world, so retrying the same goal cannot help",
			actionError: "NAV_WORKSPACE_LIMIT",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.Planning),
			wantSeverity:       agentruntime.SeverityWarning,
			wantAdviceContains: []string{"新的目标", "路径"},
		},
		{
			name:        "PLANNING: the base ran out of control steps (obstructed route)",
			why:         "the route is blocked or the map is wrong; replaying the same path repeats the failure",
			actionError: "NAV_STEP_LIMIT",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.Planning),
			wantSeverity:       agentruntime.SeverityWarning,
			wantAdviceContains: []string{"新的目标", "路径"},
		},
		{
			name:        "PERMISSION: the step needs approval that is not there",
			why:         "retrying without the approval can never succeed, and an operator must know that",
			actionError: "APPROVAL_REQUIRED",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.Permission),
			wantSeverity:       agentruntime.SeverityWarning,
			wantAdviceContains: []string{"审批", "租约", "资源"},
		},
		{
			name: "RESOURCE: another owner holds the object",
			why:  "the fence is doing its job; the advice is to look at ownership, not to retry harder",
			// A stale fencing token is what a second controller sees.
			actionError: "FENCING_TOKEN_STALE",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.Resource),
			wantSeverity:       agentruntime.SeverityWarning,
			wantAdviceContains: []string{"审批", "租约", "资源"},
		},
		{
			name:        "VALIDATION: the command itself was rejected",
			why:         "identical arguments cannot start working, so 'retry' would be actively misleading advice",
			actionError: "TOOL_PARAMETERS_INVALID",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.Validation),
			wantSeverity:       agentruntime.SeverityWarning,
			wantAdviceContains: []string{"参数", "版本", "重放"},
		},
		{
			name:        "UNKNOWN_OUTCOME: the transport died mid-action",
			why:         "this is the class that must never be retried; the robot may have moved and nobody knows",
			actionError: "EXECUTION_OUTCOME_UNKNOWN",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.UnknownOutcome),
			wantSeverity: agentruntime.SeverityCritical, wantRetryForbidden: true,
			wantAdviceContains: []string{"不要自动重试", "对账"},
		},
		{
			name:        "FATAL: the post-action check said no",
			why:         "the action completed and the world disagrees; only a person can decide",
			actionError: "VERIFICATION_FAILED",
			wantCode:    agentruntime.AnomalyActionFailed, wantCategory: string(closedloop.Fatal),
			wantSeverity:       agentruntime.SeverityWarning,
			wantAdviceContains: []string{"人工"},
		},
	}

	for _, scenario := range scenarios {
		t.Run(scenario.name, func(t *testing.T) {
			findings := agentruntime.Evaluate(scenarioObservation(scenario, now))
			assertScenario(t, scenario, findings)
		})
	}
}

// Faults that are not a tool failure code: the robot's own ledger, the safety
// system, the execution record, timing, and the observation itself.
func TestFaultMatrixCoversNonCodeFaults(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	scenarios := []faultScenario{
		{
			name: "the robot reports a blocked module",
			why:  "the fault ledger already proved it; the supervisor must pass it through with the robot's own code intact",
			faults: &robotcontract.FaultReport{
				SchemaVersion: "robot.faults.v1", Severity: "blocked", Count: 1,
				Faults: []robotcontract.Fault{{
					ModuleID: "chassis", Kind: "chassis", Code: "NAV_MAP_NOT_READY",
					Severity: "blocked", Detail: "no map activated", Remedy: "operator_assist",
					UserInstruction: "启用地图", Occurrences: 1,
				}},
				UnavailableCapabilities: []string{"navigation.navigate"},
			},
			wantCode: agentruntime.AnomalyComponentFault,
			// A blocked fault is not one of the closed-loop retry classes; the
			// supervisor must not pretend it is.
			wantCategory: "", wantSeverity: agentruntime.SeverityCritical,
			wantAdviceContains: []string{"处置", "启用地图"},
		},
		{
			name:         "the robot is emergency stopped",
			why:          "clearing an e-stop is a physical act a person performs; the system must not imply it can retry out of it",
			emergency:    true,
			wantCode:     agentruntime.AnomalySafetyStop,
			wantCategory: string(closedloop.Permission), wantSeverity: agentruntime.SeverityCritical,
			wantAdviceContains: []string{"人工复位", "急停"},
		},
		{
			name: "a physical step was dispatched and never confirmed",
			why:  "the single most important finding: the robot may have moved and the world must be reconciled first",
			stuckSteps: []agentcontract.StepRecord{{
				TaskID: "task-fault", StepID: "pick", Capability: "manipulation.pick",
				SafetyLevel: "physical_motion", Status: string(middleware.StepStarted),
			}},
			wantCode:     agentruntime.AnomalyUnverifiedMutation,
			wantCategory: string(closedloop.UnknownOutcome), wantSeverity: agentruntime.SeverityCritical,
			wantRetryForbidden: true,
			wantAdviceContains: []string{"对账", "观测"},
		},
		{
			name: "a step phase is far over budget",
			why:  "a slow step is the earliest signal of a degrading robot, before anything fails outright",
			latency: []agentruntime.LatencyFact{{
				StepID: "pick", Capability: "manipulation.pick", Phase: "execute",
				Outcome: "COMPLETED", Duration: 90 * time.Second,
			}},
			wantCode: agentruntime.AnomalyStepLatency,
			// A slow step is not a failure class and must not be given one.
			wantCategory: "", wantSeverity: agentruntime.SeverityWarning,
			wantAdviceContains: []string{"设备", "时延"},
		},
		{
			name:         "the observation is too old to reason about",
			why:          "silence from the other rules must not be read as good news; the observer has to say it cannot see",
			staleSeconds: 120,
			wantCode:     agentruntime.AnomalyTelemetryStale,
			wantCategory: "", wantSeverity: agentruntime.SeverityWarning,
			wantAdviceContains: []string{"遥测", "连接"},
		},
		{
			name:         "there is no observation at all",
			why:          "a supervisor that cannot see must say so rather than report a healthy silence",
			noTelemetry:  true,
			wantCode:     agentruntime.AnomalyTelemetryStale,
			wantCategory: "", wantSeverity: agentruntime.SeverityWarning,
			wantAdviceContains: []string{"遥测"},
		},
	}

	for _, scenario := range scenarios {
		t.Run(scenario.name, func(t *testing.T) {
			findings := agentruntime.Evaluate(scenarioObservation(scenario, now))
			assertScenario(t, scenario, findings)
		})
	}
}

// A repeated failure is the signal that a person is needed. One occurrence is
// ordinary; the second means the automatic handling already did not work.
func TestFaultMatrixEscalatesOnRepetition(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	input := agentruntime.ObservationInput{
		TaskID: "task-fault", Now: now,
		Snapshot: &telemetry.Snapshot{RobotID: "robot-1", ObservedAt: now},
		FailedActions: []agentruntime.FailedAction{{
			StepID: "pick", ToolName: "manipulation.pick",
			ErrorCode: "NAV_BRIDGE_UNAVAILABLE", Occurrences: 3,
		}},
	}
	finding, ok := findByCode(agentruntime.Evaluate(input), agentruntime.AnomalyActionFailed)
	if !ok {
		t.Fatal("no finding for a repeatedly failing step")
	}
	if finding.Severity != agentruntime.SeverityCritical {
		t.Fatalf("severity = %q; a step that keeps failing after automatic handling needs a person",
			finding.Severity)
	}
}

// assertScenario is where "did the supervisor actually help" is decided.
func assertScenario(t *testing.T, scenario faultScenario, findings []agentruntime.Finding) {
	t.Helper()
	finding, ok := findByCode(findings, scenario.wantCode)
	if !ok {
		t.Fatalf("%s\n  not detected. codes reported: %v", scenario.why, codesOf(findings))
	}

	// 2. Classified the way the rest of the system classifies it.
	if finding.Category != scenario.wantCategory {
		t.Errorf("%s\n  category = %q, want %q", scenario.why, finding.Category, scenario.wantCategory)
	}
	if finding.Severity != scenario.wantSeverity {
		t.Errorf("%s\n  severity = %q, want %q", scenario.why, finding.Severity, scenario.wantSeverity)
	}
	if finding.AutomaticRetryForbidden != scenario.wantRetryForbidden {
		t.Errorf("%s\n  automaticRetryForbidden = %v, want %v",
			scenario.why, finding.AutomaticRetryForbidden, scenario.wantRetryForbidden)
	}

	// 3. Actionable. This is the part that had no test before, and the part an
	// operator actually depends on.
	if len(finding.RecommendedActions) == 0 {
		t.Fatalf("%s\n  detected but no action was suggested: %#v", scenario.why, finding)
	}
	advice := strings.Join(finding.RecommendedActions, " / ")
	for _, wanted := range scenario.wantAdviceContains {
		if !strings.Contains(advice, wanted) {
			t.Errorf("%s\n  advice %q does not mention %q", scenario.why, advice, wanted)
		}
	}
	// A bare "something went wrong" is not advice. The message has to name
	// something concrete about what was observed.
	if finding.Message == "" {
		t.Errorf("%s\n  finding has no operator-facing message", scenario.why)
	}
	// A finding that cannot be fully explained must say what evidence is missing
	// rather than stopping at "unknown".
	if finding.Category == string(closedloop.UnknownOutcome) && len(finding.Evidence) == 0 &&
		len(finding.MissingEvidence) == 0 && len(finding.Facts) == 0 {
		t.Errorf("%s\n  an unknown-outcome finding carried no evidence, no missing-evidence note and no facts",
			scenario.why)
	}
}

func codesOf(findings []agentruntime.Finding) []string {
	codes := make([]string, 0, len(findings))
	for _, finding := range findings {
		codes = append(codes, finding.Code)
	}
	return codes
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// --- scenarios the fault matrix alone cannot reach ---------------------------
//
// The table cases above call Evaluate directly, which is the right shape for
// testing rules: pure in, pure out. But two of the objective's scenarios are not
// about a rule's output — they are about a sequence. A repeated failure escalates
// because the agent ACCUMULATED occurrences, and an unknown outcome resolves
// because the execution record CHANGED between evaluations. Neither is visible to
// a pure function, so both are driven here through the real OpsAgent.

// A failure that keeps happening stops being ordinary.
//
// One failure is handled by the existing retry policy. A second means that policy
// already did not work, and that is when a person is needed. This runs the real
// agent so the accumulation is exercised, not just the severity branch.
func TestRepeatedFailureEscalatesThroughTheRealAgent(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	observer := agentruntime.NewOpsAgent()
	observer.Telemetry = quietTelemetry()
	observer.Now = func() time.Time { return now }
	recorder := &eventCollector{}
	observer.Publish = recorder.sink

	failure := agentcontract.Event{
		Topic: agentcontract.TopicActionExecuted, TaskID: "task-repeat", StepID: "pick",
		Payload: agentcontract.ActionPayload{
			ToolName: "manipulation.pick", ActivityStatus: "FAILED", StepID: "pick",
			// A transient code: retryable, so on one occurrence this must NOT escalate.
			Error: "skill manipulation.pick failed: NAV_BRIDGE_UNAVAILABLE bridge down",
		}.Encode(),
	}

	// First failure: ordinary. A retryable failure reported once is a warning
	// with retry advice, never a critical.
	if err := observer.OnEvent(context.Background(), failure); err != nil {
		t.Fatal(err)
	}
	first := findingByCode(t, observer.Observe(context.Background()), agentruntime.AnomalyActionFailed)
	if first.Severity != agentruntime.SeverityWarning {
		t.Fatalf("a single transient failure = %q, want warning", first.Severity)
	}
	if first.Facts["occurrences"] != 1 {
		t.Fatalf("occurrences = %#v, want 1", first.Facts["occurrences"])
	}

	// Same step failing again. Repetition is counted **within one evaluation
	// window**: Observe drains the accumulator, so two failures seen before the
	// next evaluation are "occurrences: 2". They are not counted across windows,
	// which is deliberate — a failure from ten minutes ago is history, and the
	// question "is this happening again right now" is the one worth escalating.
	for attempt := 0; attempt < 2; attempt++ {
		if err := observer.OnEvent(context.Background(), failure); err != nil {
			t.Fatal(err)
		}
	}
	// A fresh clock reading so the report cooldown does not suppress the change.
	now = now.Add(2 * time.Minute)
	second := findingByCode(t, observer.Observe(context.Background()), agentruntime.AnomalyActionFailed)
	if second.Facts["occurrences"] != 2 {
		t.Fatalf("occurrences = %#v, want 2 for two failures in one window",
			second.Facts["occurrences"])
	}
	if second.Severity != agentruntime.SeverityCritical {
		t.Fatalf("a repeatedly failing step = %q, want critical: automatic handling already failed",
			second.Severity)
	}
	// And it must say so out loud, not only in the severity field.
	escalation, ok := findEvent(recorder.all(), agentcontract.TopicOpsEscalationRequired)
	if !ok {
		t.Fatalf("a repeated failure did not escalate: %#v", topicsOf(recorder.all()))
	}
	if escalation.Payload["reason"] != agentruntime.AnomalyActionFailed {
		t.Fatalf("escalation reason = %#v", escalation.Payload["reason"])
	}
}

func findingByCode(t *testing.T, findings []agentruntime.Finding, code string) agentruntime.Finding {
	t.Helper()
	finding, ok := findByCode(findings, code)
	if !ok {
		t.Fatalf("no %s finding: %#v", code, codesOf(findings))
	}
	return finding
}

// An unknown physical outcome is the case that must never be retried, and it must
// clear on its own once the world has been reconciled.
//
// This drives the real agent through three evaluations, because the condition
// lives in the execution record rather than in an event: the agent has to notice
// that the record changed.
func TestUnknownOutcomeForbidsRetryAndClearsOnReconciliation(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	store := durableSteps{runs: map[string][]middleware.StepRun{
		"task-unknown": {{
			StepRecord: middleware.StepRecord{
				TaskID: "task-unknown", StepID: "pick",
				Capability: "manipulation.pick", SafetyLevel: "physical_motion",
			},
			Status: middleware.StepStarted,
		}},
	}}
	observer := opsWithMemory(store)
	observer.Now = func() time.Time { return now }
	recorder := &eventCollector{}
	observer.Publish = recorder.sink

	// The agent learns the task exists, the way the runtime tells it.
	if err := observer.OnEvent(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicStateTransition, TaskID: "task-unknown",
		Payload: agentcontract.StateTransitionPayload{To: "EXECUTING"}.Encode(),
	}); err != nil {
		t.Fatal(err)
	}

	pending := findingByCode(t, observer.Observe(context.Background()),
		agentruntime.AnomalyUnverifiedMutation)
	if !pending.AutomaticRetryForbidden {
		t.Fatal("an unknown physical outcome must forbid automatic retry")
	}
	if pending.Category != string(closedloop.UnknownOutcome) {
		t.Fatalf("category = %q, want UNKNOWN_OUTCOME", pending.Category)
	}
	// The advice has to be the reconciliation instruction, not a generic "check
	// the robot": the whole point is that the world must be inspected.
	if advice := strings.Join(pending.RecommendedActions, " / "); !strings.Contains(advice, "对账") {
		t.Fatalf("advice %q does not tell the operator to reconcile", advice)
	}

	// The step reaches a terminal state: the outcome is no longer unknown, so the
	// finding must stop being reported rather than persisting as a stale alarm.
	store.runs["task-unknown"] = []middleware.StepRun{{
		StepRecord: middleware.StepRecord{
			TaskID: "task-unknown", StepID: "pick",
			Capability: "manipulation.pick", SafetyLevel: "physical_motion",
		},
		Status: middleware.StepFailed,
	}}
	now = now.Add(2 * time.Minute)
	if findings := observer.Observe(context.Background()); len(findings) != 0 {
		t.Fatalf("a reconciled step still raised findings: %#v", codesOf(findings))
	}
	// Reported twice more to be sure the silence is stable, not a one-off.
	if findings := observer.Observe(context.Background()); len(findings) != 0 {
		t.Fatalf("findings reappeared on a later evaluation: %#v", codesOf(findings))
	}
}
