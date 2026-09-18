package agentruntime_test

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
)

// The ledger keeps transitions, not observations.
//
// An observer re-reports a standing condition every tick, which is right for a
// console and ruinous for an append-only record: on a real deployment 99.3% of
// 159,314 ledger rows were observer output, and one standing condition on one
// task accounted for 1,410 of them. These tests pin the rule that replaced it —
// open is a fact, a repeat is not, and closing the condition is a fact again.

func detected(taskID, code, component string, count *int) agentcontract.Event {
	return agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyDetected, TaskID: taskID, Agent: "ops",
		Payload: agentcontract.AnomalyPayload{
			AnomalyID: agentcontract.AnomalyReportID(code, component, count),
			Code:      code, Component: component, Severity: "critical", Message: "still true",
		}.Encode(),
	}
}

func cleared(taskID, code, component string) agentcontract.Event {
	return agentcontract.Event{
		Topic: agentcontract.TopicOpsAnomalyCleared, TaskID: taskID, Agent: "ops",
		Payload: agentcontract.AnomalyClearedPayload{
			AnomalyID: agentcontract.AnomalyReportID(code, component, nil),
			Identity:  agentcontract.AnomalyIdentity(code, component),
			Code:      code, Component: component,
		}.Encode(),
	}
}

func TestARepeatedReportOfAStandingConditionIsNotANewFact(t *testing.T) {
	filter := agentruntime.NewLedgerFilter(0)
	first := detected("task-1", "ANOMALY_UNVERIFIED_MUTATION", "execution", nil)
	if !filter.Admit(first) {
		t.Fatal("the first report of a condition must be recorded: it opened")
	}
	for attempt := 0; attempt < 1000; attempt++ {
		if filter.Admit(first) {
			t.Fatalf("repeat %d was recorded; this is the storm", attempt+1)
		}
	}
}

// A worsening condition is re-announced to live subscribers on purpose — the
// report identity carries the count so it can be — and that is exactly why the
// ledger must not key on it. Keying on the report identity would record a
// condition every time its count moved, which is the same storm spelled
// differently.
func TestAWorseningConditionIsStillOneEpisodeInTheLedger(t *testing.T) {
	filter := agentruntime.NewLedgerFilter(0)
	countOf := func(value int) *int { return &value }
	if !filter.Admit(detected("task-1", "ANOMALY_ABNORMAL_TASK", "task", countOf(1))) {
		t.Fatal("the opening report must be recorded")
	}
	if filter.Admit(detected("task-1", "ANOMALY_ABNORMAL_TASK", "task", countOf(5))) {
		t.Fatal("a count that moved is the same condition getting worse, not a second episode")
	}
}

func TestAClosedConditionIsAFactAndReopensTheIdentity(t *testing.T) {
	filter := agentruntime.NewLedgerFilter(0)
	event := detected("task-1", "ANOMALY_COMPONENT_FAULT", "nav", nil)
	if !filter.Admit(event) {
		t.Fatal("opening must be recorded")
	}
	if filter.Admit(event) {
		t.Fatal("repeat must not be recorded")
	}
	if !filter.Admit(cleared("task-1", "ANOMALY_COMPONENT_FAULT", "nav")) {
		t.Fatal("closing must be recorded: it is what bounds the episode")
	}
	if filter.Open() != 0 {
		t.Fatalf("open = %d after a clear, want 0", filter.Open())
	}
	// The condition came back. Without the closing edge this repetition would be
	// suppressed forever, and a fault that recurs would be invisible in the record.
	if !filter.Admit(event) {
		t.Fatal("a condition that returned after clearing must be recorded again")
	}
}

// The identity is what is wrong and where. Two components failing the same way are
// two conditions, and one component failing two ways is two conditions.
func TestDifferentConditionsAreDifferentEpisodes(t *testing.T) {
	filter := agentruntime.NewLedgerFilter(0)
	for name, event := range map[string]agentcontract.Event{
		"nav":    detected("task-1", "ANOMALY_COMPONENT_FAULT", "nav", nil),
		"arm":    detected("task-1", "ANOMALY_COMPONENT_FAULT", "arm", nil),
		"other":  detected("task-1", "ANOMALY_SAFETY_STOP", "nav", nil),
		"task-2": detected("task-2", "ANOMALY_COMPONENT_FAULT", "nav", nil),
	} {
		if !filter.Admit(event) {
			t.Errorf("%s was suppressed; conditions differing in code, component or task are different episodes", name)
		}
	}
}

// Every stage of one finding is the same condition, so the hypothesis, the
// proposal and the escalation are repeats once the anomaly is on record. Without
// this the filter would halve the storm instead of ending it: the deployment that
// motivated it emitted 34,672 anomalies and 29,571 escalations for one fault.
func TestEveryStageOfOneFindingSharesTheCondition(t *testing.T) {
	filter := agentruntime.NewLedgerFilter(0)
	if !filter.Admit(detected("task-1", "ANOMALY_UNVERIFIED_MUTATION", "execution", nil)) {
		t.Fatal("the anomaly opens the episode")
	}
	for _, topic := range []string{
		agentcontract.TopicOpsRootCauseHypothesis,
		agentcontract.TopicOpsRecoveryProposed,
		agentcontract.TopicOpsEscalationRequired,
	} {
		event := agentcontract.Event{
			Topic: topic, TaskID: "task-1", Agent: "ops",
			Payload: map[string]any{"hypothesisId": "hyp-ANOMALY_UNVERIFIED_MUTATION@execution"},
		}
		if filter.Admit(event) {
			t.Errorf("%s was recorded as a new episode", topic)
		}
	}
}

// Execution facts are one row per occurrence by construction. Filtering them would
// delete the record the ledger exists to hold.
func TestExecutionEventsAlwaysPassThrough(t *testing.T) {
	filter := agentruntime.NewLedgerFilter(0)
	for attempt := 0; attempt < 3; attempt++ {
		for _, topic := range []string{
			agentcontract.TopicActionExecuted, agentcontract.TopicStateTransition,
			agentcontract.TopicOpsRecoveryExecuted, agentcontract.TopicEvidenceCollected,
		} {
			if !filter.Admit(agentcontract.Event{Topic: topic, TaskID: "task-1"}) {
				t.Fatalf("%s was filtered; it is not a re-observation", topic)
			}
		}
	}
}

// A finding with no task has no ledger row to occupy — the projection drops it —
// and a filter that keyed on it would suppress the same finding for whichever task
// later made it attributable.
func TestAnUnattributableFindingIsLeftAlone(t *testing.T) {
	filter := agentruntime.NewLedgerFilter(0)
	event := detected("", "ANOMALY_TELEMETRY_STALE", "telemetry", nil)
	for attempt := 0; attempt < 3; attempt++ {
		if !filter.Admit(event) {
			t.Fatal("a finding with no task must not consume an episode")
		}
	}
}

// Bounded memory is the reason for the eviction, and a silent eviction would look
// like the storm starting again. The count makes it readable.
func TestTheFilterStaysBoundedAndSaysWhenItEvicted(t *testing.T) {
	filter := agentruntime.NewLedgerFilter(4)
	for index := 0; index < 10; index++ {
		filter.Admit(detected("task-1", "ANOMALY_COMPONENT_FAULT", string(rune('a'+index)), nil))
	}
	if filter.Open() > 4 {
		t.Fatalf("open = %d, want at most the limit", filter.Open())
	}
	if filter.Dropped() == 0 {
		t.Fatal("evictions happened and were not counted")
	}
}

// A closing edge only exists if the observer publishes one. The filter is useless
// without it: it would suppress a recurring condition forever, and a fault that
// cleared and came back would be missing from the record.
func TestTheObserverClosesAConditionItStopsSeeing(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	observer := agentruntime.NewOpsAgent()
	observer.Telemetry = quietTelemetry()
	observer.Now = func() time.Time { return now }
	recorder := &eventCollector{}
	observer.Publish = recorder.sink

	// One retryable failure, drained by the first evaluation. The second sees
	// nothing, which is what makes this the closing edge of an episode.
	failure := agentcontract.Event{
		Topic: agentcontract.TopicActionExecuted, TaskID: "task-close", StepID: "pick",
		Payload: agentcontract.ActionPayload{
			ToolName: "manipulation.pick", ActivityStatus: "FAILED", StepID: "pick",
			Error: "skill manipulation.pick failed: NAV_BRIDGE_UNAVAILABLE bridge down",
		}.Encode(),
	}
	if err := observer.OnEvent(context.Background(), failure); err != nil {
		t.Fatal(err)
	}
	if findings := observer.Observe(context.Background()); len(findings) == 0 {
		t.Fatal("the first evaluation observed nothing, so there is no episode to close")
	}
	now = now.Add(time.Minute)
	if findings := observer.Observe(context.Background()); len(findings) != 0 {
		t.Fatalf("the second evaluation still sees the failure: %+v", findings)
	}

	var closing *agentcontract.Event
	for _, event := range recorder.all() {
		if event.Topic == agentcontract.TopicOpsAnomalyCleared {
			candidate := event
			closing = &candidate
		}
	}
	if closing == nil {
		t.Fatalf("no closing edge was published; topics = %v", recorder.topics())
	}
	payload := closing.Payload
	if payload["identity"] == "" || payload["identity"] == nil {
		t.Fatalf("the closing edge does not name the condition: %v", payload)
	}
	if closing.TaskID != "task-close" {
		t.Fatalf("closing edge filed against %q", closing.TaskID)
	}
}
