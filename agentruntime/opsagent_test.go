package agentruntime_test

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/robotcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

// The observer's whole value is that it can look without touching. That is
// enforced in two independent ways, and both are tested: the type has no way to
// act, and the runtime refuses any request that would contradict the declaration.
func TestOpsAgentDeclaresItselfReadOnly(t *testing.T) {
	agent := agentruntime.NewOpsAgent()
	permission := agent.Permissions()

	if !permission.ReadOnly {
		t.Fatal("the observing agent must declare itself read-only")
	}
	for name, value := range map[string]bool{
		"MutatesWorld":     permission.MutatesWorld,
		"MutatesTaskState": permission.MutatesTaskState,
	} {
		if value {
			t.Fatalf("%s is true on an observer", name)
		}
	}
	if !permission.RequiresApproval {
		t.Fatal("an observer must still require approval for anything beyond observing")
	}
	if permission.MayMutateWorld() || permission.MayMutateTaskState() {
		t.Fatalf("permission %#v permits a mutation", permission)
	}
}

// The structural half of the read-only guarantee: the observer holds no
// execution port.
//
// This is an allowlist rather than a search for suspicious names. A name search
// is both too weak and too strong — it misses a port named something unexpected,
// and it flags an innocent field that happens to contain a substring like
// "tasks". Any new field on this type fails here, which forces the question
// "does the observer need this?" to be answered deliberately rather than
// absorbed silently.
func TestOpsAgentHoldsNoExecutionPort(t *testing.T) {
	allowed := map[string]string{
		// Read-only sources.
		"Telemetry": "reads the latest robot observation",
		"Memory":    "reads durable execution records",
		// A read-only port: TaskHistory has only TaskIDs and Abnormal, so it
		// cannot change a task. It exists so the supervisor can see failures that
		// outlived the process that witnessed them.
		"History": "reads the durable index and ledger of existing tasks",
		// A write-only sink for robot-level findings. It stores text; it cannot
		// reach the robot, and the field being here is what lets an emergency
		// stop, which belongs to no task, reach an operator at all.
		"RunnerAlerts": "records robot-level findings for the console to show",
		// An outbound sink for findings. It carries events only, and what an
		// event can cause is decided by the runtime's permission gate, not here.
		"Publish": "emits ops events for others to route",
		// Thresholds. These bound what is reported, never what is done.
		"TelemetryMaxAge":   "rule threshold",
		"StepLatencyBudget": "rule threshold",
		// Clock and internal bookkeeping.
		"Now":          "clock",
		"mu":           "guards the bookkeeping fields below",
		"health":       "last reported health status",
		"healthReason": "last reported health reason code",
		"findings":     "anomaly report cooldown bookkeeping",
		// Which conditions the previous evaluation observed, so this one can
		// publish the closing edge. It holds the agent's own Finding values —
		// text and scalars it produced — so it is rule bookkeeping like the two
		// above, and it reaches nothing.
		"conditions":    "conditions observed by the previous evaluation",
		"failedActions": "accumulated failure observations",
		"tasks":         "task ids the agent has been told about",
		"latestTask":    "most recently observed task id",
		"recoverDone":   "whether the startup sweep has run once",
		"started":       "lifecycle flag",
		"stopped":       "lifecycle flag",
	}

	fields := reflect.TypeOf(agentruntime.NewOpsAgent()).Elem()
	for index := 0; index < fields.NumField(); index++ {
		field := fields.Field(index)
		if _, ok := allowed[field.Name]; !ok {
			t.Fatalf("OpsAgent has a new field %q of type %s.\n"+
				"The observer must have no path to the robot: if this field is not a read-only "+
				"source or rule bookkeeping, it does not belong on this type.",
				field.Name, field.Type)
		}
	}
	if fields.NumField() != len(allowed) {
		t.Fatalf("OpsAgent has %d fields but %d are accounted for; the list above must be updated deliberately",
			fields.NumField(), len(allowed))
	}
}

// A request to execute must be refused visibly. A silent success would let a
// caller believe an observer had done work it never did.
func TestOpsAgentRefusesToExecute(t *testing.T) {
	agent := agentruntime.NewOpsAgent()
	outcome, err := agent.Execute(context.Background(), agentcontract.ExecuteRequest{TaskID: "task-1"})
	if !errors.Is(err, agentcontract.ErrNotExecutable) {
		t.Fatalf("execute error = %v, want ErrNotExecutable", err)
	}
	if outcome.TaskID != "" {
		t.Fatalf("a refused execution reported an outcome: %#v", outcome)
	}
}

func TestOpsAgentDeclaresObservationCapabilitiesOnly(t *testing.T) {
	capabilities := agentruntime.NewOpsAgent().Capabilities()
	declared := map[agentcontract.Capability]bool{}
	for _, capability := range capabilities {
		declared[capability] = true
	}
	if !declared[agentcontract.CapabilityObservation] || !declared[agentcontract.CapabilityDiagnosis] {
		t.Fatalf("capabilities = %#v, want observation and diagnosis", capabilities)
	}
	// Verifying completion is an EvalAgent's authority, not this agent's. An
	// observer that appeared to hold a veto would be a different system.
	if declared[agentcontract.CapabilityCompletionVerification] {
		t.Fatal("the observer must not claim completion verification")
	}
	if declared[agentcontract.CapabilityTaskExecution] {
		t.Fatal("the observer must not claim task execution")
	}
}

func TestOpsAgentSubscribesToWhatItObserves(t *testing.T) {
	patterns := agentruntime.NewOpsAgent().Subscriptions()
	for _, wanted := range []string{
		agentcontract.TopicTaskAll, agentcontract.TopicActionAll,
		agentcontract.TopicEvidenceAll, agentcontract.TopicStateAll, agentcontract.TopicOpsAll,
	} {
		if !agentcontract.MatchesAny(patterns, strings.TrimSuffix(wanted, "*")+"probe") &&
			!contains(patterns, wanted) {
			t.Fatalf("subscriptions %#v do not include %q", patterns, wanted)
		}
	}
	for _, pattern := range patterns {
		if !agentcontract.KnownPattern(pattern) {
			t.Fatalf("subscription %q is not a declared topic pattern", pattern)
		}
	}
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

// --- rules -----------------------------------------------------------------

func emergencySnapshot() *telemetry.Snapshot {
	return &telemetry.Snapshot{
		RobotID: "robot-1", Adapter: "local-runtime", SchemaVersion: "robot.v1",
		ObservedAt: time.Unix(1000, 0).UTC(), EmergencyStopped: true,
		Anomalies: []string{"EMERGENCY_STOP_LATCHED"},
	}
}

func TestRuleReportsEmergencyStopAsCritical(t *testing.T) {
	now := time.Unix(1005, 0).UTC()
	findings := agentruntime.Evaluate(agentruntime.ObservationInput{
		Snapshot: emergencySnapshot(), Now: now,
	})
	finding, ok := findByCode(findings, agentruntime.AnomalySafetyStop)
	if !ok {
		t.Fatalf("no safety-stop finding: %#v", findings)
	}
	if finding.Severity != agentruntime.SeverityCritical {
		t.Fatalf("severity = %q, want critical", finding.Severity)
	}
	// Clearing an emergency stop is a physical act a person performs. The
	// finding must say so rather than imply the system can retry its way out.
	if len(finding.RecommendedActions) == 0 {
		t.Fatal("a safety stop must tell the operator what to do")
	}
	if finding.AutomaticRetryForbidden {
		t.Fatal("a safety stop is not an unknown-outcome retry prohibition; mislabelling it confuses both")
	}
}

func TestRuleReportsBlockingFaultsFromTheRobotLedger(t *testing.T) {
	now := time.Unix(1005, 0).UTC()
	snapshot := &telemetry.Snapshot{
		RobotID: "robot-1", ObservedAt: time.Unix(1000, 0).UTC(),
		Faults: &robotcontract.FaultReport{
			SchemaVersion: "robot.faults.v1", Severity: "blocked", Count: 1,
			Faults: []robotcontract.Fault{{
				ModuleID: "chassis", Kind: "chassis", Code: "NAV_MAP_NOT_READY",
				Severity: "blocked", Detail: "no map activated", Remedy: "operator_assist",
				UserInstruction: "启用地图", Occurrences: 1,
			}},
			UnavailableCapabilities: []string{"navigation.navigate"},
		},
	}
	findings := agentruntime.Evaluate(agentruntime.ObservationInput{Snapshot: snapshot, Now: now})
	finding, ok := findByCode(findings, agentruntime.AnomalyComponentFault)
	if !ok {
		t.Fatalf("no component-fault finding: %#v", findings)
	}
	if finding.Severity != agentruntime.SeverityCritical {
		t.Fatalf("severity = %q, want critical for a blocked fault", finding.Severity)
	}
	if finding.Component != "chassis" {
		t.Fatalf("component = %q, want chassis", finding.Component)
	}
	if finding.Facts["faultCode"] != "NAV_MAP_NOT_READY" {
		t.Fatalf("facts lost the robot's own code: %#v", finding.Facts)
	}
	// The capabilities the fault took away belong in the finding: an operator
	// needs to know what the robot can no longer do, not only what broke.
	if len(finding.AffectedComponents) == 0 || finding.AffectedComponents[0] != "navigation.navigate" {
		t.Fatalf("affected components = %#v", finding.AffectedComponents)
	}
}

// An info-severity fault is news, not a capability loss, so it must not be
// reported as a problem. This is the line between the observer being useful and
// being noise.
func TestRuleIgnoresInformationalFaults(t *testing.T) {
	snapshot := &telemetry.Snapshot{
		RobotID: "robot-1", ObservedAt: time.Unix(1000, 0).UTC(),
		Faults: &robotcontract.FaultReport{
			SchemaVersion: "robot.faults.v1", Severity: "info", Count: 1,
			Faults: []robotcontract.Fault{{
				ModuleID: "camera", Kind: "camera", Code: "FRAME_RATE_LOW",
				Severity: "info", Remedy: "self_recover", Occurrences: 1,
			}},
		},
	}
	findings := agentruntime.Evaluate(agentruntime.ObservationInput{
		Snapshot: snapshot, Now: time.Unix(1005, 0).UTC(),
	})
	if _, ok := findByCode(findings, agentruntime.AnomalyComponentFault); ok {
		t.Fatalf("an informational fault was reported as a problem: %#v", findings)
	}
}

func TestRuleReportsUnknownPhysicalOutcomeAndForbidsRetry(t *testing.T) {
	now := time.Unix(1005, 0).UTC()
	findings := agentruntime.Evaluate(agentruntime.ObservationInput{
		Snapshot: &telemetry.Snapshot{RobotID: "robot-1", ObservedAt: now},
		UncertainSteps: []agentcontract.StepRecord{{
			TaskID: "task-1", StepID: "pick", Capability: "manipulation.pick",
			SafetyLevel: "physical_motion", Status: string(middleware.StepStarted),
		}},
		Now: now,
	})
	finding, ok := findByCode(findings, agentruntime.AnomalyUnverifiedMutation)
	if !ok {
		t.Fatalf("an unconfirmed physical step was not reported: %#v", findings)
	}
	if !finding.AutomaticRetryForbidden {
		t.Fatal("an unknown physical outcome must forbid automatic retry")
	}
	if finding.Category != "UNKNOWN_OUTCOME" {
		t.Fatalf("category = %q, want UNKNOWN_OUTCOME", finding.Category)
	}
	if finding.Confidence != 1 {
		t.Fatalf("confidence = %v; a durable record of an unconfirmed step is certain", finding.Confidence)
	}
}

func TestRuleClassifiesFailuresWithTheExistingClassifier(t *testing.T) {
	now := time.Unix(1005, 0).UTC()
	cases := []struct {
		code         string
		wantCategory string
		wantForbid   bool
	}{
		{"NAV_BRIDGE_UNAVAILABLE", "TRANSIENT", false},
		{"OBJECT_NOT_FOUND", "PERCEPTION", false},
		{"APPROVAL_REQUIRED", "PERMISSION", false},
		{"TOOL_PARAMETERS_INVALID", "VALIDATION", false},
		{"EXECUTION_OUTCOME_UNKNOWN", "UNKNOWN_OUTCOME", true},
		{"SOMETHING_NOBODY_HAS_SEEN", "UNKNOWN_OUTCOME", true},
	}
	for _, testCase := range cases {
		findings := agentruntime.Evaluate(agentruntime.ObservationInput{
			Snapshot: &telemetry.Snapshot{RobotID: "robot-1", ObservedAt: now},
			FailedActions: []agentruntime.FailedAction{{
				StepID: "pick", ToolName: "manipulation.pick", ErrorCode: testCase.code, Occurrences: 1,
			}},
			Now: now,
		})
		finding, ok := findByCode(findings, agentruntime.AnomalyActionFailed)
		if !ok {
			t.Fatalf("%s: no failure finding: %#v", testCase.code, findings)
		}
		if finding.Category != testCase.wantCategory {
			t.Errorf("%s: category = %q, want %q", testCase.code, finding.Category, testCase.wantCategory)
		}
		if finding.AutomaticRetryForbidden != testCase.wantForbid {
			t.Errorf("%s: AutomaticRetryForbidden = %v, want %v",
				testCase.code, finding.AutomaticRetryForbidden, testCase.wantForbid)
		}
	}
}

func TestRuleRaisesSeverityOnRepeatedFailure(t *testing.T) {
	now := time.Unix(1005, 0).UTC()
	findings := agentruntime.Evaluate(agentruntime.ObservationInput{
		Snapshot: &telemetry.Snapshot{RobotID: "robot-1", ObservedAt: now},
		FailedActions: []agentruntime.FailedAction{{
			StepID: "pick", ToolName: "manipulation.pick",
			ErrorCode: "NAV_BRIDGE_UNAVAILABLE", Occurrences: 3,
		}},
		Now: now,
	})
	finding, _ := findByCode(findings, agentruntime.AnomalyActionFailed)
	if finding.Severity != agentruntime.SeverityCritical {
		t.Fatalf("severity = %q; a repeatedly failing step needs a person", finding.Severity)
	}
}

func TestRuleReportsStaleAndMissingObservations(t *testing.T) {
	now := time.Unix(1000, 0).UTC()

	// No observation at all: the observer must say it cannot tell, rather than
	// stay silent, because silence reads as "nothing is wrong".
	missing := agentruntime.Evaluate(agentruntime.ObservationInput{Now: now})
	if _, ok := findByCode(missing, agentruntime.AnomalyTelemetryStale); !ok {
		t.Fatalf("no finding for a missing observation: %#v", missing)
	}

	stale := agentruntime.Evaluate(agentruntime.ObservationInput{
		Snapshot: &telemetry.Snapshot{RobotID: "robot-1", ObservedAt: now.Add(-time.Minute)},
		Now:      now,
	})
	finding, ok := findByCode(stale, agentruntime.AnomalyTelemetryStale)
	if !ok {
		t.Fatalf("no finding for a stale observation: %#v", stale)
	}
	if finding.Facts["ageMs"] != int64(60000) {
		t.Fatalf("ageMs = %#v, want 60000", finding.Facts["ageMs"])
	}

	fresh := agentruntime.Evaluate(agentruntime.ObservationInput{
		Snapshot: &telemetry.Snapshot{RobotID: "robot-1", ObservedAt: now.Add(-time.Second)},
		Now:      now,
	})
	if _, ok := findByCode(fresh, agentruntime.AnomalyTelemetryStale); ok {
		t.Fatalf("a fresh observation was reported as stale: %#v", fresh)
	}
}

func TestRuleReportsSlowSteps(t *testing.T) {
	now := time.Unix(1005, 0).UTC()
	findings := agentruntime.Evaluate(agentruntime.ObservationInput{
		Snapshot: &telemetry.Snapshot{RobotID: "robot-1", ObservedAt: now},
		Latency: []agentruntime.LatencyFact{
			{StepID: "pick", Capability: "manipulation.pick", Phase: "execute", Duration: 45 * time.Second},
			{StepID: "place", Capability: "manipulation.place", Phase: "execute", Duration: time.Second},
		},
		Now: now,
	})
	slow := findingsByCode(findings, agentruntime.AnomalyStepLatency)
	if len(slow) != 1 {
		t.Fatalf("slow findings = %d, want exactly the one over budget: %#v", len(slow), findings)
	}
	if slow[0].Facts["stepId"] != "pick" {
		t.Fatalf("reported the wrong step: %#v", slow[0].Facts)
	}
}

// A healthy robot with a fresh observation and nothing uncertain must produce
// no findings. An observer that always has something to say is one an operator
// stops reading.
func TestEvaluateIsSilentWhenNothingIsWrong(t *testing.T) {
	now := time.Unix(1005, 0).UTC()
	findings := agentruntime.Evaluate(agentruntime.ObservationInput{
		Snapshot: &telemetry.Snapshot{
			RobotID: "robot-1", ObservedAt: now, Activity: "IDLE",
			Faults: &robotcontract.FaultReport{SchemaVersion: "robot.faults.v1", Severity: "info", Count: 0},
		},
		Now: now,
	})
	if len(findings) != 0 {
		t.Fatalf("a healthy robot produced findings: %#v", findings)
	}
}

func TestFindingsAreOrderedReproducibly(t *testing.T) {
	now := time.Unix(1005, 0).UTC()
	input := agentruntime.ObservationInput{
		Snapshot: emergencySnapshot(),
		UncertainSteps: []agentcontract.StepRecord{{
			TaskID: "task-1", StepID: "pick", Capability: "manipulation.pick", Status: string(middleware.StepStarted),
		}},
		FailedActions: []agentruntime.FailedAction{{
			StepID: "place", ToolName: "manipulation.place", ErrorCode: "OBJECT_NOT_FOUND", Occurrences: 1,
		}},
		Now: now,
	}
	first := agentruntime.Evaluate(input)
	second := agentruntime.Evaluate(input)
	if len(first) < 2 {
		t.Fatalf("expected several findings, got %#v", first)
	}
	for index := range first {
		if first[index].Code != second[index].Code || first[index].Component != second[index].Component {
			t.Fatalf("evaluation is not reproducible: run 1 = %#v, run 2 = %#v", first, second)
		}
	}
	// Critical first: the report leads with what needs a person.
	if first[0].Severity != agentruntime.SeverityCritical {
		t.Fatalf("first finding severity = %q, want critical", first[0].Severity)
	}
}

// --- observation loop ------------------------------------------------------

func TestOpsAgentPublishesStructuredFindings(t *testing.T) {
	agent := agentruntime.NewOpsAgent()
	agent.Now = func() time.Time { return time.Unix(1000, 0).UTC() }
	agent.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		snapshot := *emergencySnapshot()
		snapshot.ObservedAt = time.Unix(1000, 0).UTC()
		return snapshot, nil
	}
	recorder := &eventCollector{}
	agent.Publish = recorder.sink

	agent.Observe(context.Background())

	// The agent publishes from its own goroutine, so the recorded set is read
	// back through a lock rather than by racing on a plain slice.

	anomaly, ok := findEvent(recorder.all(), agentcontract.TopicOpsAnomalyDetected)
	if !ok {
		t.Fatalf("no anomaly recorder.all(): %#v", topicsOf(recorder.all()))
	}
	if anomaly.Payload["severity"] != agentruntime.SeverityCritical {
		t.Fatalf("anomaly severity = %#v", anomaly.Payload["severity"])
	}
	if anomaly.Agent != "ops" {
		t.Fatalf("anomaly attributed to %q, want ops", anomaly.Agent)
	}
	hypothesis, ok := findEvent(recorder.all(), agentcontract.TopicOpsRootCauseHypothesis)
	if !ok {
		t.Fatalf("no hypothesis recorder.all(): %#v", topicsOf(recorder.all()))
	}
	if chain, ok := hypothesis.Payload["evidenceChain"].([]string); !ok || len(chain) == 0 {
		t.Fatalf("hypothesis has no evidence chain: %#v", hypothesis.Payload)
	}
	proposal, ok := findEvent(recorder.all(), agentcontract.TopicOpsRecoveryProposed)
	if !ok {
		t.Fatalf("no recovery proposal recorder.all(): %#v", topicsOf(recorder.all()))
	}
	// The first version never proposes automation. This is the assertion that
	// keeps that true when someone later wants the observer to act.
	if proposal.Payload["requiresApproval"] != true {
		t.Fatalf("a proposal does not require approval: %#v", proposal.Payload)
	}
	if proposal.Payload["automationLevel"] != agentruntime.AutomationAdvisory {
		t.Fatalf("automation level = %#v, want advisory", proposal.Payload["automationLevel"])
	}
	if _, ok := findEvent(recorder.all(), agentcontract.TopicOpsEscalationRequired); !ok {
		t.Fatalf("a critical finding did not escalate: %#v", topicsOf(recorder.all()))
	}
}

// A persistent condition is reported once, not once per evaluation. The fault
// ledger in this repository already had to fix exactly this failure mode, where
// a self-healing fault was escalated to a person within seconds because every
// observation re-counted it.
//
// The assertion is per anomaly identity rather than a total, because a single
// snapshot can legitimately contain several independent conditions — an
// emergency stop is also a stale observation here — and each deserves to be
// reported once. What must not happen is the same condition being reported
// again on every tick.
func TestOpsAgentDoesNotRepeatAPersistentFinding(t *testing.T) {
	agent := agentruntime.NewOpsAgent()
	agent.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		snapshot := *emergencySnapshot()
		snapshot.ObservedAt = time.Unix(1000, 0).UTC()
		return snapshot, nil
	}
	recorder := &eventCollector{}
	agent.Publish = recorder.sink

	for tick := 0; tick < 5; tick++ {
		agent.Observe(context.Background())
	}

	reports := map[string]int{}
	for _, event := range recorder.all() {
		if event.Topic != agentcontract.TopicOpsAnomalyDetected {
			continue
		}
		id, _ := event.Payload["anomalyId"].(string)
		reports[id]++
	}
	if len(reports) == 0 {
		t.Fatalf("no anomalies were recorder.all() at all: %#v", topicsOf(recorder.all()))
	}
	for id, count := range reports {
		if count != 1 {
			t.Fatalf("anomaly %q was reported %d times over 5 evaluations, want once", id, count)
		}
	}
	if escalations := countTopic(recorder.all(), agentcontract.TopicOpsEscalationRequired); escalations != 1 {
		t.Fatalf("a persistent condition escalated %d times, want once", escalations)
	}
}

func TestOpsAgentPublishesHealthOnlyOnChange(t *testing.T) {
	agent := agentruntime.NewOpsAgent()
	agent.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		return telemetry.Snapshot{RobotID: "robot-1", ObservedAt: time.Now().UTC()}, nil
	}
	recorder := &eventCollector{}
	agent.Publish = recorder.sink
	for tick := 0; tick < 4; tick++ {
		agent.Observe(context.Background())
	}
	changes := countTopic(recorder.all(), agentcontract.TopicAgentHealthChanged)
	if changes != 1 {
		t.Fatalf("health changed event recorder.all() %d times over 4 evaluations, want 1", changes)
	}
}

func TestOpsAgentHealthSaysWhenItCannotSee(t *testing.T) {
	// With no telemetry source the observer is degraded, not healthy: it has no
	// way to know anything, and reporting HEALTHY would be a claim it cannot
	// support.
	blind := agentruntime.NewOpsAgent()
	if health := blind.Health(context.Background()); health.Status != agentcontract.HealthDegraded {
		t.Fatalf("health without telemetry = %#v, want DEGRADED", health)
	} else if health.ReasonCode == "" {
		t.Fatal("a degraded report must carry a reason code")
	}

	failing := agentruntime.NewOpsAgent()
	failing.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		return telemetry.Snapshot{}, errors.New("connection refused")
	}
	if health := failing.Health(context.Background()); health.Status != agentcontract.HealthDegraded {
		t.Fatalf("health with a failing telemetry source = %#v, want DEGRADED", health)
	}

	working := agentruntime.NewOpsAgent()
	working.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		return telemetry.Snapshot{RobotID: "robot-1", ObservedAt: time.Now().UTC()}, nil
	}
	if health := working.Health(context.Background()); health.Status != agentcontract.HealthHealthy {
		t.Fatalf("health with a working source = %#v, want HEALTHY", health)
	}
}

func TestOpsAgentShutdownIsIdempotentAndSilencesObservation(t *testing.T) {
	agent := agentruntime.NewOpsAgent()
	recorder := &eventCollector{}
	agent.Publish = recorder.sink
	if err := agent.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := agent.Shutdown(context.Background()); err != nil {
		t.Fatalf("second shutdown: %v", err)
	}
	if findings := agent.Observe(context.Background()); len(findings) != 0 {
		t.Fatalf("a stopped observer still produced findings: %#v", findings)
	}
	if len(recorderFor(agent).all()) != 0 {
		t.Fatalf("a stopped observer still recorder.all(): %#v", topicsOf(recorderFor(agent).all()))
	}
	if health := agent.Health(context.Background()); health.Status != agentcontract.HealthStopped {
		t.Fatalf("health after shutdown = %#v, want STOPPED", health)
	}
}

func TestOpsAgentAccumulatesFailuresFromEvents(t *testing.T) {
	agent := agentruntime.NewOpsAgent()
	recorder := &eventCollector{}
	agent.Publish = recorder.sink
	agent.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		return telemetry.Snapshot{RobotID: "robot-1", ObservedAt: time.Now().UTC()}, nil
	}

	// Two failures of the same step are one repeated failure, which needs a
	// person; judging each in isolation would report two ordinary failures.
	for attempt := 0; attempt < 2; attempt++ {
		if err := agent.OnEvent(context.Background(), agentcontract.Event{
			Topic: agentcontract.TopicActionExecuted, TaskID: "task-1", Agent: "task",
			Payload: agentcontract.ActionPayload{
				ToolName: "manipulation.pick", ActivityStatus: "FAILED",
				StepID: "pick", Error: "skill manipulation.pick failed: NAV_BRIDGE_UNAVAILABLE down",
			}.Encode(),
		}); err != nil {
			t.Fatal(err)
		}
	}
	agent.Observe(context.Background())

	// The agent publishes from its own goroutine, so the recorded set is read
	// back through a lock rather than by racing on a plain slice.

	anomaly, ok := findEvent(recorder.all(), agentcontract.TopicOpsAnomalyDetected)
	if !ok {
		t.Fatalf("no failure finding recorder.all(): %#v", topicsOf(recorder.all()))
	}
	facts, _ := anomaly.Payload["facts"].(map[string]any)
	if facts["errorCode"] != "NAV_BRIDGE_UNAVAILABLE" {
		t.Fatalf("error code = %#v, want the code extracted from the message", facts["errorCode"])
	}
	if facts["occurrences"] != 2 {
		t.Fatalf("occurrences = %#v, want 2", facts["occurrences"])
	}
}

func TestOpsAgentIgnoresUnparseableErrorText(t *testing.T) {
	// A message with no recognisable code must be treated as an unknown
	// outcome, not guessed into a retryable class: guessing wrong in that
	// direction is what causes a possibly-completed action to be repeated.
	codes := []string{"", "something went wrong", "connection refused"}
	for _, text := range codes {
		if code := agentruntime.ExtractErrorCodeForTest(text); code != "" {
			t.Fatalf("text %q produced code %q, want empty", text, code)
		}
	}
	if code := agentruntime.ExtractErrorCodeForTest("failed: NAV_BRIDGE_UNAVAILABLE down"); code != "NAV_BRIDGE_UNAVAILABLE" {
		t.Fatalf("code = %q, want NAV_BRIDGE_UNAVAILABLE", code)
	}
}

// --- helpers ---------------------------------------------------------------

func findByCode(findings []agentruntime.Finding, code string) (agentruntime.Finding, bool) {
	for _, finding := range findings {
		if finding.Code == code {
			return finding, true
		}
	}
	return agentruntime.Finding{}, false
}

func findingsByCode(findings []agentruntime.Finding, code string) []agentruntime.Finding {
	matched := make([]agentruntime.Finding, 0)
	for _, finding := range findings {
		if finding.Code == code {
			matched = append(matched, finding)
		}
	}
	return matched
}

func findEvent(events []agentcontract.Event, topic string) (agentcontract.Event, bool) {
	for _, event := range events {
		if event.Topic == topic {
			return event, true
		}
	}
	return agentcontract.Event{}, false
}

func countTopic(events []agentcontract.Event, topic string) int {
	count := 0
	for _, event := range events {
		if event.Topic == topic {
			count++
		}
	}
	return count
}

func topicsOf(events []agentcontract.Event) []string {
	topics := make([]string, 0, len(events))
	for _, event := range events {
		topics = append(topics, event.Topic)
	}
	return topics
}

// A report that went nowhere must not silence the same finding once it has
// somewhere to go.
//
// The report cooldown suppresses repeats of a standing condition. But a report
// with no task to file it under is dropped by the bridge that persists agent
// events — the ledger is per task — so it reached nobody. Letting it consume the
// cooldown meant the tick where a task finally became known was silent too, and
// the failure never appeared in that task's replay. The key therefore includes
// the attribution, exactly as it already included the count.
//
// This runs the real evaluation, not a hook: the first pass happens before any
// task is known, the second after one is, and both are driven by the agent's own
// sources.
func TestAnUnattributableReportDoesNotSilenceTheAttributedOne(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	agent := agentruntime.NewOpsAgent()
	agent.Now = func() time.Time { return now }
	// A robot in a latched emergency stop: one standing condition, reported on
	// every evaluation and suppressed by the cooldown in between.
	agent.Telemetry = func(context.Context, string) (telemetry.Snapshot, error) {
		return telemetry.Snapshot{
			RobotID: "robot-local", Adapter: "local-runtime", SchemaVersion: "robot.v1",
			ObservedAt: now, EmergencyStopped: true,
			Anomalies: []string{"EMERGENCY_STOP_LATCHED"},
		}, nil
	}

	var published []agentcontract.Event
	agent.Publish = func(_ context.Context, event agentcontract.Event) {
		published = append(published, event)
	}

	// Nothing has been seen yet: whatever is found cannot be filed under a task.
	agent.Observe(context.Background())
	unattributed := len(published)
	if unattributed == 0 {
		t.Fatal("the first evaluation reported nothing, so there is no unattributable report to test")
	}
	for _, event := range published[:unattributed] {
		if event.TaskID != "" {
			t.Fatalf("a task was known before any event was received: %q", event.TaskID)
		}
	}

	// A task becomes known. The same standing condition now belongs in that
	// task's replay, and the cooldown must not be the reason it is missing.
	if err := agent.OnEvent(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicTaskStarted, TaskID: "task-known",
	}); err != nil {
		t.Fatalf("deliver the task event: %v", err)
	}
	agent.Observe(context.Background())

	attributed := false
	for _, event := range published[unattributed:] {
		if event.TaskID == "task-known" && event.Topic == agentcontract.TopicOpsAnomalyDetected {
			attributed = true
		}
	}
	if !attributed {
		t.Fatalf("the finding was never attributed to the task that became known: %#v", published)
	}

	// The payload still names the finding itself. The cooldown key carries a task
	// id and a separator, and neither belongs in a field a reader interprets.
	reports := 0
	for _, event := range published {
		if event.Topic != agentcontract.TopicOpsAnomalyDetected {
			continue
		}
		reports++
		id, _ := event.Payload["anomalyId"].(string)
		if id == "" || strings.ContainsRune(id, '\x00') || strings.HasSuffix(id, "@") {
			t.Fatalf("anomalyId = %q, want the finding's own identity", id)
		}
	}
	if reports != 2 {
		t.Fatalf("published %d anomaly reports, want one per evaluation", reports)
	}
}
