package agentruntime_test

import (
	"context"
	"testing"
	"time"

	"github.com/SUSTechWLA/tangying-robot-agent-os/agentruntime"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/agentcontract"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/closedloop"
	"github.com/SUSTechWLA/tangying-robot-agent-os/core/telemetry"
	"github.com/SUSTechWLA/tangying-robot-agent-os/middleware"
)

// Durable execution store used by these tests: it answers with what is on disk
// and nothing else. It is deliberately not the running task service, because the
// question here is precisely what the observer can learn without being told.
type durableSteps struct {
	runs map[string][]middleware.StepRun
}

func (d durableSteps) StepStatus(_ context.Context, taskID, stepID string) (middleware.StepStatus, error) {
	for _, run := range d.runs[taskID] {
		if run.StepID == stepID {
			return run.Status, nil
		}
	}
	return middleware.StepPending, nil
}

func (d durableSteps) ListStepRuns(_ context.Context, taskID string) ([]middleware.StepRun, error) {
	return d.runs[taskID], nil
}

// quietTelemetry reports a healthy robot with a fresh observation, so the only
// possible finding is about execution records.
func quietTelemetry() func(context.Context, string) (telemetry.Snapshot, error) {
	return func(context.Context, string) (telemetry.Snapshot, error) {
		return telemetry.Snapshot{
			RobotID: "robot-local", Adapter: "local-runtime", SchemaVersion: "robot.v1",
			ObservedAt: time.Now().UTC(),
		}, nil
	}
}

func opsWithMemory(store durableSteps) *agentruntime.OpsAgent {
	observer := agentruntime.NewOpsAgent()
	observer.Telemetry = quietTelemetry()
	observer.Memory = agentruntime.NewMemory(store)
	return observer
}

// A physical step left STARTED is the case that must never be missed: the robot
// may have moved and nobody knows. While the process is running, the observer
// learns about the task from its events and reports it. This is the behaviour
// that already works, and it is pinned so a fix for the restart case cannot
// silently break it.
func TestObserverReportsAnUnconfirmedStepWhileRunning(t *testing.T) {
	store := durableSteps{runs: map[string][]middleware.StepRun{
		"task-live": {{
			StepRecord: middleware.StepRecord{
				TaskID: "task-live", StepID: "pick",
				Capability: "manipulation.pick", SafetyLevel: "physical_motion",
			},
			Status: middleware.StepStarted,
		}},
	}}
	observer := opsWithMemory(store)

	// The observer is told the task exists, the way the runtime tells it.
	if err := observer.OnEvent(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicStateTransition, TaskID: "task-live",
		Payload: agentcontract.StateTransitionPayload{To: "EXECUTING"}.Encode(),
	}); err != nil {
		t.Fatal(err)
	}

	findings := observer.Observe(context.Background())
	finding, ok := findByCode(findings, agentruntime.AnomalyUnverifiedMutation)
	if !ok {
		t.Fatalf("an unconfirmed physical step was not reported while running: %#v", findings)
	}
	if !finding.AutomaticRetryForbidden {
		t.Fatal("an unknown physical outcome must forbid automatic retry")
	}
}

// The restart case, fixed.
//
// The same unconfirmed step is on disk, but this process started after the
// failure and has seen no events for it. Reading the durable record on startup is
// what makes the supervisor useful across a restart, and a restart is exactly
// when an unconfirmed physical action is most dangerous: the robot may have been
// left mid-action and the process that dispatched it is gone.
func TestObserverFindsAnUnconfirmedStepAfterARestart(t *testing.T) {
	store := durableSteps{runs: map[string][]middleware.StepRun{
		"task-restarted": {{
			StepRecord: middleware.StepRecord{
				TaskID: "task-restarted", StepID: "pick",
				Capability: "manipulation.pick", SafetyLevel: "physical_motion",
			},
			Status: middleware.StepStarted,
		}},
	}}
	observer := opsWithMemory(store)
	observer.History = staticHistory{tasks: []string{"task-restarted"}}

	// No events at all: this is a fresh process looking at a task that failed
	// before it existed.
	findings := observer.Observe(context.Background())
	finding, ok := findByCode(findings, agentruntime.AnomalyUnverifiedMutation)
	if !ok {
		t.Fatalf("the supervisor is blind after a restart: %#v", codesOf(findings))
	}
	if !finding.AutomaticRetryForbidden {
		t.Fatal("an unconfirmed physical outcome must forbid automatic retry")
	}
	if len(finding.RecommendedActions) == 0 {
		t.Fatal("the finding must tell the operator what to do")
	}
}

// A failure that outlived its process is still a failure. The durable record of
// the step is FAILED, which the reconciliation rule deliberately does not pick up
// (a failed step is not an unknown one), so this depends on the startup sweep
// having made the task visible at all.
func TestObserverSeesAnAbnormallyEndedTaskAfterARestart(t *testing.T) {
	store := durableSteps{runs: map[string][]middleware.StepRun{
		"task-old": {{
			StepRecord: middleware.StepRecord{
				TaskID: "task-old", StepID: "navigate",
				Capability: "navigation.navigate", SafetyLevel: "physical_motion",
			},
			Status: middleware.StepFailed,
		}},
	}}
	observer := opsWithMemory(store)
	observer.History = staticHistory{tasks: []string{"task-old"}, abnormal: []string{"task-old"}}

	findings := observer.Observe(context.Background())
	// The sweep makes the task visible, and the task being worth a look is
	// itself reported rather than left for someone to notice.
	if _, ok := findByCode(findings, agentruntime.AnomalyAbnormalTask); !ok {
		t.Fatalf("a task that ended abnormally was not reported: %#v", codesOf(findings))
	}
}

// The sweep must not invent work. With no durable history configured the
// supervisor behaves exactly as before: it reports what it witnessed and nothing
// more, so a deployment that has not wired history keeps working.
func TestObserverWithoutHistoryStillReportsWhatItWitnessed(t *testing.T) {
	store := durableSteps{runs: map[string][]middleware.StepRun{
		"task-unknown": {{
			StepRecord: middleware.StepRecord{TaskID: "task-unknown", StepID: "pick"},
			Status:     middleware.StepStarted,
		}},
	}}
	observer := opsWithMemory(store)
	// No History installed.
	findings := observer.Observe(context.Background())
	if _, ok := findByCode(findings, agentruntime.AnomalyUnverifiedMutation); ok {
		t.Fatal("a step was reported without any way to know the task exists")
	}
}

// staticHistory is a durable task index for tests.
type staticHistory struct {
	tasks    []string
	abnormal []string
	// events is the durable ledger, per task. A failure that outlived its
	// process is only visible through this.
	events map[string][]agentcontract.TaskEvent
}

func (h staticHistory) TaskIDs(context.Context) ([]string, error)  { return h.tasks, nil }
func (h staticHistory) Abnormal(context.Context) ([]string, error) { return h.abnormal, nil }

func (h staticHistory) Events(_ context.Context, taskID string) ([]agentcontract.TaskEvent, error) {
	return h.events[taskID], nil
}

// A situation that gets worse must not be suppressed as a repeat.
//
// The report cooldown exists so a steady condition is not re-announced every
// tick. But "one task ended abnormally" and "five tasks ended abnormally" are not
// the same report, and the cooldown window is exactly when an operator most needs
// to hear that the number moved. This is the case the cooldown would otherwise
// swallow.
func TestWorseningSituationIsReportedAgain(t *testing.T) {
	now := time.Unix(1000, 0).UTC()
	evaluate := func(tasks []string) []agentruntime.Finding {
		return agentruntime.Evaluate(agentruntime.ObservationInput{
			Now:           now,
			AbnormalTasks: tasks,
			Snapshot:      &telemetry.Snapshot{RobotID: "robot-1", ObservedAt: now},
		})
	}

	one := evaluate([]string{"task-a"})
	if _, ok := findByCode(one, agentruntime.AnomalyAbnormalTask); !ok {
		t.Fatalf("one abnormal task was not reported: %#v", codesOf(one))
	}
	five := evaluate([]string{"task-a", "task-b", "task-c", "task-d", "task-e"})
	if _, ok := findByCode(five, agentruntime.AnomalyAbnormalTask); !ok {
		t.Fatalf("five abnormal tasks were not reported: %#v", codesOf(five))
	}

	// The count is what the identity uses to tell the two apart.
	observer := agentruntime.NewOpsAgent()
	observer.Now = func() time.Time { return now }
	var published []agentcontract.Event
	recorder := &eventCollector{}
	observer.Publish = recorder.sink
	published = nil

	for _, tasks := range [][]string{{"task-a"}, {"task-a", "task-b"}} {
		observer.History = staticHistory{tasks: tasks, abnormal: tasks}
		observer.Observe(context.Background())
	}
	ids := map[string]bool{}
	for _, event := range recorder.all() {
		if event.Topic != agentcontract.TopicOpsAnomalyDetected {
			continue
		}
		id, _ := event.Payload["anomalyId"].(string)
		ids[id] = true
	}
	if len(ids) < 2 {
		t.Fatalf("a worsening situation produced %d distinct anomaly identities, want 2: %#v",
			len(ids), ids)
	}
	_ = published
}

// A failure that outlived its process must be reported by what it was, not only
// as "a task ended abnormally".
//
// This gap was found on the live stack: a task failed with GRASP_NOT_REACHED
// before the supervisor asked, and the supervisor could say only that the task
// had ended badly. The action that broke is the part an operator needs, and it
// was unreachable because failures were accumulated from live events alone.
func TestObserverNamesTheActionThatFailedBeforeItStarted(t *testing.T) {
	observer := agentruntime.NewOpsAgent()
	observer.Telemetry = quietTelemetry()
	observer.History = staticHistory{
		tasks:    []string{"task-failed"},
		abnormal: []string{"task-failed"},
		events: map[string][]agentcontract.TaskEvent{
			"task-failed": {{
				Type: "action.executed",
				Payload: map[string]any{
					"agent": "task", "toolName": "manipulation.pick",
					"activityStatus": "FAILED", "stepId": "pick",
					"error": "skill manipulation.pick failed: GRASP_NOT_REACHED blue-bottle",
				},
			}},
		},
	}

	findings := observer.Observe(context.Background())
	finding, ok := findByCode(findings, agentruntime.AnomalyActionFailed)
	if !ok {
		t.Fatalf("the failed action was not named: %#v", codesOf(findings))
	}
	// The code, not the prose: it is what the classifier keys on.
	if finding.Facts["errorCode"] != "GRASP_NOT_REACHED" {
		t.Fatalf("error code = %#v, want GRASP_NOT_REACHED", finding.Facts["errorCode"])
	}
	if finding.Component != "manipulation.pick" {
		t.Fatalf("component = %q, want the tool that failed", finding.Component)
	}
	if len(finding.RecommendedActions) == 0 {
		t.Fatal("the finding must tell the operator what to do about the failed action")
	}
}

// The same failure counted twice — once live, once from the ledger on startup —
// is still one failure. Otherwise a restart would report every past failure as
// newly repeated.
func TestObserverDoesNotDoubleCountAFailureSeenLiveAndInTheLedger(t *testing.T) {
	payload := map[string]any{
		"agent": "task", "toolName": "manipulation.pick",
		"activityStatus": "FAILED", "stepId": "pick", "error": "GRASP_NOT_REACHED",
	}
	observer := agentruntime.NewOpsAgent()
	observer.Telemetry = quietTelemetry()

	// Live: the event arrives as it happens.
	if err := observer.OnEvent(context.Background(), agentcontract.Event{
		Topic: agentcontract.TopicActionExecuted, TaskID: "task-x", Payload: payload,
	}); err != nil {
		t.Fatal(err)
	}
	// Restart: the same failure is read back from the ledger.
	observer2 := agentruntime.NewOpsAgent()
	observer2.Telemetry = quietTelemetry()
	observer2.History = staticHistory{
		tasks: []string{"task-x"}, abnormal: []string{"task-x"},
		events: map[string][]agentcontract.TaskEvent{"task-x": {{Type: "action.executed", Payload: payload}}},
	}

	for name, agent := range map[string]*agentruntime.OpsAgent{"live": observer, "ledger": observer2} {
		findings := agent.Observe(context.Background())
		finding, ok := findByCode(findings, agentruntime.AnomalyActionFailed)
		if !ok {
			t.Fatalf("%s: no failure finding: %#v", name, codesOf(findings))
		}
		if finding.Facts["occurrences"] != 1 {
			t.Fatalf("%s: occurrences = %#v, want 1 for a single failure",
				name, finding.Facts["occurrences"])
		}
		// The classification must come from the failure code, not from how the
		// failure was learned about. Before the ledger path existed the two paths
		// could not even be compared, and they disagreed: a code the table did not
		// know fell through to UnknownOutcome, so a known, explainable grasp
		// failure was reported as an unrecoverable unknown.
		//
		// GRASP_NOT_REACHED is now in the table as Perception: the end effector
		// could not reach the object, so a new observation is what is needed.
		if finding.Category != string(closedloop.Perception) {
			t.Fatalf("%s: category = %q, want Perception for a failed approach",
				name, finding.Category)
		}
		if finding.AutomaticRetryForbidden {
			t.Fatalf("%s: a perception failure is retryable after re-observation; "+
				"forbidding retry here would be the false prohibition the missing table "+
				"entry used to produce", name)
		}
	}
}
